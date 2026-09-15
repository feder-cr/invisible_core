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
    ip_to_locale,
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
    mod.open_database = lambda path: _FakeReader(record)
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
def test_resolve_no_proxy_failure_falls_back_to_host(monkeypatch):
    # Without a proxy, a lookup failure must NOT break the launch → host TZ ("").
    def boom(proxy=None, **kw):
        raise GeoTimezoneError("offline")

    monkeypatch.setattr(_geo, "discover_egress_ip", boom)
    assert resolve_session_timezone("auto", None) == ""
    assert resolve_session_timezone("", None) == ""


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
#  prepare_session_geo - one round-trip for BOTH timezone + the WebRTC
#  egress IP. The egress feeds the srflx override (only behind a proxy).
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_prepare_geo_egress_present_behind_proxy(stub_egress):
    geo = prepare_session_geo("auto", SOCKS)
    assert geo.timezone == "America/New_York"
    assert geo.egress_ip == "203.0.113.7"  # discovered for WebRTC
    assert stub_egress["proxy_arg"] == SOCKS


@pytest.mark.unit
def test_prepare_geo_egress_present_even_with_explicit_tz(stub_egress):
    # explicit IANA zone still needs the egress for WebRTC behind a proxy.
    geo = prepare_session_geo("Asia/Tokyo", SOCKS)
    assert geo.timezone == "Asia/Tokyo"
    assert geo.egress_ip == "203.0.113.7"
    assert stub_egress["called"] is True


@pytest.mark.unit
def test_prepare_geo_no_webrtc_override_without_proxy(stub_egress):
    """With no proxy no srflx is declared - but the fact is carried anyway.

    ⛔ THIS TEST ASSERTED THE MECHANISM, NOT THE REQUIREMENT. The comment said
    "no WebRTC override (real STUN already tells the truth)", which is the
    requirement and still holds; the assertion was `geo.egress_ip is None`,
    which was only the WAY that requirement was obtained until 2026-08-26. That
    way cost a second discovery of the address, because the fact was thrown away
    in order to be able to say "do not declare".

    Now the requirement is asserted directly, and the fact is asserted as a
    fact. Two lines where there used to be one ambiguous one.
    """
    geo = prepare_session_geo("auto", None)
    assert geo.timezone == "America/New_York"
    assert geo.srflx_da_dichiarare() is None, (
        "with no proxy the engine must receive no srflx to declare: the real "
        "one is born with the right address and with its own allocation")
    assert geo.egress_ip == "203.0.113.7", (
        "il giro di rete e' stato fatto per il fuso: buttarne il risultato "
        "obbliga resolve_session_locale a rifarlo")


@pytest.mark.unit
def test_prepare_geo_timezone_matches_resolve_session_timezone(stub_egress):
    # the thin tz wrapper must stay equivalent to prepare_session_geo().timezone
    for tz, proxy in [("Asia/Tokyo", SOCKS), ("auto", HTTP), ("", None)]:
        assert prepare_session_geo(tz, proxy).timezone == resolve_session_timezone(tz, proxy)


# ---------------------------------------------------------------------------
#  ip_to_locale - the country table
#
#  ADDED 2026-07-27. It had no test anywhere: making `ip_to_locale` return
#  "en-US" unconditionally survived the core's whole suite AND every file moved
#  into it that day. It is the function that decides the browser's language, so
#  the failure it hides is a US-English browser behind a proxy egressing from
#  Milan - a mismatch a consistency check reads straight off the page.
# ---------------------------------------------------------------------------

def _country(monkeypatch, code):
    _install_fake_maxminddb(monkeypatch, {"country": {"iso_code": code}})


@pytest.mark.parametrize("cc,locale", [
    ("IT", "it-IT"), ("DE", "de-DE"), ("JP", "ja-JP"), ("BR", "pt-BR"),
    # Same language, different country: the table is not a language map, and
    # collapsing these would put en-US on a British egress.
    ("GB", "en-GB"), ("CA", "en-CA"),
    # Multi-language countries take the majority language, by design.
    ("CH", "de-CH"), ("BE", "fr-BE"),
])
@pytest.mark.unit
def test_ip_to_locale_follows_the_egress_country(monkeypatch, cc, locale):
    _country(monkeypatch, cc)
    assert ip_to_locale("198.51.100.4", "x.mmdb") == locale


@pytest.mark.unit
def test_ip_to_locale_is_case_insensitive_about_the_country_code(monkeypatch):
    """MaxMind returns upper case; nothing guarantees a future DB will."""
    _country(monkeypatch, "it")
    assert ip_to_locale("198.51.100.4", "x.mmdb") == "it-IT"


