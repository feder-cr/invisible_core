"""Unit tests for `invisible_core._proxy.configure_proxy`.

Decision-table coverage of every input partition: None/empty/direct,
SOCKS4/5/default, HTTP/HTTPS, case variants, malformed, mutation contract.
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
import pytest

from invisible_core._proxy import configure_proxy, parse_proxy


# ──────────────────────────────────────────────────────────────────────
#  CP1-CP7: no-op cases - return None, do NOT mutate prefs
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp1_none_proxy_returns_none():
    prefs = {}
    assert configure_proxy(None, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp2_empty_dict_returns_none():
    prefs = {}
    assert configure_proxy({}, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp3_empty_server_returns_none():
    prefs = {}
    assert configure_proxy({"server": ""}, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp4_whitespace_server_returns_none():
    prefs = {}
    assert configure_proxy({"server": "  "}, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp5_direct_scheme_returns_none():
    prefs = {}
    assert configure_proxy({"server": "direct://"}, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp6_direct_scheme_uppercase_returns_none():
    prefs = {}
    assert configure_proxy({"server": "DIRECT://"}, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp7_direct_scheme_mixed_case_returns_none():
    prefs = {}
    assert configure_proxy({"server": "DiReCt://"}, prefs) is None
    assert prefs == {}


# ──────────────────────────────────────────────────────────────────────
#  CP8-CP9: HTTP/HTTPS - passthrough (return proxy unchanged, no mutation)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp8_http_proxy_passthrough():
    prefs = {}
    proxy = {"server": "http://proxy:8080"}
    result = configure_proxy(proxy, prefs)
    assert result == proxy
    # No SOCKS-related mutations.
    assert "network.proxy.type" not in prefs
    assert "network.proxy.socks" not in prefs


@pytest.mark.unit
def test_cp9_https_proxy_passthrough():
    prefs = {}
    proxy = {"server": "https://proxy:8080"}
    result = configure_proxy(proxy, prefs)
    assert result == proxy
    assert "network.proxy.type" not in prefs


@pytest.mark.unit
def test_cp8b_http_with_username_password_passthrough():
    """HTTP proxies preserve username/password for Playwright to consume."""
    prefs = {}
    proxy = {"server": "http://proxy:8080", "username": "user", "password": "pw"}
    result = configure_proxy(proxy, prefs)
    assert result == proxy
    assert "network.proxy.type" not in prefs


# ──────────────────────────────────────────────────────────────────────
#  Una strada sola: `parse_proxy` legge, il comando del motore instrada
#
#  I test che stavano qui asserivano il contrario - che uno schema
#  scrivesse `network.proxy.*` e un altro tornasse un dict - ed erano
#  corretti finche' le strade erano tre. Sono stati cancellati con le
#  strade, non riadattati: un test che descrive un meccanismo che non
#  esiste piu' e' peggio di nessun test, perche' passa.
#
#  Quello che quei test proteggevano davvero sopravvive qui: nessun
#  endpoint viene lasciato cadere in silenzio, e nessuno schema e'
#  trattato diversamente da un altro.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("server,atteso", [
    ("socks5://host:1080", ("socks", "host", 1080)),
    ("SOCKS5://HOST:1080", ("socks", "HOST", 1080)),   # lo schema e' insensibile
    ("socks4://host:1080", ("socks4", "host", 1080)),
    ("socks://host:1080",  ("socks", "host", 1080)),
    ("http://host:8080",   ("http", "host", 8080)),
    ("https://host:3128",  ("https", "host", 3128)),
    ("host:9999",          ("http", "host", 9999)),    # senza schema: http
    ("socks5://host",      ("socks", "host", 1080)),   # senza porta: il default
    ("http://host",        ("http", "host", 80)),
    ("  socks5://host:1080  ", ("socks", "host", 1080)),
])
def test_ogni_schema_e_letto_dallo_STESSO_lettore(server, atteso):
    """Quattro tipi, un lettore. Era questa la differenza che ha fatto danno."""
    ep = parse_proxy({"server": server})
    assert (ep.type, ep.host, ep.port) == atteso


@pytest.mark.unit
def test_un_ipv6_fra_parentesi_resta_intero():
    """Le parentesi le toglie il lettore; i due punti dentro non sono la porta."""
    ep = parse_proxy({"server": "socks5://[2001:db8::1]:1080"})
    assert ep.host == "2001:db8::1" and ep.port == 1080


@pytest.mark.unit
def test_le_credenziali_arrivano_al_comando_su_OGNI_schema():
    """Il ramo HTTP le RIFIUTAVA, perche' le prefs non sanno portarle.

    Il comando del motore si', e la stessa riga ora vale per tutti e quattro:
    la limitazione era della strada, non del protocollo.
    """
    for server in ("http://h:1", "https://h:1", "socks5://h:1", "socks4://h:1"):
        cmd = parse_proxy({"server": server, "username": "u",
                           "password": "p"}).as_engine_command()
        assert cmd["username"] == "u" and cmd["password"] == "p", server


@pytest.mark.unit
def test_una_credenziale_vuota_non_viene_spedita():
    """Il motore le dichiara Optional: "" e' un valore, non un'assenza."""
    cmd = parse_proxy({"server": "http://h:1", "username": "",
                       "password": None}).as_engine_command()
    assert "username" not in cmd and "password" not in cmd


@pytest.mark.unit
@pytest.mark.parametrize("server", [
    "ftp://h:1",        # uno schema che il motore non sa esprimere
    "http://:80",       # nessun host - letto come host "80" dalla prima
                        # versione del lettore, e trovato dal caso noto-cattivo
    "socks5://h:abc",   # una porta che non e' un numero
    "socks5://h:0",     # fuori intervallo
    "http://h:99999",
])
def test_un_endpoint_illeggibile_ALZA_invece_di_sparire(server):
    """La forma del difetto, in piccolo.

    Chi chiama non puo' distinguere "nessun proxy" da "il proxy e' stato
    buttato", e il secondo caso deve fermare il lancio.
    """
    with pytest.raises(ValueError):
        parse_proxy({"server": server})


@pytest.mark.unit
def test_nessuno_schema_scrive_prefs_di_instradamento():
    """La CLASSE, non il singolo nome: nessuna `network.proxy.*` di percorso.

    Asserito su tutti e quattro gli schemi insieme, perche' la regressione
    nasceva proprio dal fatto che due schemi facevano cose diverse.
    """
    vietate = ("network.proxy.type", "network.proxy.socks",
               "network.proxy.socks_port", "network.proxy.socks_version",
               "network.proxy.socks_username", "network.proxy.socks_password",
               "network.proxy.socks_remote_dns", "network.proxy.http",
               "network.proxy.http_port", "network.proxy.ssl",
               "network.proxy.ssl_port")
    for server in ("socks5://h:1080", "socks4://h:1080", "http://h:8080",
                   "https://h:3128"):
        prefs = {}
        tornato = configure_proxy({"server": server, "username": "u",
                                   "password": "p"}, prefs)
        assert tornato is not None, server
        for nome in vietate:
            assert nome not in prefs, (server, nome)


@pytest.mark.unit
def test_le_prefs_dei_canali_di_fuga_restano_su_OGNI_schema():
    """Cio' che non era instradamento non e' stato toccato dall'unificazione."""
    for server in ("socks5://h:1080", "http://h:8080"):
        prefs = {}
        configure_proxy({"server": server}, prefs)
        assert prefs["network.proxy.allow_bypass"] is False, server
        assert prefs["zoom.stealth.dns.no_local_resolution"] is True, server
        assert prefs["zoom.stealth.webrtc.no_direct_udp"] is True, server


