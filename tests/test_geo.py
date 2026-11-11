"""Unit tests for `invisible_core._geo` (timezone="auto" resolution).

Covers: the precedence policy (resolve_session_timezone), proxy→requests
translation, egress IP discovery (mocked HTTP), and IP→IANA mapping (mocked
mmdb). No real network or mmdb is touched.
"""
# MOVED FROM invisible_playwright/tests/ ON 2026-07-27.
#
# Every test in this file exercises code in THIS package and reached it through
# a four-line back-compat shim in the wrapper. That is not where coverage for a
# module belongs, and it was not academic: measured on 2026-07-27, six realistic
# one-line breaks in core code SURVIVED the core's own suite and were caught
# only by the wrapper's - `cloak_prefs()` returning {}, SOCKS detection always
# False, the scheme never stripped from a proxy server, `_proxy_is_set` always
# True, the locale always en-US, `get_default_args()` injecting -headless. The
# core's pre-push gate and its publish gate were both green over all six.
#
# `test_no_test_reaches_the_core_through_a_shim` in the wrapper keeps them here.
import re
import sys
import types

import pytest

from invisible_core import _geo
from invisible_core._geo import (
    GeoTimezoneError,
    _proxies_for_requests,
    _proxy_is_set,
    discover_egress_ip,
    ip_to_country,
    ip_to_coordinates,
    ip_to_timezone,
    prepare_session_geo,
    resolve_session_timezone,
)

SOCKS = {"server": "socks5://gw.example:1080", "username": "u", "password": "p"}
HTTP = {"server": "http://gw.example:8080", "username": "u", "password": "p"}


# ──────────────────────────────────────────────────────────────────────
#  _proxy_is_set
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize(
    "proxy,expected",
    [
        (None, False),
        ({}, False),
        ({"server": ""}, False),
        ({"server": "   "}, False),
        ({"server": "direct://"}, False),
        ({"server": "DIRECT://"}, False),
        ({"server": "socks5://h:1"}, True),
        ({"server": "http://h:8080"}, True),
    ],
)
def test_proxy_is_set(proxy, expected):
    assert _proxy_is_set(proxy) is expected


# ──────────────────────────────────────────────────────────────────────
#  _proxies_for_requests - scheme + credential translation
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_proxies_socks5_uses_socks5h_remote_dns():
    out = _proxies_for_requests(SOCKS)
    assert out["http"] == "socks5h://u:p@gw.example:1080"
    assert out["https"] == out["http"]


@pytest.mark.unit
def test_proxies_socks4_scheme():
    out = _proxies_for_requests({"server": "socks4://gw:1080"})
    assert out["http"] == "socks4://gw:1080"


@pytest.mark.unit
def test_proxies_http_and_https_schemes():
    assert _proxies_for_requests(HTTP)["http"] == "http://u:p@gw.example:8080"
    out = _proxies_for_requests({"server": "https://gw:8443"})
    assert out["https"] == "https://gw:8443"


@pytest.mark.unit
def test_proxies_no_scheme_defaults_to_http():
    out = _proxies_for_requests({"server": "gw.example:3128"})
    assert out["http"] == "http://gw.example:3128"


@pytest.mark.unit
def test_proxies_credentials_are_url_encoded():
    out = _proxies_for_requests(
        {"server": "socks5://gw:1080", "username": "user@x", "password": "p:w/d"}
    )
    # '@', ':' and '/' in creds must be percent-encoded so they don't break
    # the proxy URL parsing.
    assert "user%40x:p%3Aw%2Fd@gw:1080" in out["http"]


@pytest.mark.unit
def test_proxies_no_credentials_has_no_auth_prefix():
    out = _proxies_for_requests({"server": "socks5://gw:1080"})
    assert out["http"] == "socks5h://gw:1080"


# ──────────────────────────────────────────────────────────────────────
#  discover_egress_ip - mocked requests
# ──────────────────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")


