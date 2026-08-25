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
#  ip_to_coordinates - stesso record del fuso, stesse mutazioni
# ──────────────────────────────────────────────────────────────────────
#
# ⛔ Questa funzione e' arrivata SENZA test e SENZA consumatore: il core
# dichiara la posizione, ma il 2026-08-23 nessun modulo la legge e nessuna pref
# la porta al motore (voce 18 di `72-next-steps.md`). I test qui sotto coprono
# il pezzo che esiste; il consumatore e' un'altra cosa e va scritto a parte.
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
    """⛔ Non si inventa un ripiego: senza dichiarazione si rifiuta (regola 7)."""
    _install_fake_maxminddb(monkeypatch, {"location": location})
    with pytest.raises(GeoTimezoneError):
        ip_to_coordinates("198.51.100.4", "x.mmdb")


@pytest.mark.unit
def test_a_session_without_coordinates_still_resolves_its_timezone(monkeypatch):
    """La differenza VOLUTA fra i due: il fuso e' fatale, la posizione no.

    Un fuso sbagliato dietro un proxy e' la trappola `tz_mismatch`; una
    posizione assente e' solo un browser a cui nessuno ha chiesto dove sia.
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
def test_prepare_geo_no_egress_without_proxy(stub_egress):
    # no proxy → no WebRTC override (real STUN already tells the truth).
    geo = prepare_session_geo("auto", None)
    assert geo.timezone == "America/New_York"
    assert geo.egress_ip is None


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
#  Il BUDGET, che nessun test guardava e che infatti non funzionava
# ──────────────────────────────────────────────────────────────────────
def test_a_single_endpoint_cannot_eat_the_whole_budget(monkeypatch):
    """Il timeout passato a requests deve essere una COPPIA che sta nel budget.

    `requests` applica un timeout SCALARE alla fase di connessione e poi di
    nuovo a quella di lettura: `timeout=10` puo' costare venti secondi in una
    chiamata sola. Con `budget=15` il primo endpoint sfondava da solo il budget
    e il ciclo usciva subito, quindi degli endpoint configurati ne veniva
    provato UNO.

    Misurato il 2026-08-10 su un proxy che aveva smesso di instradare: l'errore
    diceva `20.1s` con budget 15 e "1 of 3 endpoints". La funzione documentava
    gia' l'intento giusto - "il budget limita il passo intero" - e il codice
    faceva un'altra cosa, che e' la ragione per cui questo test guarda il valore
    passato e non la docstring.
    """
    from invisible_core import _geo

    visti = []

    def _get(url, **kw):
        visti.append(kw.get("timeout"))
        raise RuntimeError("questo endpoint tace")

    monkeypatch.setattr(_geo.requests, "get", _get)

    with pytest.raises(_geo.GeoTimezoneError):
        _geo.discover_egress_ip(None, timeout=10.0, budget=15.0)

    assert visti, "nessun endpoint e' stato provato"
    # 1. ogni chiamata riceve una coppia, non uno scalare
    for t in visti:
        assert isinstance(t, tuple), (
            f"timeout scalare {t!r}: requests lo applica DUE volte, "
            "quindi una sola chiamata puo' costare il doppio del budget"
        )
        assert sum(t) <= 15.0 + 1e-6, (
            f"una singola chiamata puo' spendere {sum(t)}s con un budget di 15s"
        )
    # 2. e la ridondanza serve davvero: piu' di un endpoint viene provato
    assert len(visti) > 1, (
        f"provato {len(visti)} endpoint su {len(_geo._IP_ECHO_ENDPOINTS)}: "
        "il budget si esaurisce sul primo e gli altri non entrano mai in gioco"
    )


# ---------------------------------------------------------------------------
# La decisione sul srflx viene dalle CAPACITA' dell'uscita, non dallo schema.
# ---------------------------------------------------------------------------

class _CapacitaFinte:
    """Sostituisce la sonda di rete. Nessun proxy, nessun socket, nessuna attesa."""

    def __init__(self, risposta):
        self.risposta = risposta
        self.chiamate = []

    def __call__(self, proxy, **kw):
        self.chiamate.append(kw)
        if isinstance(self.risposta, Exception):
            raise self.risposta
        return self.risposta


def _decidi(monkeypatch, risposta, egress="203.0.113.7"):
    from invisible_core import _capacita, _geo
    finta = _CapacitaFinte(risposta)
    monkeypatch.setattr(_capacita, "capacita", finta)
    return _geo._srflx_soppresso({"server": "socks5://gw:1080"}, egress), finta


def test_il_DEFAULT_del_campo_e_quello_prudente():
    """IL DIFETTO CHE QUESTO TEST ESISTE PER TENERE CHIUSO.

    La prima stesura portava l'INDIRIZZO da dichiarare, con default ``None``.
    `SessionGeo` si costruisce per posizione in sei punti fra codice e test, e
    tutti quelli che non conoscevano il campo nuovo hanno smesso di dichiarare
    il srflx per distrazione: il default cadeva dal lato che porta al messaggio
    peggiore che un rilevatore possa scrivere. Invertito in un interruttore, il
    silenzio va chiesto.
    """
    from invisible_core._geo import SessionGeo
    g = SessionGeo("America/New_York", "198.51.100.4")
    assert g.srflx_soppresso is False, "il default deve DICHIARARE"
    assert g.srflx_da_dichiarare() == "198.51.100.4"


def test_con_udp_coerente_il_srflx_si_SOPPRIME(monkeypatch):
    """L'unico caso in cui tacere e' meglio che dichiarare.

    Il srflx vero nascera' gia' con l'indirizzo giusto, perche' l'UDP esce da
    dove esce il TCP. Dichiararne uno aggiungerebbe un candidato senza
    allocazione corrispondente, che e' esattamente il segnale che un rilevatore
    con un TURN proprio sa leggere.
    """
    from invisible_core._geo import SessionGeo
    soppresso, _ = _decidi(monkeypatch, {"udp": True, "udp_coerente": True})
    assert soppresso is True
    assert SessionGeo("tz", "203.0.113.7", None, None, True).srflx_da_dichiarare() is None


def test_con_udp_INCOERENTE_si_dichiara(monkeypatch):
    """UDP c'e' ma esce da un altro indirizzo: il srflx vero porterebbe quello."""
    soppresso, _ = _decidi(monkeypatch, {"udp": True, "udp_coerente": False})
    assert soppresso is False


def test_senza_udp_si_dichiara(monkeypatch):
    soppresso, _ = _decidi(monkeypatch, {"udp": False, "udp_coerente": None})
    assert soppresso is False


def test_una_sonda_MUTA_non_e_una_licenza_a_tacere(monkeypatch):
    """Campi assenti non sono una dimostrazione di coerenza."""
    soppresso, _ = _decidi(monkeypatch, {})
    assert soppresso is False


def test_una_sonda_che_ESPLODE_non_puo_far_fallire_il_lancio(monkeypatch):
    """Qualunque errore cade dal lato prudente, e il lancio prosegue."""
    soppresso, _ = _decidi(monkeypatch, OSError("rete giu'"))
    assert soppresso is False


def test_l_uscita_gia_scoperta_viene_RIUSATA_non_rimisurata(monkeypatch):
    """Un fatto, un giro. `prepare_session_geo` ha gia' pagato quel round-trip."""
    _, finta = _decidi(monkeypatch, {"udp": False, "udp_coerente": None})
    assert finta.chiamate, "la sonda non e' stata chiamata affatto"
    assert finta.chiamate[0].get("uscita_tcp_nota") == "203.0.113.7"


