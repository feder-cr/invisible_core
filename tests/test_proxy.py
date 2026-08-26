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

from invisible_core._proxy import configure_proxy


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
#  CP10-CP13: SOCKS - mutate prefs, return None
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp10_socks5_with_credentials():
    prefs = {}
    proxy = {
        "server": "socks5://host:1080",
        "username": "u",
        "password": "p",
    }
    result = configure_proxy(proxy, prefs)
    assert result is None
    assert prefs["network.proxy.type"] == 1
    assert prefs["network.proxy.socks"] == "host"
    assert prefs["network.proxy.socks_port"] == 1080
    assert prefs["network.proxy.socks_version"] == 5
    assert prefs["network.proxy.socks_username"] == "u"
    assert prefs["network.proxy.socks_password"] == "p"
    assert prefs["network.proxy.socks_remote_dns"] is True


@pytest.mark.unit
def test_cp11_socks4_sets_version_4():
    prefs = {}
    configure_proxy({"server": "socks4://host:1080"}, prefs)
    assert prefs["network.proxy.socks_version"] == 4


@pytest.mark.unit
def test_cp12_bare_socks_defaults_to_v5():
    prefs = {}
    configure_proxy({"server": "socks://host:1080"}, prefs)
    assert prefs["network.proxy.socks_version"] == 5


@pytest.mark.unit
def test_cp13_socks_scheme_is_case_insensitive():
    prefs = {}
    proxy = {"server": "SOCKS5://HOST:1080"}
    result = configure_proxy(proxy, prefs)
    assert result is None
    assert prefs["network.proxy.type"] == 1
    # Host preserves case (only the scheme is case-folded).
    assert prefs["network.proxy.socks"] == "HOST"
    assert prefs["network.proxy.socks_version"] == 5


# ──────────────────────────────────────────────────────────────────────
#  CP14-CP15: edge SOCKS inputs
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp14_socks_without_port_is_refused_not_dropped():
    """This test asserted the opposite until 2026-08-01, and it was wrong.

    A SOCKS server with no port wrote no `network.proxy.*` pref and returned
    None, which the caller reads as "SOCKS handled, Playwright needs nothing".
    The session then went out on the host's own address while the caller
    believed it was proxied. Silent, and for this package the worst outcome
    available: the browser launches, the page loads, the IP is wrong.

    The two parsers disagreed as well - `_geo` builds `socks5h://host` from the
    same dict and gives it to requests - so a malformed endpoint could leave one
    half of a session proxied and the other half not.

    Refusing costs a caller with a typo an exception at launch. That is the
    trade, and it is the right way round.
    """
    prefs = {}
    with pytest.raises(ValueError) as exc:
        configure_proxy({"server": "socks5://hostonly"}, prefs)
    assert "no port" in str(exc.value)
    assert prefs == {}, "a refused proxy must not leave half its prefs behind"


@pytest.mark.unit
def test_cp15_socks_without_credentials_uses_empty_strings():
    prefs = {}
    configure_proxy({"server": "socks5://host:1080"}, prefs)
    assert prefs["network.proxy.socks_username"] == ""
    assert prefs["network.proxy.socks_password"] == ""


@pytest.mark.unit
def test_cp15b_socks_with_none_credentials_uses_empty_strings():
    """`proxy.get("username")` returning None should resolve to ""."""
    prefs = {}
    configure_proxy(
        {"server": "socks5://host:1080", "username": None, "password": None},
        prefs,
    )
    assert prefs["network.proxy.socks_username"] == ""
    assert prefs["network.proxy.socks_password"] == ""


# ──────────────────────────────────────────────────────────────────────
#  CP16: mutation contract - prefs dict mutated in-place
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp16_prefs_mutated_in_place():
    """Caller's prefs dict receives the SOCKS keys directly (not a copy)."""
    prefs = {"existing.pref": "kept"}
    sentinel = prefs
    configure_proxy({"server": "socks5://host:1080"}, prefs)
    # Same object identity - mutated, not replaced.
    assert prefs is sentinel
    # Existing pref preserved.
    assert prefs["existing.pref"] == "kept"
    # SOCKS keys added.
    assert "network.proxy.type" in prefs
    assert "network.proxy.socks" in prefs


# ──────────────────────────────────────────────────────────────────────
#  CP17: boundary - IPv6-style host preserved via rsplit
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp17_ipv6_bracketed_host_preserved_via_rsplit():
    """rsplit(':', 1) keeps brackets intact for `[::1]:1080`-style hosts."""
    prefs = {}
    configure_proxy({"server": "socks5://[::1]:1080"}, prefs)
    assert prefs["network.proxy.socks"] == "[::1]"
    assert prefs["network.proxy.socks_port"] == 1080


