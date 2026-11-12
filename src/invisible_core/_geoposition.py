"""The position a session declares to the engine: the exit city's point, moved by the seed, with the accuracy of an IP-based Windows fix.

WHY. ``navigator.geolocation`` answers a page that has been granted the permission. On Windows the engine used to ask
the host's location platform (Wi-Fi, GPS, IP), so the answer was where the MACHINE under the browser is, in
contradiction with the time zone and the language the same core declares from the exit. The engine no longer asks the
host; the core declares the position, once, and sends it with the command that already exists
(``Browser.setGeolocationOverride {latitude, longitude, accuracy}``), at launch for the default context and for every
context the wrapper creates. Without the permission nothing changes: the engine answers only a page that was granted.

WHAT IS DECLARED. :func:`declared_position` is a pure function of the session seed and of the point the geoip record
of the exit gives (``_geo.ip_to_coordinates``, a city centroid):

* ``accuracy``: drawn per seed, log-uniformly in [1000, 10000] metres, whole metres.
* ``latitude`` / ``longitude``: the exit point plus an offset per seed, uniform over the DISC of half the accuracy
  as radius, with the geometry of the sphere (a metre of longitude is shorter by ``cos(latitude)``), latitude clamped
  to [-90, 90] and longitude wrapped to [-180, 180], rounded to 6 decimals (about 0.1 m).
* ``altitude``, ``altitudeAccuracy``, ``heading``, ``speed``: ``null``. The override command has no field for them
  (the engine turns the missing ones into ``null``), so there is nothing to declare, and an IP-based fix has none of
  them either.

A seed is a machine: two seeds do not give the same position, and the same seed gives the same one on every host. The
draw uses ``sub_seed(seed, "geoposition")``, an independent stream, so no other planner of the package moves. No trigonometry
and no ``log`` enters the draw (rejection sampling in the unit square, ``exp`` of a constant product, ``sqrt``: all
measured identical across the Python versions the package supports, see ``_floats.py``); ``cos`` appears once, for the
longitude scale, and its result is rounded to 6 decimals, so the bytes do not depend on the last bit of a libm.

NOTHING IS INVENTED. When the exit has no point (the egress discovery failed, or the record has no coordinates)
:func:`position_to_declare` returns ``None`` and the session sends no override: a browser nobody asked where it is.

SOURCE OF THE ACCURACY BAND. Microsoft Learn, ``Windows.Devices.Geolocation`` (``Geocoordinate.Accuracy`` and
``PositionSource.IPAddress``): a position whose source is the IP address of the device has an accuracy of roughly
300 to 10000 m, and above 1 km it is probably the resolution of the IP lookup. The lower edge is set at 1000 m
because the point here IS a city centroid of an IP database, never a Wi-Fi or a GPS fix.

CONFIDENCE LOW: re-measure with real Windows samples. The band, the log-uniform shape, the whole-metre rounding and
the half-radius offset are choices made from documentation, not from a population of real IP-only fixes
(``invisible_Geolocation`` keeps the capture layout for them).
"""
from __future__ import annotations

import math
import random
from typing import NamedTuple, Optional

from .seedmix import sub_seed

_TAG = "geoposition"

#: The band of an IP-based Windows fix, in metres (see the source above).
ACCURACY_MIN_M = 1000.0
ACCURACY_MAX_M = 10000.0

#: ln(10), written out so the draw calls no logarithm.
_LN10 = 2.302585092994046

#: Metres in one degree of latitude on a sphere of the mean Earth radius (6371008.8 m).
_METRES_PER_DEGREE = 111194.92664455873


class DeclaredPosition(NamedTuple):
    """What ``Browser.setGeolocationOverride`` carries, and nothing else."""

    latitude: float
    longitude: float
    accuracy: float

    def as_override(self) -> dict:
        """The ``geolocation`` object of the command: exactly these three keys."""
        return {"latitude": self.latitude, "longitude": self.longitude, "accuracy": self.accuracy}


def _unit_disc(rng: random.Random) -> "tuple[float, float]":
    """A point uniform over the unit disc, by rejection: only ``random()``, ``*`` and ``+``."""
    while True:
        x = rng.random() * 2.0 - 1.0
        y = rng.random() * 2.0 - 1.0
        if x * x + y * y <= 1.0:
            return x, y


def declared_position(seed: int, base_lat: float, base_lon: float) -> DeclaredPosition:
    """The position of the machine ``seed`` at the exit point (``base_lat``, ``base_lon``). Pure."""
    rng = random.Random(sub_seed(seed, _TAG))
    # Log-uniform in [ACCURACY_MIN_M, ACCURACY_MAX_M]: equal weight to each decade of the band.
    # The band spans exactly one decade, so the exponent runs over [0, ln 10].
    accuracy = float(round(ACCURACY_MIN_M * math.exp(rng.random() * _LN10)))
    accuracy = min(accuracy, ACCURACY_MAX_M)
    dx, dy = _unit_disc(rng)
    radius = accuracy / 2.0
    north_m, east_m = dy * radius, dx * radius
    lat = max(-90.0, min(90.0, base_lat + north_m / _METRES_PER_DEGREE))
    # A degree of longitude is shorter by cos(latitude); at the poles it is not a length at all.
    scale = max(math.cos(math.radians(base_lat)), 1e-6)
    lon = base_lon + east_m / (_METRES_PER_DEGREE * scale)
    lon = ((lon + 180.0) % 360.0) - 180.0
    return DeclaredPosition(round(lat, 6), round(lon, 6), accuracy)


def position_to_declare(seed: int, base_lat: Optional[float],
                        base_lon: Optional[float]) -> Optional[DeclaredPosition]:
    """The declaration of a session, or ``None`` when the exit has no point (nothing is invented)."""
    if base_lat is None or base_lon is None:
        return None
    return declared_position(seed, base_lat, base_lon)