def test_senza_proxy_la_sonda_NON_viene_nemmeno_chiamata(monkeypatch):
    """Su una connessione diretta lo STUN vero dice gia' la verita'."""
    from invisible_core import _capacita, _geo
    finta = _CapacitaFinte({"udp": True, "udp_coerente": True})
    monkeypatch.setattr(_capacita, "capacita", finta)
    assert _geo._srflx_soppresso(None, "203.0.113.7") is False
    assert finta.chiamate == [], "sondare senza proxy e' un giro di rete sprecato"


def test_la_domanda_riceve_risposta_in_UN_SOLO_posto():
    """I due costruttori di env chiamano il metodo, non ricalcolano la regola."""
    import inspect
    from invisible_core import launch
    src = inspect.getsource(launch.build_launch_env)
    assert "srflx_soppresso" not in src, (
        "build_launch_env sta rileggendo l'interruttore: la regola tornerebbe a "
        "essere scritta in due posti, che e' come divergono")


def test_la_stickiness_non_entra_piu_in_nessuna_decisione():
    """Decisione del proprietario 2026-08-25, piu' il fatto che quel campo mentiva."""
    import inspect
    from invisible_core import _capacita
    corpo = inspect.getsource(_capacita.misura)
    corpo = corpo.split('"""')[2] if corpo.count('"""') >= 2 else corpo
    assert "e_sticky" not in corpo, (
        "la stickiness e' tornata dentro misura(): costava sei giri di rete su "
        "otto e diceva 'si' per un endpoint misurato ruotare 8 volte in 25 minuti")
