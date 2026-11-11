"""The position a session declares: the exit point moved by the seed, with the accuracy of an IP-based fix.

Written ahead of the code (red on the old tree, where ``invisible_core._geoposition`` does not exist and
``SessionGeo.latitude`` is the raw geoip point nobody reads). The coordinates here are made up (45.0 / 9.0 and a few
corners of the globe); no real place enters this file.
"""
from __future__ import annotations

import math

import pytest

from invisible_core import _geo
from invisible_core._geo import SessionGeo
from invisible_core._geoposition import (ACCURACY_MAX_M, ACCURACY_MIN_M, DeclaredPosition, declared_position,
                                         position_to_declare)

BASE = (45.0, 9.0)
M_PER_DEG = 111_194.93


def _metres_between(a, b):
    """Small-distance planar distance on the sphere, enough for a 5 km disc."""
    dlat = (a[0] - b[0]) * M_PER_DEG
    dlon = (a[1] - b[1]) * M_PER_DEG * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


@pytest.mark.unit
def test_the_same_seed_gives_the_same_position_and_two_seeds_do_not():
    assert declared_position(42, *BASE) == declared_position(42, *BASE)
    positions = {declared_position(s, *BASE) for s in range(200)}
    assert len(positions) == 200, "seeds must not be linkable by a shared position"


@pytest.mark.unit
def test_the_accuracy_is_log_uniform_in_the_band_of_an_ip_fix():
    accuracies = [declared_position(s, *BASE).accuracy for s in range(4000)]
    assert min(accuracies) >= ACCURACY_MIN_M and max(accuracies) <= ACCURACY_MAX_M
    assert all(a == round(a) for a in accuracies), "whole metres"
    # Log-uniform: the median is the geometric mean of the band, not its midpoint.
    below = sum(1 for a in accuracies if a < math.sqrt(ACCURACY_MIN_M * ACCURACY_MAX_M))
    assert 0.45 < below / len(accuracies) < 0.55, below
    # A linear-uniform draw would put 50% below 5500; a log-uniform one 74%.
    assert sum(1 for a in accuracies if a < 5500) / len(accuracies) > 0.68


@pytest.mark.unit
def test_the_offset_stays_inside_half_of_the_accuracy_and_fills_the_disc():
    distances = []
    for s in range(4000):
        p = declared_position(s, *BASE)
        d = _metres_between((p.latitude, p.longitude), BASE)
        assert d <= p.accuracy / 2 + 1.0, (s, d, p.accuracy)  # +1 m: the 6-decimal rounding
        distances.append(d / (p.accuracy / 2))
    # Uniform over the DISC: the share inside half the radius is 1/4 (a uniform radius would give 1/2).
    inside = sum(1 for x in distances if x <= 0.5) / len(distances)
    assert 0.21 < inside < 0.29, inside
    assert max(distances) > 0.95, "the disc is used up to its rim"


@pytest.mark.unit
def test_east_west_is_scaled_by_the_latitude():
    """A metre of longitude is longer in degrees at 60 north than at the equator."""
    spread = {}
    for lat in (0.0, 60.0):
        spread[lat] = max(abs(declared_position(s, lat, 9.0).longitude - 9.0) for s in range(500))
    assert 1.8 < spread[60.0] / spread[0.0] < 2.2, spread


@pytest.mark.unit
@pytest.mark.parametrize("base", [(89.999, 9.0), (-89.999, 9.0), (0.0, 179.999), (0.0, -179.999), (90.0, 0.0), (-90.0, 180.0)])
def test_the_result_is_always_a_valid_coordinate(base):
    for s in range(300):
        p = declared_position(s, *base)
        assert -90.0 <= p.latitude <= 90.0, p
        assert -180.0 <= p.longitude <= 180.0, p
        assert math.isfinite(p.latitude) and math.isfinite(p.longitude)


@pytest.mark.unit
def test_across_the_antimeridian_the_longitude_wraps():
    seen_east = seen_west = False
    for s in range(300):
        lon = declared_position(s, 0.0, 179.999).longitude
        seen_east |= lon > 179.0
        seen_west |= lon < -179.0
    assert seen_east and seen_west


@pytest.mark.unit
def test_nothing_is_invented_without_a_point():
    assert position_to_declare(42, None, None) is None
    assert position_to_declare(42, 45.0, None) is None
    assert position_to_declare(42, None, 9.0) is None
    assert position_to_declare(42, *BASE) == declared_position(42, *BASE)