@pytest.mark.unit
def test_il_lancio_diretto_RIFIUTA_un_proxy_invece_di_inventarsi_una_strada():
    """Non ha una connessione di protocollo, quindi non ha un proxy.

    Prima si scriveva prefs sue, ed e' la terza strada: quella che rendeva
    possibile dimenticarne una. Il rifiuto e' l'alternativa onesta, e arriva
    PRIMA che fuso e lingua vengano risolti attraverso quel proxy.
    """
    from invisible_core.launch import build_launch_plan

    with pytest.raises(ValueError) as caught:
        build_launch_plan(1, profile_dir="/non/serve",
                          proxy={"server": "http://h:8080"})
    messaggio = str(caught.value)
    assert "invisible_playwright" in messaggio, messaggio


@pytest.mark.unit
def test_il_lancio_diretto_senza_proxy_non_e_toccato():
    """Il caso che deve NON scattare: `direct://` e l'assenza non sono un proxy."""
    from invisible_core import parse_proxy as _pp

    for niente in (None, {}, {"server": ""}, {"server": "direct://"}):
        assert _pp(niente) is None


@pytest.mark.unit
def test_dietro_qualunque_proxy_il_ripiego_diretto_e_spento():
    """Il ripiego di Firefox a un proxy che fallisce e' una connessione DIRETTA.

    `network.proxy.allow_bypass` vale `true` di default nella nostra build, e un
    canale che chiede `bypassProxy` salta `ResolveProxy()`: risolve col resolver
    dell'utente e si connette col suo IP, proprio mentre il proxy sta fallendo.
    I due consumatori sono `fallbackOrReject` di Remote Settings e `retryRequest`
    della telemetria, e Remote Settings gira a ogni sessione.

    Misurato il 2026-08-25 con un SOCKS5 locale che rifiuta apposta quei due
    host: senza la pref, 43 rifiuti e l'host di Remote Settings risolto 13 volte
    in locale; con la pref, 45 rifiuti e ZERO risoluzioni locali, cioe' lo stesso
    insieme di nomi di un SOCKS5 che non fallisce. La navigazione resta ok in
    entrambi i bracci.

    Si asserisce su OGNI SCHEMA, non sul solo SOCKS: il difetto non dipende
    dalla versione di SOCKS e nemmeno dal fatto che sia SOCKS. Decisione del
    proprietario 2026-08-25, "dobbiamo permettere di usare tutti i protocolli":
    la difesa vale ovunque, invece di essere una ragione per escludere uno
    schema.
    """
    from invisible_core import configure_proxy

    for server in ("socks5://h:1080", "socks4://h:1080", "socks://h:1080",
                   "http://h:8080", "https://h:8443"):
        prefs = {}
        configure_proxy({"server": server}, prefs)
        assert prefs.get("network.proxy.allow_bypass") is False, (
            f"{server}: senza questa pref, un fallimento del proxy fa uscire "
            f"Firefox in diretta con l'IP vero")


