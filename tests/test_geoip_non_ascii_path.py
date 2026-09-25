"""The GeoIP database opens under a path that is not ASCII.

The known-bad input of [B227], measured on 2026-09-25: on Windows the C
extension of maxminddb, which its default mode picks, cannot open a non-ASCII
path. The cache lives under the user's profile, so a user called José or with a
CJK account name got FileNotFoundError on a database that exists, and behind a
proxy the browser did not start at all.

The real reader on a real (tiny) file, because the rest of the geo suite fakes
the library and so cannot see a defect that lives in it. On Linux the old code
passes too; the Windows jobs of the matrix are the ones that tell.
"""
from __future__ import annotations

import pytest

from invisible_core._geo import GeoTimezoneError, ip_to_coordinates, ip_to_timezone

from _tiny_mmdb import tiny_mmdb

NAME = "Jösé Müller 测试"


@pytest.fixture
def non_ascii_db(tmp_path):
    d = tmp_path / NAME / "geoip"
    d.mkdir(parents=True)
    f = d / "geoip-aio-all.mmdb"
    f.write_bytes(tiny_mmdb("Europe/Rome", 41.9, 12.5))
    return f


def test_the_timezone_is_read_from_a_database_under_a_non_ascii_path(non_ascii_db):
    assert ip_to_timezone("8.8.8.8", non_ascii_db) == "Europe/Rome"


def test_the_coordinates_are_read_from_the_same_database(non_ascii_db):
    assert ip_to_coordinates("8.8.8.8", non_ascii_db) == (41.9, 12.5)


def test_a_database_that_is_there_but_unreadable_is_said_as_such(tmp_path):
    """Not a bare OSError naming a missing file: that is what sent the reader
    of the old message looking for a download problem."""
    f = tmp_path / NAME / "broken.mmdb"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"this is not a MaxMind database")
    with pytest.raises(GeoTimezoneError) as e:
        ip_to_timezone("8.8.8.8", f)
    assert e.value.kind == "geoip_unreadable", e.value.kind
    assert "could not be read" in str(e.value)