@pytest.mark.unit
def test_discover_egress_ip_first_endpoint_wins(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _FakeResp("203.0.113.7\n")

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    assert discover_egress_ip(SOCKS) == "203.0.113.7"
    assert len(calls) == 1  # stopped at the first success


@pytest.mark.unit
def test_discover_egress_ip_falls_through_to_next_on_error(monkeypatch):
    seq = iter([_FakeResp("junk-not-an-ip"), _FakeResp("198.51.100.42")])

    def fake_get(url, **kw):
        return next(seq)

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    assert discover_egress_ip(HTTP) == "198.51.100.42"


@pytest.mark.unit
def test_discover_egress_ip_all_fail_raises(monkeypatch):
    def fake_get(url, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    with pytest.raises(GeoTimezoneError):
        discover_egress_ip(SOCKS)


@pytest.mark.unit
def test_discover_egress_ip_no_proxy_is_direct(monkeypatch):
    # proxy=None → direct request, requests.get must get proxies=None.
    seen = {}

    def fake_get(url, **kw):
        seen["proxies"] = kw.get("proxies", "MISSING")
        return _FakeResp("192.0.2.55")

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    assert discover_egress_ip(None) == "192.0.2.55"
    assert seen["proxies"] is None


# ──────────────────────────────────────────────────────────────────────
#  ip_to_timezone - mocked mmdb reader
# ──────────────────────────────────────────────────────────────────────
class _FakeReader:
    def __init__(self, record):
        self._record = record

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, ip):
        return self._record


def _install_fake_maxminddb(monkeypatch, record):
    mod = types.ModuleType("maxminddb")
    mod.open_database = lambda path, mode=None: _FakeReader(record)
    mod.MODE_MMAP = 2
    monkeypatch.setitem(sys.modules, "maxminddb", mod)


@pytest.mark.unit
def test_ip_to_timezone_reads_location_time_zone(monkeypatch):
    _install_fake_maxminddb(monkeypatch, {"location": {"time_zone": "Europe/Rome"}})
    assert ip_to_timezone("198.51.100.4", "x.mmdb") == "Europe/Rome"


@pytest.mark.unit
def test_ip_to_timezone_ip_absent_raises(monkeypatch):
    _install_fake_maxminddb(monkeypatch, None)
    with pytest.raises(GeoTimezoneError):
        ip_to_timezone("198.51.100.4", "x.mmdb")


@pytest.mark.unit
def test_ip_to_timezone_missing_zone_raises(monkeypatch):
    _install_fake_maxminddb(monkeypatch, {"location": {}})
    with pytest.raises(GeoTimezoneError):
        ip_to_timezone("198.51.100.4", "x.mmdb")


@pytest.mark.unit
def test_ip_to_timezone_invalid_iana_raises(monkeypatch):
    _install_fake_maxminddb(monkeypatch, {"location": {"time_zone": "Not/AZone"}})
    with pytest.raises(GeoTimezoneError):
        ip_to_timezone("198.51.100.4", "x.mmdb")


# ──────────────────────────────────────────────────────────────────────
#  ip_to_coordinates - the same record as the timezone, the same mutations
# ──────────────────────────────────────────────────────────────────────
#
# ⛔ This function arrived with NO test and NO consumer: the core declares the
# position, but on 2026-08-23 no module reads it and no pref carries it to the
# engine (entry 18 of `72-next-steps.md`). The tests below cover the piece that
# exists; the consumer is a different thing and has to be written separately.
@pytest.mark.unit
def test_ip_to_coordinates_reads_the_same_record_as_the_timezone(monkeypatch):
    _install_fake_maxminddb(
        monkeypatch,
        {"location": {"time_zone": "Europe/Rome", "latitude": 41.9, "longitude": 12.5}},
    )
    assert ip_to_coordinates("198.51.100.4", "x.mmdb") == (41.9, 12.5)


@pytest.mark.unit
def test_ip_to_coordinates_returns_floats_not_whatever_the_database_stored(monkeypatch):
    """Il database puo' portare interi: chi legge si aspetta due float."""
    _install_fake_maxminddb(monkeypatch, {"location": {"latitude": 41, "longitude": 12}})
    lat, lon = ip_to_coordinates("198.51.100.4", "x.mmdb")
    assert isinstance(lat, float) and isinstance(lon, float)
    assert (lat, lon) == (41.0, 12.0)


@pytest.mark.unit
def test_ip_to_coordinates_ip_absent_raises(monkeypatch):
    _install_fake_maxminddb(monkeypatch, None)
    with pytest.raises(GeoTimezoneError):
        ip_to_coordinates("198.51.100.4", "x.mmdb")


@pytest.mark.unit
@pytest.mark.parametrize(
    "location",
    [
        {},
        {"latitude": 41.9},                      # meta' record e' assenza
        {"longitude": 12.5},
        {"time_zone": "Europe/Rome"},            # il fuso c'e', la posizione no
    ],
)
def test_ip_to_coordinates_incomplete_record_raises(monkeypatch, location):
    """⛔ No fallback is invented: with no declaration it refuses (rule 7)."""
    _install_fake_maxminddb(monkeypatch, {"location": location})
    with pytest.raises(GeoTimezoneError):
        ip_to_coordinates("198.51.100.4", "x.mmdb")


@pytest.mark.unit
def test_a_session_without_coordinates_still_resolves_its_timezone(monkeypatch):
    """La differenza VOLUTA fra i due: il fuso e' fatale, la posizione no.

    A wrong timezone behind a proxy is the `tz_mismatch` trap; a missing
    position is just a browser nobody has asked where it is.
    """
    _install_fake_maxminddb(monkeypatch, {"location": {"time_zone": "Europe/Rome"}})
    geo = prepare_session_geo("Europe/Rome", None)
    assert geo.timezone == "Europe/Rome"
    assert geo.latitude is None and geo.longitude is None



# ──────────────────────────────────────────────────────────────────────
#  resolve_session_timezone - the precedence policy
# ──────────────────────────────────────────────────────────────────────
@pytest.fixture
def stub_egress(monkeypatch):
    """Make egress resolution deterministic + offline; record if it ran."""
    state = {"called": False}

    def fake_discover(proxy=None, **kw):
        state["called"] = True
        state["proxy_arg"] = proxy
        return "203.0.113.7"

    monkeypatch.setattr(_geo, "discover_egress_ip", fake_discover)
    monkeypatch.setattr(_geo, "ip_to_timezone", lambda ip, mmdb: "America/New_York")
    # ensure_geoip_mmdb is imported from .download at call time
    import invisible_core.download as dl

    monkeypatch.setattr(dl, "ensure_geoip_mmdb", lambda *a, **k: "fake.mmdb")
    return state


@pytest.mark.unit
def test_resolve_explicit_iana_wins(stub_egress):
    # An explicit zone wins and never triggers resolution (proxy or not).
    assert resolve_session_timezone("Asia/Tokyo", SOCKS) == "Asia/Tokyo"
    assert resolve_session_timezone("Asia/Tokyo", None) == "Asia/Tokyo"
    assert stub_egress["called"] is False


@pytest.mark.unit
def test_resolve_empty_with_proxy_resolves_from_proxy(stub_egress):
    assert resolve_session_timezone("", SOCKS) == "America/New_York"
    assert stub_egress["called"] is True
    assert stub_egress["proxy_arg"] == SOCKS  # routed through the proxy


@pytest.mark.unit
def test_resolve_auto_with_proxy_resolves_from_proxy(stub_egress):
    assert resolve_session_timezone("auto", HTTP) == "America/New_York"
    assert stub_egress["proxy_arg"] == HTTP


@pytest.mark.unit
def test_resolve_empty_no_proxy_resolves_from_host(stub_egress):
    # auto ALWAYS resolves - without a proxy, from the host's own public IP.
    assert resolve_session_timezone("", None) == "America/New_York"
    assert stub_egress["called"] is True
    assert stub_egress["proxy_arg"] is None  # direct request, no proxy


@pytest.mark.unit
def test_resolve_auto_no_proxy_resolves_from_host(stub_egress):
    assert resolve_session_timezone("auto", None) == "America/New_York"
    assert stub_egress["proxy_arg"] is None


@pytest.mark.unit
def test_resolve_direct_proxy_resolves_via_host(stub_egress):
    # direct:// counts as "no proxy" → resolve from the host IP, don't skip.
    assert resolve_session_timezone("auto", {"server": "direct://"}) == "America/New_York"
    assert stub_egress["proxy_arg"] is None


@pytest.mark.unit
def test_resolve_no_proxy_failure_raises_instead_of_using_the_host_zone(monkeypatch):
    # "auto" is the zone of the egress. Until 47.41.0 a failure without a proxy
    # returned "" (the host's zone) in silence, which on a server kept in UTC
    # contradicts its own address; it raises now, as behind a proxy.
    def boom(proxy=None, **kw):
        raise GeoTimezoneError("offline")

    monkeypatch.setattr(_geo, "discover_egress_ip", boom)
    with pytest.raises(GeoTimezoneError):
        resolve_session_timezone("auto", None)
    with pytest.raises(GeoTimezoneError):
        resolve_session_timezone("", None)


@pytest.mark.unit
def test_resolve_proxy_failure_raises(monkeypatch):
    # With a proxy set, a failure must raise - never a silent host-TZ fallback.
    def boom(proxy=None, **kw):
        raise GeoTimezoneError("no egress")

    monkeypatch.setattr(_geo, "discover_egress_ip", boom)
    with pytest.raises(GeoTimezoneError):
        resolve_session_timezone("auto", SOCKS)
    with pytest.raises(GeoTimezoneError):
        resolve_session_timezone("", SOCKS)


# ──────────────────────────────────────────────────────────────────────
#  prepare_session_geo - one round-trip for the timezone, the language and
#  the position: all three come from the egress IP.
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_prepare_geo_egress_present_behind_proxy(stub_egress):
    geo = prepare_session_geo("auto", SOCKS)
    assert geo.timezone == "America/New_York"
    assert geo.egress_ip == "203.0.113.7"  # discovered once
    assert stub_egress["proxy_arg"] == SOCKS


@pytest.mark.unit
def test_prepare_geo_egress_present_even_with_explicit_tz(stub_egress):
    # explicit IANA zone still needs the egress for the language and position.
    geo = prepare_session_geo("Asia/Tokyo", SOCKS)
    assert geo.timezone == "Asia/Tokyo"
    assert geo.egress_ip == "203.0.113.7"
    assert stub_egress["called"] is True


@pytest.mark.unit
def test_prepare_geo_carries_the_exit_without_proxy(stub_egress):
    """With no proxy the exit is the host's public address, and the fact is carried.

    The round trip was paid for the timezone: throwing its result away makes the
    language lookup pay it again.
    """
    geo = prepare_session_geo("auto", None)
    assert geo.timezone == "America/New_York"
    assert geo.egress_ip == "203.0.113.7"


@pytest.mark.unit
def test_prepare_geo_timezone_matches_resolve_session_timezone(stub_egress):
    # the thin tz wrapper must stay equivalent to prepare_session_geo().timezone
    for tz, proxy in [("Asia/Tokyo", SOCKS), ("auto", HTTP), ("", None)]:
        assert prepare_session_geo(tz, proxy).timezone == resolve_session_timezone(tz, proxy)


# ---------------------------------------------------------------------------
#  ip_to_country - the egress country, and only the country
#
#  Until 36.32.0 this function returned a language TAG from a country table,
#  and the table held tags no Firefox build has ("DE": "de-DE"). The language
#  is now a Firefox question answered in `_locale._COUNTRY_FIREFOX_BUILDS`
#  (tests in test_session_locale.py); this function only reads the DB.
# ---------------------------------------------------------------------------

def _country(monkeypatch, code):
    _install_fake_maxminddb(monkeypatch, {"country": {"iso_code": code}})


@pytest.mark.parametrize("cc", ["IT", "DE", "JP", "BR", "GB", "CA", "CH", "BE", "ZZ"])
@pytest.mark.unit
def test_ip_to_country_reads_the_egress_country(monkeypatch, cc):
    _country(monkeypatch, cc)
    assert ip_to_country("198.51.100.4", "x.mmdb") == cc


@pytest.mark.unit
def test_ip_to_country_is_case_insensitive_about_the_country_code(monkeypatch):
    """MaxMind returns upper case; nothing guarantees a future DB will."""
    _country(monkeypatch, "it")
    assert ip_to_country("198.51.100.4", "x.mmdb") == "IT"


@pytest.mark.parametrize("record", [
    {"country": {}},                        # a record with no code
    {},                                     # a record with no country
    None,                                   # an IP the DB does not know
])
@pytest.mark.unit
def test_ip_to_country_says_None_when_the_DB_cannot_tell(monkeypatch, record):
    _install_fake_maxminddb(monkeypatch, record)
    assert ip_to_country("198.51.100.4", "x.mmdb") is None


# ──────────────────────────────────────────────────────────────────────
#  The BUDGET, which no test watched and which indeed did not work
# ──────────────────────────────────────────────────────────────────────
def test_a_single_endpoint_cannot_eat_the_whole_budget(monkeypatch):
    """The timeout handed to requests has to be a PAIR that fits in the budget.

    `requests` applies a SCALAR timeout to the connect phase and then again to
    the read phase: `timeout=10` can cost twenty seconds in a single call. With
    `budget=15` the first endpoint blew the budget on its own and the loop exited
    at once, so ONE of the configured endpoints was ever tried.

    Measured 2026-08-10 against a proxy that had stopped routing: the error said
    `20.1s` with budget 15 and "1 of 3 endpoints". The function already
    documented the right intent - "the budget bounds the whole step" - and the
    code did something else, which is why this test looks at the value passed
    and not at the docstring.
    """
    from invisible_core import _geo

    seen = []

    def _get(url, **kw):
        seen.append(kw.get("timeout"))
        raise RuntimeError("this endpoint stays silent")

    monkeypatch.setattr(_geo.requests, "get", _get)

    with pytest.raises(_geo.GeoTimezoneError):
        _geo.discover_egress_ip(None, timeout=10.0, budget=15.0)

    assert seen, "no endpoint was tried at all"
    # 1. every call receives a pair, not a scalar
    for t in seen:
        assert isinstance(t, tuple), (
            f"scalar timeout {t!r}: requests applies it TWICE, so a single "
            "call can cost double the budget"
        )
        assert sum(t) <= 15.0 + 1e-6, (
            f"a single call can spend {sum(t)}s against a 15s budget"
        )
    # 2. and the redundancy is real: more than one endpoint gets tried
    assert len(seen) > 1, (
        f"tried {len(seen)} endpoint(s) of {len(_geo._IP_ECHO_ENDPOINTS)}: the "
        "budget is spent on the first and the others never come into play"
    )


def _count_probes(monkeypatch):
    """Makes discovery deterministic and COUNTS how many times it is called."""
    import invisible_core.download as dl
    from invisible_core import _geo

    count = {"n": 0}

    def fake(proxy=None, **kw):
        count["n"] += 1
        return "203.0.113.7"

    monkeypatch.setattr(_geo, "discover_egress_ip", fake)
    monkeypatch.setattr(_geo, "ip_to_timezone", lambda ip, mmdb: "America/New_York")
    monkeypatch.setattr(_geo, "ip_to_country", lambda ip, mmdb: "GB")
    monkeypatch.setattr(_geo, "ip_to_coordinates", lambda ip, mmdb: (1.0, 2.0))
    monkeypatch.setattr(dl, "ensure_geoip_mmdb", lambda *a, **k: "fake.mmdb")
    return count


@pytest.mark.unit
def test_without_a_proxy_the_address_is_discovered_ONCE_only(monkeypatch):
    """Un fatto, un giro di rete. Il percorso predefinito ne faceva DUE.

    ⛔ THIS IS RULE 16 APPLIED TO A NETWORK ROUND-TRIP. `prepare_session_geo`
    discovered the address to derive the timezone from it, then threw it away
    because the field that could have carried it meant something else;
    `resolve_session_locale` therefore had to rediscover it. Two identical
    requests to an external service, from the REAL address, before the browser
    even exists - and a real user makes zero of them, not two.

    The count has to be taken over BOTH steps together, because each one on its
    own was already correct: neither made one round-trip too many. The defect
    lived between them, and a test on a single function could not see it.
    """
    from invisible_core import _geo

    count = _count_probes(monkeypatch)
    # Since 36.x the language is decided INSIDE the geo decision, so the two
    # steps are one call - and the count still has to be one.
    geo = _geo.prepare_session_geo("auto", None)

    assert geo.locale.region == "GB", "the language must still resolve from the address"
    assert count["n"] == 1, (
        "the address was asked of the network %d times instead of once: the "
        "fact is still being thrown away between one step and the next"
        % count["n"])


@pytest.mark.unit
def test_behind_a_proxy_a_failed_discovery_does_NOT_fall_to_the_direct_address(monkeypatch):
    """The language is never derived from the home country while the timezone
    says another one.

    This is the risk in the other half of the fix: the "auto" language (since
    36.x `decide_session_locale`, before it `resolve_session_locale`) reuses
    what it receives instead of rediscovering it, and the shorter form -
    `ip = egress_ip or discover_egress_ip(None)` - would have dropped the case
    "proxy alive, discovery failed" onto the DIRECT address. The session would
    have declared the home country's language and the proxy country's timezone:
    a contradiction between two fields, which is worse than falling back to
    `en-US`.
    """
    from invisible_core import _geo

    count = _count_probes(monkeypatch)
    count["n"] = 0
    from invisible_core import decide_session_locale

    loc = decide_session_locale("auto", proxy={"server": "socks5://g:1"})

    assert loc.primary == "en-US", "behind a proxy with no address it falls back, it does not guess"
    assert count["n"] == 0, (
        "it went out on the DIRECT network to derive the language while a proxy "
        "was configured: that address is the one at home")


# ──────────────────────────────────────────────────────────────────────
#  A16 - the exit decides, with a proxy or without one (2026-10-17)
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_without_a_proxy_an_explicit_zone_still_gets_the_exits_position_and_language(monkeypatch):
    """The owner's rule of 2026-10-17 (28-valori-dell-host.md A16): the zone,
    the language and the position come from the EXIT, and without a proxy the
    exit is the host's public address. Until 49.46.0 an explicit zone without
    a proxy asked nothing, so the session had no position at all - the
    known-bad this test would have caught is that `latitude` stayed None and
    the count stayed 0."""
    count = _count_probes(monkeypatch)
    geo = _geo.prepare_session_geo("Asia/Tokyo", None, "auto")

    assert geo.timezone == "Asia/Tokyo", "an explicit zone still wins"
    assert (geo.latitude, geo.longitude) == (1.0, 2.0), "the position is the exit's"
    assert geo.locale.region == "GB", "the language is the exit's"
    assert count["n"] == 1, "one round trip for the three, not %d" % count["n"]


@pytest.mark.unit
def test_without_a_proxy_a_failed_discovery_does_not_fail_an_explicit_zone(monkeypatch):
    """An explicit zone does not need the address, so a dead network leaves
    the session without a position rather than without a launch - the same
    tolerance it always had behind a proxy - and "auto" still raises."""
    count = {"n": 0}

    def boom(proxy=None, **kw):
        count["n"] += 1
        raise GeoTimezoneError("offline")

    monkeypatch.setattr(_geo, "discover_egress_ip", boom)
    geo = _geo.prepare_session_geo("Asia/Tokyo", None, "en-US")
    assert geo.timezone == "Asia/Tokyo" and geo.egress_ip is None
    assert (geo.latitude, geo.longitude) == (None, None)
    assert count["n"] == 1, "a failure is not retried by the language decision"
    with pytest.raises(GeoTimezoneError):
        _geo.prepare_session_geo("auto", None, "en-US")


@pytest.mark.unit
def test_the_public_prefs_helper_declares_the_exits_zone_by_default(monkeypatch):
    """`get_default_stealth_prefs()` with nothing passed used to emit no zone
    at all, which is the host's: its default was "" and the docstring said
    "Empty means use the host TZ". It resolves "auto" from the exit now, the
    way a launch does (A16), and the zone it writes is that one."""
    from invisible_core.config import get_default_stealth_prefs

    count = _count_probes(monkeypatch)
    decided = _geo.prepare_session_geo("auto", None, "auto")
    count["n"] = 0
    prefs = get_default_stealth_prefs(seed=42)
    assert prefs["juggler.timezone.override"] == decided.timezone == "America/New_York"
    # The language the exit's decision gives (the country picks the builds,
    # the address one of them), not a tag this test guesses.
    assert prefs["intl.locale.requested"] == decided.locale.primary
    assert prefs["intl.accept_languages"] == decided.locale.accept_languages
    assert count["n"] == 1