def test_dietro_qualunque_proxy_il_dns_locale_e_dichiarato_vietato():
    """`allow_bypass` chiude i CANALI. Questa chiude cio' che canale non e'.

    Tre superfici chiamano `AsyncResolveNative` diretto e non passano da nessun
    filtro: `NetworkConnectivityService`, le sonde dell'euristica DoH, e il
    resolver ICE. Il cancello del motore le fermerebbe
    (`netwerk/dns/DNSServiceBase.cpp`, `DNSForbiddenByActiveProxy`) ma riconosce
    solo un proxy scritto nelle `network.proxy.*`, e sul ramo HTTP non ne
    scriviamo nessuna. Quindi il fatto lo dichiara il core: regola 1.

    La peggiore delle tre e' il resolver ICE, perche' il nome lo sceglie LA
    PAGINA via `iceServers`: senza questa pref, dietro un proxy HTTP un sito
    puo' far risolvere al browser un nome suo e guardare quale resolver
    interroga, cioe' quello di casa, mentre l'HTTP esce dal proxy.

    Misurato il 2026-08-25 dietro proxy HTTP, due giri identici: senza la pref
    21 nomi risolti in locale fra cui `stunprobe.invalid` 6; con la pref **3**,
    cioe' `local`, `localhost` e l'endpoint del proxy - la stessa identica forma
    del braccio SOCKS. Il braccio senza proxy resta a 28 nomi: la pref li' non
    viene scritta e il motore si comporta come upstream.
    """
    from invisible_core import configure_proxy

    for server in ("socks5://h:1080", "socks4://h:1080", "socks://h:1080",
                   "http://h:8080", "https://h:8443"):
        prefs = {}
        configure_proxy({"server": server}, prefs)
        assert prefs.get("zoom.stealth.dns.no_local_resolution") is True, (
            f"{server}: senza questa dichiarazione una PAGINA puo' far "
            f"risolvere al browser un nome scelto da lei sul resolver di casa")


def test_senza_proxy_il_ripiego_non_si_dichiara():
    """Il caso che deve NON scattare, e vale quanto quello che scatta.

    Senza proxy non c'e' niente da aggirare, quindi la pref non ha nulla da
    governare e dichiararla sarebbe una divergenza dal retail pagata per zero.
    Stessa forma per `direct://`, che e' un modo di dire "nessun proxy".
    """
    from invisible_core import configure_proxy

    for proxy in (None, {}, {"server": ""}, {"server": "direct://"}):
        prefs = {}
        configure_proxy(proxy, prefs)
        assert "network.proxy.allow_bypass" not in prefs, (
            f"{proxy!r}: pref dichiarata dove non c'e' proxy da aggirare")
        assert "zoom.stealth.dns.no_local_resolution" not in prefs, (
            f"{proxy!r}: senza proxy il DNS locale e' il percorso NORMALE, e "
            f"vietarlo sarebbe una divergenza dal retail pagata per zero")


def test_un_endpoint_malformato_non_lascia_prefs_a_meta():
    """Il rifiuto deve lasciare il dict come l'ha trovato.

    La prima stesura scriveva `allow_bypass` PRIMA di validare la porta, quindi
    un endpoint malformato sollevava lasciandosi dietro una pref: un dict
    parzialmente configurato che il chiamante puo' usare credendolo intatto. Due
    test esistenti sono diventati rossi e avevano ragione loro.

    ⛔ L'input e' cambiato il 2026-08-30 e la ragione va detta: prima era
    `socks5://senzaporta`, perche' un SOCKS senza porta veniva RIFIUTATO. Ora
    una porta mancante prende il default dello schema, come documenta
    Playwright, e puo' farlo senza rischio proprio grazie all'unificazione: non
    esiste piu' un percorso che parte lo stesso senza proxy: o il comando viene
    mandato, o il lancio fallisce. Quindi serve un endpoint davvero illeggibile.
    """
    import pytest
    from invisible_core import configure_proxy

    prefs = {}
    with pytest.raises(ValueError):
        configure_proxy({"server": "socks5://host:non-un-numero"}, prefs)
    assert prefs == {}, f"prefs sporcate dal rifiuto: {prefs}"