# ──────────────────────────────────────────────────────────────────────
#  Recheck additions - branches discovered while re-reading _proxy.py
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_socks_with_surrounding_whitespace_in_server_stripped():
    """The implementation strips whitespace before scheme checks."""
    prefs = {}
    result = configure_proxy({"server": "  socks5://host:1080  "}, prefs)
    assert result is None
    assert prefs["network.proxy.socks"] == "host"
    assert prefs["network.proxy.socks_port"] == 1080


@pytest.mark.unit
def test_server_key_missing_returns_none():
    """No 'server' key → treated as empty → no-op."""
    prefs = {}
    result = configure_proxy({"username": "u"}, prefs)
    assert result is None
    assert prefs == {}


@pytest.mark.unit
def test_server_key_none_returns_none():
    """`server: None` is normalized to "" by the implementation."""
    prefs = {}
    result = configure_proxy({"server": None}, prefs)
    assert result is None
    assert prefs == {}


@pytest.mark.unit
def test_socks_port_coerced_to_int():
    """Port string is parsed via int() - not a numeric string."""
    prefs = {}
    configure_proxy({"server": "socks5://host:443"}, prefs)
    assert prefs["network.proxy.socks_port"] == 443
    assert isinstance(prefs["network.proxy.socks_port"], int)


@pytest.mark.unit
def test_socks_non_numeric_port_raises_value_error():
    """Non-numeric port is a programmer error - int() raises."""
    prefs = {}
    with pytest.raises(ValueError):
        configure_proxy({"server": "socks5://host:notaport"}, prefs)


@pytest.mark.unit
def test_the_two_proxy_parsers_agree_on_what_is_usable():
    """One dict, two readers, and nothing compared them.

    `configure_proxy` writes the browser's prefs; `_geo._proxies_for` builds the
    requests URL for the egress lookup. A dict that one accepts and the other
    rejects means half a session is proxied - which is what a portless SOCKS
    endpoint used to produce.
    """
    from invisible_core import _geo

    for server in ("socks5://hostonly", "socks4://hostonly", "socks://hostonly"):
        proxy = {"server": server}
        with pytest.raises(ValueError):
            configure_proxy(proxy, {})
        with pytest.raises(ValueError):
            _geo._proxies_for_requests(proxy)

    for server in ("socks5://host:1080", "http://host:8080", "https://host:8080"):
        proxy = {"server": server}
        configure_proxy(proxy, {})          # must not raise
        assert _geo._proxies_for_requests(proxy)     # must produce a usable URL


# ---------------------------------------------------------------------------
# delegates_auth: il chiamante dichiara se ha un Playwright a cui delegare
#
# Il difetto che questi casi difendono e' vissuto sei settimane in 17 versioni
# pubblicate: `build_launch_plan` chiamava configure_proxy, riceveva indietro il
# dict di un endpoint HTTP, non aveva dove metterlo e lo scartava. Il browser
# partiva senza nessuna pref di proxy, mentre `_geo` aveva gia' risolto fuso e
# lingua ATTRAVERSO quel proxy. Nessuna eccezione, nessun log: la pagina
# dichiarava un paese e la connessione ne usava un altro.


def test_un_http_senza_credenziali_viene_INSTRADATO_dalle_prefs():
    """Il routing non ha bisogno di Playwright, ed e' misurato sul binario
    spedito: con network.proxy.type piu' http/http_port/ssl/ssl_port il browser
    va al proxy e non ripiega su diretto."""
    prefs = {}
    resto = configure_proxy({"server": "http://gw.esempio:8080"}, prefs,
                            delegates_auth=False)
    assert resto is None
    assert prefs["network.proxy.type"] == 1
    assert prefs["network.proxy.http"] == "gw.esempio"
    assert prefs["network.proxy.http_port"] == 8080
    assert prefs["network.proxy.ssl"] == "gw.esempio"
    assert prefs["network.proxy.ssl_port"] == 8080


def test_un_http_CON_credenziali_su_un_percorso_senza_playwright_e_rifiutato():
    """L'autenticazione invece Playwright la vuole: le credenziali che scriviamo
    per i SOCKS arrivano al nsProxyInfo ma nessuno ne costruisce la
    Proxy-Authorization, quindi un endpoint autenticato si ferma al 407.
    Rifiutare e' l'unico esito onesto: l'alternativa e' uscire dall'IP di casa
    credendo di essere proxati."""
    for server in ("http://gw.esempio:8080", "https://gw.esempio:8080"):
        with pytest.raises(ValueError) as e:
            configure_proxy({"server": server, "username": "u", "password": "p"},
                            {}, delegates_auth=False)
        messaggio = str(e.value)
        assert "credentials" in messaggio
        assert "invisible_playwright" in messaggio, "il rifiuto deve dire dove funziona"


def test_una_password_senza_username_e_comunque_una_credenziale():
    """Il controllo e' un OR, non un AND: mezzo segreto e' un segreto."""
    with pytest.raises(ValueError):
        configure_proxy({"server": "http://gw.esempio:8080", "password": "p"},
                        {}, delegates_auth=False)