@pytest.mark.unit
def test_the_command_params_are_exactly_latitude_longitude_accuracy():
    """The override has no field for altitude, altitudeAccuracy, heading or speed: those stay null in the page."""
    p = declared_position(42, *BASE)
    assert isinstance(p, DeclaredPosition)
    assert set(p.as_override()) == {"latitude", "longitude", "accuracy"}
    geo = SessionGeo("Europe/Rome", "203.0.113.7", p.latitude, p.longitude, None, p.accuracy)
    assert geo.geolocation_override() == p.as_override()
    assert set(geo.geolocation_override()) == {"latitude", "longitude", "accuracy"}


@pytest.mark.unit
def test_a_pinned_value_so_a_change_of_the_draw_is_a_decision():
    """Written out once: the draw is shipped, a change moves every seed's position."""
    assert declared_position(42, *BASE) == DeclaredPosition(PINNED_LAT, PINNED_LON, PINNED_ACC)


PINNED_LAT, PINNED_LON, PINNED_ACC = 44.991972, 9.005186, 2866.0


# -- prepare_session_geo: the declaration is built where the SessionGeo is ---------------------------------------

@pytest.fixture
def exit_at_base(monkeypatch):
    import invisible_core.download as dl
    monkeypatch.setattr(_geo, "discover_egress_ip", lambda proxy=None, **kw: "203.0.113.7")
    monkeypatch.setattr(_geo, "ip_to_timezone", lambda ip, mmdb: "Europe/Rome")
    monkeypatch.setattr(_geo, "ip_to_country", lambda ip, mmdb: "IT")
    monkeypatch.setattr(_geo, "ip_to_coordinates", lambda ip, mmdb: BASE)
    monkeypatch.setattr(dl, "ensure_geoip_mmdb", lambda *a, **k: "fake.mmdb")


@pytest.mark.unit
@pytest.mark.parametrize("timezone", ["auto", "Europe/Rome"])
def test_the_session_geo_carries_the_declared_position(exit_at_base, timezone):
    geo = _geo.prepare_session_geo(timezone, None, "it-IT", seed=42)
    want = declared_position(42, *BASE)
    assert (geo.latitude, geo.longitude, geo.accuracy) == tuple(want)
    assert geo.geolocation_override() == want.as_override()


@pytest.mark.unit
def test_two_seeds_on_the_same_exit_get_different_positions(exit_at_base):
    a = _geo.prepare_session_geo("auto", None, "it-IT", seed=1).geolocation_override()
    b = _geo.prepare_session_geo("auto", None, "it-IT", seed=2).geolocation_override()
    assert a != b


@pytest.mark.unit
def test_without_a_seed_nothing_is_declared(exit_at_base):
    geo = _geo.prepare_session_geo("auto", None, "it-IT")
    assert geo.geolocation_override() is None


@pytest.mark.unit
def test_when_the_coordinates_are_unavailable_nothing_is_declared(exit_at_base, monkeypatch):
    def refuse(ip, mmdb):
        raise _geo.GeoTimezoneError("no coordinates", kind="no_coordinates_for_ip")
    monkeypatch.setattr(_geo, "ip_to_coordinates", refuse)
    geo = _geo.prepare_session_geo("auto", None, "it-IT", seed=42)
    assert geo.timezone == "Europe/Rome", "the zone is not touched by the gap"
    assert (geo.latitude, geo.longitude, geo.accuracy) == (None, None, None)
    assert geo.geolocation_override() is None


@pytest.mark.unit
def test_the_launch_plan_carries_the_override_of_its_seed(monkeypatch, tmp_path):
    from invisible_core import build_launch_plan
    monkeypatch.delenv("STEALTHFOX_WEBRTC_NETWORK", raising=False)
    seen = {}

    def fake_geo(tz, proxy, locale="auto", *, seed=None, udp_stun_servers=None):
        seen["seed"] = seed
        p = declared_position(seed, *BASE)
        return SessionGeo("Europe/Rome", "203.0.113.7", p.latitude, p.longitude, None, p.accuracy)

    monkeypatch.setattr(_geo, "prepare_session_geo", fake_geo)
    plan = build_launch_plan(42, profile_dir=tmp_path, binary_path="firefox", timezone="Europe/Rome")
    assert seen["seed"] == 42, "the plan must hand its seed to the geo step"
    assert plan.geolocation == declared_position(42, *BASE).as_override()


@pytest.mark.unit
def test_the_public_config_path_hands_its_seed_to_the_geo_step(monkeypatch):
    """``get_default_stealth_prefs`` (config.py) resolves a seed and the geo step must receive it."""
    from invisible_core import config
    seen = {}

    def fake_geo(tz, proxy, locale="auto", *, seed=None, udp_stun_servers=None):
        seen["seed"] = seed
        return SessionGeo("Europe/Rome", "203.0.113.7")

    monkeypatch.setattr(config, "prepare_session_geo", fake_geo)
    config.get_default_stealth_prefs(seed=7, timezone="Europe/Rome", locale="it-IT")
    assert seen["seed"] == 7