@pytest.mark.parametrize("record", [
    {"country": {"iso_code": "ZZ"}},        # a country we do not map
    {"country": {}},                        # a record with no code
    {},                                     # a record with no country
    None,                                   # an IP the DB does not know
])
@pytest.mark.unit
def test_ip_to_locale_falls_back_to_en_US(monkeypatch, record):
    """The fallback is correct, and it is also what an always-wrong
    implementation looks like - which is why it is asserted separately from the
    cases above rather than being the only thing asserted."""
    _install_fake_maxminddb(monkeypatch, record)
    assert ip_to_locale("198.51.100.4", "x.mmdb") == "en-US"


@pytest.mark.unit
def test_the_country_table_is_well_formed():
    """Every value a real BCP-47 tag whose region half is the key.

    `"PT": "pt-BR"` would be a plausible typo, invisible in every test above
    that does not name PT, and it would hand a Portuguese egress a Brazilian
    browser.
    """
    from invisible_core._geo import _COUNTRY_LOCALE

    for cc, tag in _COUNTRY_LOCALE.items():
        assert re.fullmatch(r"[a-z]{2}-[A-Z]{2}", tag), f"{cc}: {tag!r}"
        assert tag.split("-")[1] == cc, (
            f"{cc} maps to {tag}, whose region is {tag.split('-')[1]}")


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


# ---------------------------------------------------------------------------
# The srflx decision comes from the exit's CAPABILITIES, not from the scheme.
# ---------------------------------------------------------------------------