def test_il_percorso_che_delega_NON_cambia_comportamento():
    """Guardia di regressione sul ramo misurato verde: il wrapper passa il dict a
    Playwright, che instrada e risponde al 407. Se questo caso si muove, si e'
    rotto l'unico percorso HTTP che funziona per gli utenti.

    ⛔ IL CONTRATTO E' SULLE PREFS DI INSTRADAMENTO, non su "nessuna pref", e
    fino al 2026-08-25 era scritto `prefs == {}`. Cio' che va protetto e' che
    non scriviamo noi il ROUTING (`type`, `http`, `ssl`, `socks*`), perche' quel
    ramo lo instrada Playwright ed e' l'unico che sa rispondere al 407.

    E' cambiato perche' `network.proxy.allow_bypass` deve valere anche qui: non
    instrada niente e non tocca l'autenticazione, toglie solo a Firefox il
    ripiego DIRETTO quando il proxy fallisce. Con `prefs == {}` il ramo HTTP
    sarebbe restato scoperto proprio sul difetto misurato quel giorno.
    """
    instradamento = ("network.proxy.type", "network.proxy.http",
                     "network.proxy.http_port", "network.proxy.ssl",
                     "network.proxy.ssl_port", "network.proxy.socks",
                     "network.proxy.socks_port", "network.proxy.socks_version",
                     "network.proxy.socks_remote_dns")
    for creds in ({}, {"username": "u", "password": "p"}):
        proxy = dict({"server": "http://gw.esempio:8080"}, **creds)
        prefs = {}
        assert configure_proxy(proxy, prefs) is proxy
        scritte = [k for k in prefs if k in instradamento]
        assert not scritte, f"chi delega non deve instradare da se': {scritte}"
        assert prefs.get("network.proxy.allow_bypass") is False, (
            "il ramo HTTP resta senza la difesa dal ripiego diretto")


def test_un_http_senza_porta_e_rifiutato_anche_qui():
    """Stessa refusal del ramo SOCKS, sull'altro schema: un endpoint senza porta
    non e' un endpoint."""
    with pytest.raises(ValueError):
        configure_proxy({"server": "http://gw.esempio"}, {}, delegates_auth=False)


def test_i_socks_non_sono_toccati_da_delegates_auth():
    """delegates_auth parla solo degli schemi che Playwright deve autenticare.
    Un SOCKS e' gia' completo nelle prefs, con o senza."""
    for delega in (True, False):
        prefs = {}
        assert configure_proxy({"server": "socks5://gw:1080", "username": "u",
                                "password": "p"}, prefs, delegates_auth=delega) is None
        assert prefs["network.proxy.socks_username"] == "u"
        assert prefs["network.proxy.socks_version"] == 5


def test_il_lancio_diretto_dichiara_di_non_poter_delegare():
    """Il gate vero: non che la funzione sappia rifiutare, ma che il percorso che
    non ha Playwright glielo DICA. Senza questo, i casi qui sopra passano e il
    difetto resta esattamente dov'era.

    ⛔ E si legge l'ALBERO SINTATTICO, non il testo del sorgente. La prima
    stesura faceva `"delegates_auth=False" in inspect.getsource(...)` ed e'
    sopravvissuta alla propria mutazione: il commento che sta sopra la chiamata
    contiene quella stessa stringa, quindi il controllo era soddisfatto dal
    COMMENTO mentre il codice sotto aveva di nuovo il difetto. E' il difetto piu'
    ripetuto del progetto, colto qui solo perche' la mutazione e' stata eseguita.
    """
    import ast
    import inspect
    from invisible_core import launch

    albero = ast.parse(inspect.getsource(launch.build_launch_plan).lstrip())
    chiamate = [n for n in ast.walk(albero)
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", getattr(n.func, "attr", None))
                == "compose_session_prefs"]
    assert chiamate, "build_launch_plan non compone piu' le prefs: gate da riscrivere"
    for c in chiamate:
        kw = {k.arg: k.value for k in c.keywords}
        assert "delegates_auth" in kw, (
            "build_launch_plan non dichiara di non poter delegare: un endpoint "
            "HTTP tornerebbe di nuovo come dict e verrebbe scartato dal .prefs")
        assert isinstance(kw["delegates_auth"], ast.Constant)
        assert kw["delegates_auth"].value is False, (
            "delegates_auth c'e' ma non e' False: questo percorso Playwright non "
            "ce l'ha")
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
    """
    import pytest
    from invisible_core import configure_proxy

    prefs = {}
    with pytest.raises(ValueError):
        configure_proxy({"server": "socks5://senzaporta"}, prefs)
    assert prefs == {}, f"prefs sporcate dal rifiuto: {prefs}"