class _FakeCapability:
    """Replaces the network probe. No proxy, no socket, no waiting."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, proxy, **kw):
        self.calls.append(kw)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _decide(monkeypatch, answer, egress="203.0.113.7"):
    from invisible_core import _capability, _geo
    fake = _FakeCapability(answer)
    monkeypatch.setattr(_capability, "capability", fake)
    return _geo._srflx_soppresso({"server": "socks5://gw:1080"}, egress), fake


def test_the_fields_DEFAULT_is_the_cautious_one():
    """THE DEFECT THIS TEST EXISTS TO KEEP CLOSED.

    The first draft carried the ADDRESS to declare, defaulting to ``None``.
    `SessionGeo` is built positionally in six places across code and tests, and
    every one of them that did not know about the new field stopped declaring
    the srflx by accident: the default fell on the side leading to the message
    peggiore che un rilevatore possa scrivere. Invertito in un interruttore, il
    silenzio va chiesto.
    """
    from invisible_core._geo import SessionGeo
    g = SessionGeo("America/New_York", "198.51.100.4")
    assert g.srflx_soppresso is False, "il default deve DICHIARARE"
    assert g.srflx_da_dichiarare() == "198.51.100.4"


def test_with_coherent_udp_the_srflx_is_SUPPRESSED(monkeypatch):
    """The only case where staying silent beats declaring.

    The real srflx will be born with the right address already, because UDP
    leaves from where TCP leaves. Declaring one would add a candidate with no
    matching allocation, which is exactly the signal a detector with a TURN of
    its own knows how to read.
    """
    from invisible_core._geo import SessionGeo
    from invisible_core import _proxy
    # ⛔ TWO CONDITIONS ARE NEEDED, not one: that the exit carries coherent UDP
    # AND that the browser sends that UDP inside the proxy. This test exercised
    # one only, and for an hour the function checked one only.
    monkeypatch.setattr(_proxy, "UDP_GOES_THROUGH_SOCKS", True)
    soppresso, _ = _decide(monkeypatch, {"udp": True, "udp_matches_tcp": True})
    assert soppresso is True
    assert SessionGeo("tz", "203.0.113.7", None, None, True).srflx_da_dichiarare() is None


def test_with_INCOHERENT_udp_it_declares(monkeypatch):
    """UDP is there but leaves from another address: the real srflx would carry it."""
    soppresso, _ = _decide(monkeypatch, {"udp": True, "udp_matches_tcp": False})
    assert soppresso is False


def test_without_udp_it_declares(monkeypatch):
    soppresso, _ = _decide(monkeypatch, {"udp": False, "udp_matches_tcp": None})
    assert soppresso is False


def test_a_SILENT_probe_is_not_a_licence_to_stay_quiet(monkeypatch):
    """Missing fields are not a demonstration of coherence."""
    soppresso, _ = _decide(monkeypatch, {})
    assert soppresso is False


def test_a_probe_that_BLOWS_UP_cannot_fail_the_launch(monkeypatch):
    """Qualunque errore cade dal lato prudente, e il lancio prosegue."""
    soppresso, _ = _decide(monkeypatch, OSError("rete giu'"))
    assert soppresso is False


def test_the_exit_already_discovered_is_REUSED_not_remeasured(monkeypatch):
    """One fact, one round-trip. `prepare_session_geo` has already paid for it.

    ⛔ Routing has to be turned on, because with it off the probe is not called
    AT ALL - and rightly so: if the browser does not send UDP inside the proxy,
    knowing that the proxy would carry it changes no decision, and those 4-19
    seconds would be spent for nothing.
    """
    from invisible_core import _proxy
    monkeypatch.setattr(_proxy, "UDP_GOES_THROUGH_SOCKS", True)
    _, fake = _decide(monkeypatch, {"udp": False, "udp_matches_tcp": None})
    assert fake.calls, "la sonda non e' stata chiamata affatto"
    assert fake.calls[0].get("known_tcp_exit") == "203.0.113.7"


def test_without_a_proxy_the_probe_is_NOT_even_called(monkeypatch):
    """On a direct connection the real STUN already tells the truth.

    ⛔ AND THAT SENTENCE IS THE REQUIREMENT, so the answer is `True` - do not
    declare. Until 2026-08-26 this assertion said `False`, and the "do not
    declare" arrived anyway because `egress_ip` was `None` without a proxy.
    Docstring and assertion said opposite things about what to do with an
    address in hand, and it was the absence of the fact, not the rule, that
    decided.
    """
    from invisible_core import _capability, _geo
    fake = _FakeCapability({"udp": True, "udp_matches_tcp": True})
    monkeypatch.setattr(_capability, "capability", fake)
    assert _geo._srflx_soppresso(None, "203.0.113.7") is True
    assert fake.calls == [], "probing without a proxy is a wasted round-trip"


def test_the_question_is_answered_in_ONE_place_only():
    """I due costruttori di env chiamano il metodo, non ricalcolano la regola."""
    import inspect
    from invisible_core import launch
    src = inspect.getsource(launch.build_launch_env)
    assert "srflx_soppresso" not in src, (
        "build_launch_env is re-reading the switch: the rule would go back to "
        "being written in two places, which is how they diverge")


def test_stickiness_no_longer_enters_any_decision():
    """Owner decision 2026-08-25, plus the fact that the field was lying.

    ⛔ THE NAME THIS ASSERTS ON IS THE LIVE ONE, AND IT WAS NOT, FOR A WHILE.
    The helper was called `e_sticky` until the core was translated on
    2026-09-15; the assertion kept naming the old spelling, so it looked for a
    string that no longer exists anywhere and could never fail again. A rename
    disarmed a gate and nothing said so, because a test that cannot fail is
    green exactly like one that passes.
    """
    import inspect
    from invisible_core import _capability
    body = inspect.getsource(_capability.measure)
    body = body.split('"""')[2] if body.count('"""') >= 2 else body
    assert "is_sticky" not in body, (
        "stickiness is back inside measure(): it cost six network round-trips "
        "out of eight and answered 'yes' for an endpoint measured rotating 8 "
        "times in 25 minutes")


def test_coherent_udp_is_NOT_enough_if_the_browser_does_not_route_udp_through_the_proxy(monkeypatch):
    """⛔ THE DEFECT IN THE FIRST DRAFT, from 2026-08-25.

    That the EXIT carries coherent UDP is not enough: the BROWSER also has to be
    the one sending us the UDP. With `network.proxy.socks_remote_udp` off, UDP
    goes around the proxy, so the REAL srflx would be born with the home
    address, and to stop declaring would be a leak instead of a remedy.

    The branch was unreachable by LUCK - no provider has usable UDP - and not by
    construction. It is the shape of defect this project pays for: a condition
    whose safety rests on a fact it does not verify.
    """
    from invisible_core import _capability, _geo, _proxy

    monkeypatch.setattr(_capability, "capability",
                        lambda p, **k: {"udp": True, "udp_matches_tcp": True})

    monkeypatch.setattr(_proxy, "UDP_GOES_THROUGH_SOCKS", False)
    assert _geo._srflx_soppresso({"server": "socks5://g:1"}, "203.0.113.7") is False, (
        "with no UDP routing through the proxy it MUST keep declaring")

    monkeypatch.setattr(_proxy, "UDP_GOES_THROUGH_SOCKS", True)
    assert _geo._srflx_soppresso({"server": "socks5://g:1"}, "203.0.113.7") is True, (
        "con l'instradamento acceso E l'UDP coerente il ramo deve accendersi, "
        "altrimenti la costante non e' una condizione ma un interruttore morto")


def test_udp_routing_is_declared_in_one_place_only():
    """The fact must not be rewritten: it is read from the constant."""
    import inspect
    from invisible_core import _geo, _proxy

    assert _proxy.UDP_GOES_THROUGH_SOCKS is False, (
        "if one day it is turned on, it is turned on HERE and the pref "
        "network.proxy.socks_remote_udp is emitted in the same commit")
    # ⛔ THE CHECK GOES ON THE CODE, NOT ON THE COMMENT. The first draft of this
    # test looked for the string `socks_remote_udp` in the function's source and
    # found it in the COMMENT explaining why the constant exists. It is this
    # project's most repeated rule, applied in reverse.
    lines = [r.split("#")[0] for r in
             inspect.getsource(_geo._srflx_soppresso).splitlines()]
    source = chr(10).join(lines)
    assert "UDP_GOES_THROUGH_SOCKS" in source, (
        "the decision is not reading the constant: the fact would go back to "
        "being written in two places")
    assert "Preferences" not in source and "socks_remote_udp" not in source, (
        "_geo is reading the pref on its own instead of the constant")


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
    monkeypatch.setattr(_geo, "ip_to_locale", lambda ip, mmdb: "en-GB")
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
    geo = _geo.prepare_session_geo("auto", None)
    loc = _geo.resolve_session_locale(geo.egress_ip, None)

    assert loc == "en-GB", "the language must still resolve from the address"
    assert count["n"] == 1, (
        "the address was asked of the network %d times instead of once: the "
        "fact is still being thrown away between one step and the next"
        % count["n"])


@pytest.mark.unit
def test_without_a_proxy_the_engine_gets_neither_srflx_nor_ipv6_filter(monkeypatch):
    """⛔ THE REGRESSION THE OBVIOUS FIX WOULD HAVE CAUSED.

    Carrying the discovered address inside `egress_ip` and touching nothing else
    looks like the minimal fix for the double round-trip, and it would have been
    a double failure, because `egress_ip` drove TWO things:

    1. the declared srflx - we would have announced a synthetic candidate with
       the HOME address, that is, a candidate with no matching allocation, which
       is exactly the signal a detector reads as the front end being
       manipulated;
    2. the IPv6 filter - `build_launch_env` turns it on for `if webrtc_ip`, so it
       would have come back on with no proxy at all, undoing the measurement of
       2026-08-25 (retail 6 candidates, us 3, because we always filtered).

    Neither is visible from a test on `_geo` alone: the first goes through a
    method, the second through an environment builder in another module. That is
    why this test reaches all the way to the environment.
    """
    from invisible_core import _geo
    from invisible_core.launch import build_launch_env

    _count_probes(monkeypatch)
    geo = _geo.prepare_session_geo("auto", None)

    assert geo.egress_ip == "203.0.113.7", "the fact has to be in hand"
    env = build_launch_env({}, timezone=geo.timezone or None,
                           srflx_dichiarato=geo.srflx_da_dichiarare(),
                           base_env={})

    assert "STEALTHFOX_WEBRTC_PUBLIC_IP" not in env, (
        "with no proxy we would be declaring a synthetic srflx with the home "
        "address: a candidate with no matching allocation")
    assert "STEALTHFOX_WEBRTC_DISABLE_IPV6" not in env, (
        "with no proxy the IPv6 filter came back on: that is the regression of "
        "the 2026-08-25 measurement, retail 6 candidates against our 3")


@pytest.mark.unit
def test_behind_a_proxy_a_failed_discovery_does_NOT_fall_to_the_direct_address(monkeypatch):
    """The language is never derived from the home country while the timezone
    says another one.

    This is the risk in the other half of the fix: `resolve_session_locale` now
    reuses what it receives instead of rediscovering it, and the shorter form -
    `ip = egress_ip or discover_egress_ip(None)` - would have dropped the case
    "proxy alive, discovery failed" onto the DIRECT address. The session would
    have declared the home country's language and the proxy country's timezone:
    a contradiction between two fields, which is worse than falling back to
    `en-US`.
    """
    from invisible_core import _geo

    count = _count_probes(monkeypatch)
    count["n"] = 0
    loc = _geo.resolve_session_locale(None, {"server": "socks5://g:1"})

    assert loc == "en-US", "behind a proxy with no address it falls back, it does not guess"
    assert count["n"] == 0, (
        "it went out on the DIRECT network to derive the language while a proxy "
        "was configured: that address is the one at home")
