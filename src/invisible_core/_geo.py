"""Resolve the session timezone from the egress IP (``timezone="auto"``).

Approach B: discover the egress IP with one HTTP request - routed *through the
proxy* when one is set, otherwise a direct request that sees the host's own
public IP - then map IP → IANA timezone with an offline mmdb
(``daijro/geoip-all-in-one``, downloaded + cached by ``_geoip_db.py``).

Precedence (see ``resolve_session_timezone``):

    explicit IANA   → unchanged   explicit always wins
    "" / "auto"     → egress      ALWAYS resolve. With a proxy, from the proxy
                                  egress IP; without a proxy, from the host's
                                  own public IP. This is the default.

On failure:
    with a proxy    → raise       a foreign proxy paired with the host TZ is
                                  the precise ``timezone_mismatch`` signal, so
                                  we fail loudly rather than fall back silently.
    without a proxy → "" (host)   the host TZ is a safe default, so a transient
                                  lookup failure must not break the launch.
"""
from __future__ import annotations

import ipaddress
import re
import time
from typing import Any, Dict, NamedTuple, Optional
from urllib.parse import quote

import requests


class GeoTimezoneError(RuntimeError):
    """Raised when ``timezone="auto"`` cannot resolve a valid IANA zone.

    Carries ``kind`` - one of the keys of :data:`_REMEDY` - and, for a discovery
    failure, one :class:`_Attempt` per endpoint. Read those instead of matching
    on the prose: the message is written for a person, the attributes are the
    part a caller may branch on.
    """

    def __init__(self, message: str, *, kind: str = "unknown",
                 attempts: "tuple[_Attempt, ...]" = ()) -> None:
        super().__init__(message)
        self.kind = kind
        self.attempts = attempts


# Plain-text IP echo endpoints (each returns just the caller's public IP).
_IP_ECHO_ENDPOINTS = (
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://checkip.amazonaws.com",
)

_SOCKS_SCHEMES = ("socks5://", "socks4://", "socks://")


class _Attempt(NamedTuple):
    """One endpoint tried, and what came back from it."""

    url: str
    seconds: float
    kind: str
    detail: str

    def line(self) -> str:
        return f"  {self.url:<32} {self.seconds:5.2f}s  {self.kind}: {self.detail}"


# What each failure MEANS, and what to do about it. TWO strings per class: the
# advice when a proxy is in the request path, and the advice for a direct
# request. They are not interchangeable, and one string per class was a defect
# of exactly the kind this whole change exists to remove: on the direct path the
# message read "could not discover the egress IP directly (no proxy set): the
# proxy HOSTNAME does not resolve - check it for a typo", which denies a proxy
# and blames one in the same sentence, and sends the reader to a config line
# that is empty.
#
# The CLASS names the MECHANISM and never the path, which is what lets
# `_classify` stay a pure function of the exception. Only the advice knows
# whether a proxy exists, and the caller is the one that already knows it, so
# nothing computes that fact twice.
#
# Three families, and telling them apart is the first thing to know when a
# launch dies, because they are fixed in three different places:
#   the transport   the proxy, the credentials, the local network
#   endpoint_*      a third-party echo service   (theirs, usually transient)
#   the rest        the geoip database           (a download, a disk, the data)
_REMEDY = {
    # class:                   (a proxy is in the path,  the request went direct)
    "proxy_auth": (
        "the proxy refused the credentials - check username/password",
        "something on this network demanded proxy credentials, so a transparent "
        "proxy is intercepting the request"),
    "proxy_rejected": (
        "the proxy answered but refused to open the tunnel - check the plan, the "
        "target, or an allowlist on the provider side",
        "something on this network refused to open the tunnel, so a transparent "
        "proxy is intercepting the request"),
    "socks_missing": (
        "a socks:// proxy needs PySocks - pip install 'requests[socks]'",
        "a socks:// proxy needs PySocks - pip install 'requests[socks]'"),
    "dns_failure": (
        "the proxy HOSTNAME does not resolve - check it for a typo",
        "the echo endpoint hostname does not resolve, so this host has no working "
        "DNS - not a proxy problem, there is no proxy here"),
    "connect_failed": (
        "nothing accepted a connection at the proxy host:port",
        "no route to the echo endpoint from this host - the network is down or "
        "something is refusing the connection"),
    "read_timeout": (
        "the proxy accepted the connection and then never answered",
        "the endpoint accepted the connection and then never answered"),
    "tls_failed": (
        "TLS to the endpoint failed THROUGH the proxy, which is what an "
        "intercepting proxy looks like - check the certificate chain",
        "TLS to the endpoint failed, which is what a captive portal or a "
        "TLS-inspecting middlebox looks like - check the certificate chain"),
    "endpoint_http_error": (
        "the echo endpoint itself answered with an error, which is a third-party "
        "outage rather than a fault on this side",
        "the echo endpoint itself answered with an error, which is a third-party "
        "outage rather than a fault on this side"),
    "endpoint_not_an_ip": (
        "the reply was not an IP address - something is intercepting the request "
        "(a captive portal, or a proxy error page)",
        "the reply was not an IP address - something on this network is "
        "intercepting the request, most likely a captive portal"),
    "not_routing": (
        "the reply was a PRIVATE address, so the request never left the local "
        "network - the proxy is not routing to the internet",
        "the reply was a PRIVATE address, so the request never left the local "
        "network - something here answered on the endpoint's behalf"),
    "no_endpoint_tried": (
        "the budget expired before a single endpoint could be contacted, so "
        "nothing below was measured - raise `budget`, or find what delayed the "
        "caller before this step",
        "the budget expired before a single endpoint could be contacted, so "
        "nothing below was measured - raise `budget`, or find what delayed the "
        "caller before this step"),
    # Phrased to follow "the egress IP is <ip>, ... but", which is how the raise
    # in `_geoip_database` reads it. Repeating "the egress IP was found" here
    # would say it twice in one sentence.
    "geoip_unavailable": (
        "the geoip database could not be obtained, so nothing can be mapped - a "
        "download or a disk problem, NOT a proxy problem",
        "the geoip database could not be obtained, so nothing can be mapped - a "
        "download or a disk problem"),
    "ip_not_in_db": (
        "the egress IP is absent from the geoip database, which usually means the "
        "database is stale rather than that the IP is wrong",
        "the egress IP is absent from the geoip database, which usually means the "
        "database is stale rather than that the IP is wrong"),
    "no_timezone_for_ip": (
        "the geoip database knows the IP but carries no timezone for it",
        "the geoip database knows the IP but carries no timezone for it"),
    "no_coordinates_for_ip": (
        "the geoip database knows the IP but carries no coordinates",
        "the geoip database knows the IP but carries no coordinates"),
    "invalid_timezone": (
        "the geoip database returned a zone this system's tz database does not "
        "know - check tzdata",
        "the geoip database returned a zone this system's tz database does not "
        "know - check tzdata"),
    "unknown": (
        "unrecognised failure - the repr on the line above is all there is",
        "unrecognised failure - the repr on the line above is all there is"),
    "mixed": (
        "the endpoints failed for DIFFERENT reasons, listed below",
        "the endpoints failed for DIFFERENT reasons, listed below"),
}


def _remedy(kind: str, proxied: bool) -> str:
    """The advice for one class, for the path the request actually took.

    Falls back to the class name rather than inventing text: a class with no
    entry is a bug in the table, and saying its name is more honest than
    guessing what it means.
    """
    pair = _REMEDY.get(kind)
    if not pair:
        return kind
    return pair[0] if proxied else pair[1]


# `Tunnel connection failed: 407 Proxy Authentication Required` and friends. A
# proxy's status code reaches us only as text inside a chained cause, so this is
# one of the three places a message is read rather than a type inspected; the
# other two are the DNS tokens and the SOCKS marker in `_classify`.
_TUNNEL_STATUS = re.compile(r"Tunnel connection failed:\s*(\d{3})")

# The tokens that mean "a name did not resolve". THREE spellings, because the
# HTTP and the SOCKS paths through urllib3 do not agree, measured 2026-09-02:
# an HTTP proxy with a bad hostname produces `NameResolutionError` in the text,
# while socks5h:// produces a `NewConnectionError` whose text says only
# `[Errno 11001] getaddrinfo failed`. Matching the first alone sent every SOCKS
# hostname typo to `connect_failed`, whose advice is to check the PORT.
# `gaierror` is the type name, which appears only when a bare `socket.gaierror`
# is classified directly rather than through requests.
_DNS_TOKENS = ("NameResolutionError", "getaddrinfo failed", "gaierror")

# The networks that mean "this request never left the local network".
#
# NOT `ipaddress.is_private`, and this is the trap to know about: Python counts
# the RFC 5737 DOCUMENTATION ranges (192.0.2/24, 198.51.100/24, 203.0.113/24) as
# private, so `is_private` would reject the very addresses this package's own
# tests use as stand-ins for a real egress - passing every test about private
# replies while refusing every legitimate one.
_NOT_ROUTABLE = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",   # RFC1918
    "127.0.0.0/8", "169.254.0.0/16", "0.0.0.0/8",      # loopback, link-local, this-host
    # RFC 6598 shared address space, handed out by a carrier-grade NAT. Never
    # globally routed and never present in a geoip database, and `is_private` is
    # False for it (measured), so the rejected shortcut would not have caught it
    # and neither did the first version of this list.
    "100.64.0.0/10",
    # The IPv6 counterpart of each row above that has one: loopback,
    # unique-local, link-local, the unspecified address, and the deprecated
    # site-local block, whose `is_private` is also False.
    "::1/128", "fc00::/7", "fe80::/10", "::/128", "fec0::/10",
))


def _classify(exc: BaseException) -> "tuple[str, str]":
    """Name the failure class behind one failed attempt.

    A PURE function - no network, no clock, no state, and no notion of whether a
    proxy is in the path - which is what lets the whole classification be checked
    against known-bad inputs without a proxy. The class names the MECHANISM; the
    caller pairs it with the path to produce advice, because the caller is the
    only place that already knows the path.

    Classified on MEASURED evidence rather than on the exception type, because
    the type does not separate the cases. Measured 2026-09-02: a closed proxy
    port, a proxy hostname that does not resolve, a blackholed proxy address AND
    a 407 all arrive as `requests.exceptions.ProxyError`, while the same closed
    port behind socks5:// arrives as `ConnectTimeout` instead. What distinguishes
    them lives in the chained cause, which reaches `str(exc)`.

    ORDER IS LOAD-BEARING in three places, and each is marked below. The general
    rule: any branch testing a SUBCLASS must precede the branch testing its base,
    and `requests.exceptions` has two inheritance edges that are easy to miss -
    `SSLError` and `ProxyError` both subclass `ConnectionError`, and
    `InvalidSchema` subclasses both `RequestException` and `ValueError`.
    """
    text = f"{type(exc).__name__}: {exc}"

    status = _TUNNEL_STATUS.search(text)
    if status:
        code = status.group(1)
        if code == "407":
            return "proxy_auth", f"proxy answered {code} to CONNECT"
        return "proxy_rejected", f"proxy answered {code} to CONNECT"

    # ORDER 1: before the bare-ValueError branch. `InvalidSchema` inherits from
    # BOTH RequestException and ValueError, so without this a missing PySocks
    # would be reported as a malformed reply. The `not isinstance(...)` guard on
    # that branch is a second, independent defence; both are tested.
    if isinstance(exc, requests.exceptions.InvalidSchema) and "SOCKS" in text:
        return "socks_missing", "PySocks is not installed"

    if isinstance(exc, requests.exceptions.HTTPError):
        code = getattr(getattr(exc, "response", None), "status_code", None)
        return "endpoint_http_error", f"endpoint answered HTTP {code}"

    if isinstance(exc, ValueError) and not isinstance(exc, requests.RequestException):
        # `ipaddress.ip_address` on a body that is not an address.
        return "endpoint_not_an_ip", f"reply was not an IP ({str(exc)[:70]})"

    if any(token in text for token in _DNS_TOKENS):
        return "dns_failure", "a hostname did not resolve"

    # ORDER 2: before the ConnectionError arm, which SSLError subclasses. Without
    # this a certificate failure - the signature of the TLS-intercepting captive
    # portal this classification exists to separate - was reported as a dead TCP
    # port, sending the reader to check a host:port that was answering fine.
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls_failed", f"TLS failed ({str(exc)[:70]})"

    # ORDER 3: before the ConnectionError arm, which ProxyError also subclasses.
    # ReadTimeout is not a ConnectionError, but it is kept adjacent so the three
    # ordered branches read as one group.
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "read_timeout", "connected, then no reply before the deadline"

    if isinstance(exc, (requests.exceptions.ProxyError,
                        requests.exceptions.ConnectTimeout,
                        requests.exceptions.ConnectionError)):
        return "connect_failed", "could not open a connection"

    return "unknown", repr(exc)[:110]


def _proxy_is_set(proxy: Optional[Dict[str, str]]) -> bool:
    if not proxy:
        return False
    server = (proxy.get("server") or "").strip()
    return bool(server) and server.lower() != "direct://"


def _proxies_for_requests(proxy: Dict[str, str]) -> Dict[str, str]:
    """Translate our proxy dict into a ``requests`` proxies mapping.

    SOCKS5 uses the ``socks5h`` scheme so DNS is resolved proxy-side (matches
    ``network.proxy.socks_remote_dns=True`` in the Firefox path). HTTP/HTTPS
    pass through unchanged. Credentials are URL-encoded.
    """
    server = (proxy.get("server") or "").strip()
    low = server.lower()
    if low.startswith("socks5://") or low.startswith("socks://"):
        scheme = "socks5h"
    elif low.startswith("socks4://"):
        scheme = "socks4"
    elif low.startswith("https://"):
        scheme = "https"
    else:
        scheme = "http"

    host_port = server.split("://", 1)[1] if "://" in server else server
    if ":" not in host_port:
        # The same refusal `configure_proxy` makes, on the same dict. These two
        # are the only readers of a proxy endpoint in the package and they used
        # to disagree: this one built `socks5h://host` with no port and handed it
        # to requests while the browser side wrote no proxy pref at all, so one
        # half of a session was proxied and the other was not.
        raise ValueError(
            f"proxy server {server!r} has no port. An endpoint needs host:port "
            f"- e.g. socks5://host:1080")
    user = proxy.get("username") or ""
    pwd = proxy.get("password") or ""
    if user:
        auth = f"{quote(user, safe='')}:{quote(pwd, safe='')}@"
    else:
        auth = ""
    url = f"{scheme}://{auth}{host_port}"
    return {"http": url, "https": url}


def discover_egress_ip(
    proxy: Optional[Dict[str, str]] = None,
    *,
    timeout: float = 10.0,
    budget: float = 15.0,
) -> str:
    """Return the public egress IP.

    Routes the request through ``proxy`` when given (SOCKS support requires
    ``requests[socks]`` / PySocks); with ``proxy=None`` it makes a direct
    request that sees the host's own public IP. Tries each echo endpoint in
    turn; raises :class:`GeoTimezoneError` if none return a valid IP.

    ``timeout`` bounds ONE request; ``budget`` bounds the whole step. Both are
    needed, and having only the first is what made this the slowest thing in a
    launch: three endpoints tried in sequence at ten seconds each is a
    thirty-second worst case that nothing capped, and one launch in six spent
    35s here. A per-request timeout says how long to wait for a server; it
    cannot say how long the caller is willing to wait in total. The remaining
    budget is now handed to each request, so a slow first endpoint shortens the
    second rather than adding to it, and the step returns or raises within
    ``budget`` however many endpoints the list grows to.
    """
    proxies = _proxies_for_requests(proxy) if proxy else None
    attempts: "list[_Attempt]" = []
    deadline = time.monotonic() + budget
    for url in _IP_ECHO_ENDPOINTS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        started = time.monotonic()
        try:
            # Una COPPIA, non uno scalare. `requests` applica un timeout scalare
            # alla fase di CONNESSIONE e poi di nuovo a quella di LETTURA, quindi
            # `timeout=10` puo' spendere venti secondi in una chiamata sola e
            # sfondare da solo un budget di quindici. Misurato il 2026-08-10 con
            # un proxy che aveva smesso di instradare: l'errore riportava
            # `20.1s` con `budget=15` e "1 of 3 endpoints", cioe' il primo si e'
            # mangiato tutto e la ridondanza degli altri due non e' mai entrata
            # in gioco. Il commento della funzione descriveva gia' l'intento
            # giusto - il budget limita il passo intero - ed era il codice a
            # implementarne un altro.
            #
            # Meta' per fase garantisce che UNA chiamata non superi il rimanente,
            # quindi il ciclo arriva davvero al secondo e al terzo endpoint
            # quando il primo tace.
            slice_ = min(timeout, remaining)
            resp = requests.get(
                url, proxies=proxies, timeout=(slice_ / 2, slice_ / 2)
            )
            resp.raise_for_status()
            ip = resp.text.strip()
            parsed = ipaddress.ip_address(ip)  # raises ValueError if not an IP
        except Exception as exc:  # noqa: BLE001 - try the next endpoint
            kind, detail = _classify(exc)
            attempts.append(
                _Attempt(url, time.monotonic() - started, kind, detail))
            continue

        if any(parsed in net for net in _NOT_ROUTABLE):
            # A well-formed answer that cannot be an egress IP. Recorded as a
            # failed attempt rather than returned, because until 2026-09-02 it
            # WAS returned: a proxy that had stopped routing answered `10.x.x.x`
            # here, sailed through, and failed two steps later as "not present
            # in the geoip database" - which blames the database for a proxy
            # that was not proxying.
            attempts.append(_Attempt(
                url, time.monotonic() - started, "not_routing",
                f"{ip} is not a routable public address"))
            continue
        return ip

    spent = budget - (deadline - time.monotonic())
    tried = len(attempts)
    total = len(_IP_ECHO_ENDPOINTS)

    proxied = bool(proxies)
    kinds = {a.kind for a in attempts}
    if not attempts:
        # NOT "mixed". Nothing failed for differing reasons, because nothing ran
        # at all - the budget was already spent when this step started. The old
        # code fell through to "mixed", so both the prose and `.kind`, which the
        # class docstring tells callers to branch on instead of the prose, said
        # the endpoints disagreed while promising a list that was empty.
        kind = "no_endpoint_tried"
    elif len(kinds) == 1:
        kind = kinds.pop()
    else:
        kind = "mixed"
    through = "through the proxy" if proxied else "directly (no proxy set)"

    lines = [
        f"could not discover the egress IP {through}: {_remedy(kind, proxied)}",
        f"tried {tried} of {total} endpoint(s) in {spent:.1f}s of a {budget:g}s budget",
    ]
    lines.extend(a.line() for a in attempts)
    if tried < total:
        # The signature of the pathology fixed on 2026-08-10, kept visible so a
        # regression is legible in the message instead of needing a bisection:
        # one endpoint eating the whole budget means the other two never ran, so
        # the redundancy that is supposed to cover a single outage never applies.
        lines.append(
            f"  NOTE: the budget ran out with {total - tried} endpoint(s) never "
            f"tried, so the redundancy never came into play")
    raise GeoTimezoneError("\n".join(lines), kind=kind, attempts=tuple(attempts))


def _geo_record(ip: str, mmdb_path: Any) -> "Optional[Dict[str, Any]]":
    """L'UNICO punto che apre il database e legge un record.

    Le tre funzioni qui sotto - fuso, locale e coordinate - leggono lo STESSO
    record dello STESSO IP, e prima di questa funzione due di loro ripetevano
    le stesse tre righe. Aggiungere la terza avrebbe fatto tre copie di come si
    legge il database, che e' la regola 16 violata mentre la si applica.

    Torna ``None`` se l'IP non c'e': chi chiama decide se e' fatale (il fuso,
    che dietro un proxy deve fallire rumorosamente) o no (il locale, che ha un
    ripiego dichiarato).
    """
    import maxminddb

    with maxminddb.open_database(str(mmdb_path)) as reader:
        record = reader.get(ip)
    return record if isinstance(record, dict) else None


def ip_to_coordinates(ip: str, mmdb_path: Any) -> "tuple[float, float]":
    """Map ``ip`` -> (latitudine, longitudine) dallo stesso record del fuso.

    ⛔ E' la fonte UNICA della posizione dichiarata: il motore non chiede piu'
    niente all'hardware (niente WiFi, niente GPS, niente cella) e non chiede
    niente a Google. La posizione esce dall'IP di uscita del proxy, esattamente
    come il fuso e la lingua, quindi le tre cose non possono contraddirsi.

    ⛔ LA PRECISIONE NON VIENE DA QUI, e non e' una dimenticanza: misurato il
    2026-08-20 su un record vero, ``location`` porta ``latitude``,
    ``longitude`` e ``time_zone`` e **non** ``accuracy_radius``. Dichiararla e'
    un'altra decisione, e vive nel Profile: una posizione derivata da un IP con
    una precisione da GPS sarebbe incoerente per costruzione.

    Solleva :class:`GeoTimezoneError` - stessa classe del fuso, perche' e' lo
    stesso guasto - se l'IP manca o il record non porta le coordinate. **Non si
    inventa un ripiego**: senza dichiarazione il motore rifiuta, che e' la
    regola 7.
    """
    record = _geo_record(ip, mmdb_path)
    if not record:
        raise GeoTimezoneError(
            f"egress IP {ip} not present in the geoip database",
            kind="ip_not_in_db")
    loc = record.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if lat is None or lon is None:
        raise GeoTimezoneError(
            f"no coordinates for egress IP {ip} in the geoip database",
            kind="no_coordinates_for_ip")
    return float(lat), float(lon)


def ip_to_timezone(ip: str, mmdb_path: Any) -> str:
    """Map ``ip`` to its IANA timezone using the offline mmdb.

    Reads the standard MaxMind ``location.time_zone`` field and validates it
    against the system tz database. Raises :class:`GeoTimezoneError` if the IP
    is absent from the DB or the zone is missing / not a valid IANA name.
    """
    record = _geo_record(ip, mmdb_path)
    if not record:
        raise GeoTimezoneError(
            f"egress IP {ip} not present in the geoip database",
            kind="ip_not_in_db")
    tz = (record.get("location") or {}).get("time_zone")
    if not tz:
        raise GeoTimezoneError(
            f"no timezone for egress IP {ip} in the geoip database",
            kind="no_timezone_for_ip")
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise GeoTimezoneError(
            f"geoip returned an invalid IANA zone {tz!r} for {ip}: {exc}",
            kind="invalid_timezone") from exc
    return tz


# ISO 3166 country code -> the primary BCP-47 locale a real Windows machine in that
# country most commonly runs. Multi-language countries use the majority language; the
# user can always force a specific locale instead of "auto". Unknown -> en-US.
_COUNTRY_LOCALE = {
    "US": "en-US", "GB": "en-GB", "CA": "en-CA", "AU": "en-AU", "NZ": "en-NZ", "IE": "en-IE",
    "ZA": "en-ZA", "IN": "en-IN", "SG": "en-SG", "PH": "en-PH",
    "FR": "fr-FR", "BE": "fr-BE", "LU": "fr-LU",
    "DE": "de-DE", "AT": "de-AT", "CH": "de-CH",
    "IT": "it-IT", "ES": "es-ES", "PT": "pt-PT", "NL": "nl-NL",
    "SE": "sv-SE", "NO": "nb-NO", "DK": "da-DK", "FI": "fi-FI", "IS": "is-IS",
    "PL": "pl-PL", "CZ": "cs-CZ", "SK": "sk-SK", "HU": "hu-HU", "RO": "ro-RO",
    "GR": "el-GR", "BG": "bg-BG", "HR": "hr-HR", "RS": "sr-RS", "SI": "sl-SI",
    "RU": "ru-RU", "UA": "uk-UA", "TR": "tr-TR", "IL": "he-IL",
    "BR": "pt-BR", "MX": "es-MX", "AR": "es-AR", "CL": "es-CL", "CO": "es-CO", "PE": "es-PE",
    "JP": "ja-JP", "KR": "ko-KR", "CN": "zh-CN", "TW": "zh-TW", "HK": "zh-HK",
    "ID": "id-ID", "TH": "th-TH", "VN": "vi-VN", "MY": "ms-MY",
    "SA": "ar-SA", "AE": "ar-AE", "EG": "ar-EG",
}


#: The EEA plus the UK and Switzerland, i.e. every country where a real Google
#: CONSENT cookie carries `<lang>+<COUNTRY>` rather than the `en+FX` a non-EU
#: visitor gets. A finite, knowable set; the alternative was a 22-row timezone
#: table in the wrapper that silently answered "non-EU English" for every
#: country it did not list.
CONSENT_REGION_COUNTRIES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",            # EU 27
    "IS", "LI", "NO",            # EEA
    "GB", "CH",                  # UK and Switzerland behave the same way here
})


def consent_region_lang(locale: str) -> "tuple[str, str]":
    """`(region_token, lang)` for a Google CONSENT cookie, from the LOCALE.

    WHY IT TAKES A LOCALE. This used to live in the wrapper as a 22-row IANA
    timezone table (`_TZ_TO_REGION`), while the locale a session actually runs
    with is resolved HERE, from the egress country, against a 55-row table. Two
    tables for one fact, and they drifted exactly the way two tables do: a
    Romanian session resolved `ro-RO` for `navigator.language` and fell through
    to `("FX", "en")` for the cookie, because `Europe/Bucharest` was not one of
    the 22. A page that reads the cookie and the language sees a Romanian
    browser claiming to be a non-EU English one.

    Deriving from the locale removes the second table rather than extending it:
    every locale this package can produce is covered by construction, including
    the ones nobody has added to a list yet.
    """
    tag = (locale or "en-US").replace("_", "-")
    parts = tag.split("-")
    lang = parts[0].lower()
    country = parts[-1].upper() if len(parts) > 1 else ""
    if country in CONSENT_REGION_COUNTRIES:
        return (country, lang)
    return ("FX", lang if country else "en")


def ip_to_locale(ip: str, mmdb_path: Any) -> str:
    """Map ``ip`` -> a BCP-47 locale via the MaxMind ``country.iso_code`` field, so the
    browser language stays consistent with the proxy egress country. Falls back to
    ``en-US`` for IPs absent from the DB or countries we don't map."""
    record = _geo_record(ip, mmdb_path)
    cc = ((record.get("country") or {}).get("iso_code") or "") if record else ""
    return _COUNTRY_LOCALE.get(cc.upper(), "en-US")


def resolve_session_locale(egress_ip: Optional[str], proxy: Optional[Dict[str, str]]) -> str:
    """Resolve ``locale="auto"`` to a BCP-47 locale from the egress country. Behind a proxy
    it reuses the already-discovered ``egress_ip`` (no extra round-trip); without a proxy it
    discovers the host's public IP. On any failure it returns ``en-US`` (never breaks launch
    - locale is cosmetic, unlike timezone which traps a foreign-proxy mismatch)."""
    from .download import ensure_geoip_mmdb

    try:
        # ⛔ SI RIUSA CIO' CHE IL CHIAMANTE HA GIA': `prepare_session_geo` ha
        # gia' pagato questo round-trip e adesso porta il risultato anche senza
        # proxy. Si scopre solo se non c'e' niente da riusare - il caso di un
        # fuso esplicito, in cui nessuno ha ancora chiesto niente alla rete.
        #
        # E la scoperta resta VIETATA dietro un proxy: se li' `egress_ip` manca,
        # la scoperta e' fallita, e cadere sull'indirizzo diretto derivarebbe la
        # lingua dal paese di CASA mentre il fuso dice quello del proxy. Meglio
        # `en-US` di una contraddizione fra due campi.
        ip = egress_ip
        if ip is None and not _proxy_is_set(proxy):
            ip = discover_egress_ip(None)
        if ip is None:
            _warn_locale_fallback(proxy, "no egress IP was resolved")
            return "en-US"
        return ip_to_locale(ip, ensure_geoip_mmdb())
    except Exception as exc:  # noqa: BLE001
        _warn_locale_fallback(proxy, f"{type(exc).__name__}: {exc}")
        return "en-US"


def _warn_locale_fallback(proxy: Optional[Dict[str, str]], why: str) -> None:
    """Say that the locale was NOT resolved, on stderr, every time.

    This used to be two bare returns. The docstring called locale "cosmetic",
    and for a lone session it nearly is - but the timezone is resolved from the
    SAME egress IP and does not fall back, so a failure here produces a session
    whose timezone says one country and whose language says the United States.
    That pairing is a cross-field inconsistency of exactly the kind the
    timezone trap exists to prevent, and it was reaching users with no signal
    at all.

    The OUTCOME is deliberately unchanged: raising here would break launches
    that work today, on a field that is recoverable by passing `locale=`
    explicitly. What changes is that it stops being invisible - an absent
    signal must be loud, never silent.
    """
    import sys

    where = "behind a proxy" if _proxy_is_set(proxy) else "with no proxy"
    # The cause goes AFTER the sentence, indented, rather than inside a
    # parenthesis in the middle of it. `why` became multi-line on 2026-09-02 when
    # the egress failure started listing one line per endpoint, and spliced into
    # the middle it left the warning's own advice glued to the last endpoint line,
    # where neither a reader nor a log grep expects to find it.
    detail = "\n".join("    " + line for line in why.split("\n"))
    print(
        f"invisible-core: could not resolve the session locale {where}; "
        f"falling back to en-US. The timezone is still resolved from the egress "
        f"IP, so this session may pair a non-US timezone with a US language - "
        f"pass locale=\"xx-XX\" to set it explicitly.\n"
        f"{detail}",
        file=sys.stderr,
    )


class SessionGeo(NamedTuple):
    """Geo facts resolved once per session from a single egress round-trip.

    ``timezone`` follows the precedence in the module docstring.

    ⛔ ``egress_ip`` E' UN FATTO, NON UNA DECISIONE: l'indirizzo da cui questa
    sessione esce davvero, scoperto una volta, con o senza proxy. Se poi quel
    valore venga DICHIARATO al motore come srflx lo decide
    :meth:`srflx_da_dichiarare`, e la risposta senza proxy e' no.

    Fino al 2026-08-26 questo campo valeva ``None`` senza proxy, e il "no" era
    espresso proprio da quel ``None``. Un campo solo per due significati:
    l'effetto era che ``prepare_session_geo`` scopriva l'indirizzo per il fuso,
    **lo buttava via**, e ``resolve_session_locale`` doveva riscoprirlo. Due
    richieste identiche a un servizio esterno, dall'indirizzo vero, prima che il
    browser esista - dove un utente vero ne fa zero. Il fatto adesso si porta,
    e il "no" vive dove viveva gia' la decisione.
    """

    timezone: str
    egress_ip: Optional[str]
    #: La posizione dichiarata, dallo STESSO record dell'IP di uscita da cui
    #: escono fuso e lingua. ``None`` quando non c'e' un IP da cui derivarla
    #: (nessun proxy, o scoperta fallita): in quel caso il motore NON riceve
    #: nessuna dichiarazione e rifiuta, invece di chiedere all'hardware.
    #:
    #: Hanno un default perche' ``SessionGeo`` si costruisce per posizione con
    #: due argomenti in sei punti fra codice e test: aggiungerli senza default
    #: sarebbe stato un cambiamento non retrocompatibile di un tipo esportato.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    #: ⛔ UN INTERRUTTORE, E IL SUO DEFAULT E' QUELLO PRUDENTE.
    #:
    #: La prima stesura era un campo che portava l'INDIRIZZO da dichiarare, con
    #: default ``None``. Sbagliato, e un test lo ha colto subito: ``SessionGeo``
    #: si costruisce per posizione in sei punti fra codice e test, e tutti
    #: quelli che non conoscevano il campo nuovo hanno smesso di dichiarare il
    #: srflx **per distrazione**. Cioe' il default cadeva dal lato che porta al
    #: messaggio peggiore che un rilevatore possa scrivere.
    #:
    #: Invertito: ``False`` significa "dichiara", che e' il comportamento di
    #: sempre, e per spegnerlo bisogna dirlo. Vale ``True`` SOLO quando l'uscita
    #: ha UDP dimostrato E coerente, perche' li' il srflx vero nasce gia' con
    #: l'indirizzo giusto e dichiararne uno aggiungerebbe un candidato senza
    #: allocazione corrispondente.
    srflx_soppresso: bool = False

    def srflx_da_dichiarare(self) -> Optional[str]:
        """L'indirizzo che il motore deve annunciare come srflx, o ``None``.

        ⛔ E' L'UNICO POSTO in cui questa domanda riceve risposta. I due
        costruttori di env - ``launch.build_launch_env`` nel core e
        ``_session.build_env`` nel wrapper - la chiamano, non la ricalcolano:
        erano gia' due punti di atterraggio, e la stessa regola scritta due
        volte avrebbe potuto divergere.
        """
        return None if self.srflx_soppresso else self.egress_ip


def _srflx_soppresso(proxy: Optional[Dict[str, str]],
                     egress_ip: Optional[str]) -> bool:
    """Dichiarare un srflx sintetico, o lasciar passare quello vero?

    Il criterio viene dal codice di un rilevatore vero, letto e non dedotto
    (`docs_research/scrapfly-re/00-WEBRTC-LEAK.md`): la sua configurazione non
    contiene nessuno STUN, solo TURN, e la username dell'allocazione e' lo
    stesso identificativo che il POST di verifica manda al backend. Quindi in un
    browser onesto **un srflx puo' venire soltanto da un'allocazione riuscita**,
    e candidato lato client e prova lato server coincidono per costruzione. E'
    quell'implicazione che un candidato dichiarato rompe.

    Da qui la regola, che ha tre rami:

    * **nessun proxy**: lo STUN vero risponde con l'indirizzo da cui si esce
      davvero, quindi il srflx nasce gia' giusto e con la sua allocazione. Non
      si dichiara niente. Fino al 2026-08-26 questo ramo non era scritto qui:
      lo stesso esito usciva dal fatto che ``egress_ip`` valesse ``None`` senza
      proxy, cioe' la decisione era codificata nell'ASSENZA di un fatto. Le due
      cose sono ora separate, e questo ramo dice a voce cio' che prima si
      otteneva per effetto collaterale.
    * **UDP dimostrato e coerente** (l'UDP esce dallo stesso indirizzo del TCP):
      il srflx vero nascera' gia' con l'indirizzo giusto. Non si dichiara niente:
      dichiarare aggiungerebbe un candidato senza allocazione corrispondente.
    * **tutto il resto**: si dichiara l'IP di uscita, che e' il comportamento di
      sempre. Senza un srflx il rilevatore scrive *"Javascript is manipulated"*,
      cioe' accusa il browser; con lui scrive *"VPN/PROXY detected"*, cioe'
      accusa la rete. La differenza fra i due messaggi e' questa riga.

    ⛔ E LA SONDA NON PUO' FAR FALLIRE UN LANCIO. Qualunque cosa vada storta -
    rete, timeout, un campo che non c'e' - si ricade sul ramo prudente. Una
    capacita' si sfrutta solo quando e' DIMOSTRATA.
    """
    if not egress_ip:
        return False  # niente da dichiarare comunque: il ramo prudente
    if not _proxy_is_set(proxy):
        return True  # connessione diretta: il srflx vero e' gia' la verita'
    # ⛔ PRIMA DELLA SONDA, perche' se questo e' falso la sonda non serve.
    #
    # Che l'USCITA porti UDP coerente non basta: deve anche essere il BROWSER a
    # mandarci l'UDP. Con `network.proxy.socks_remote_udp` spenta l'UDP scavalca
    # il proxy, quindi un srflx vero nascerebbe con l'indirizzo di CASA - e
    # smettere di dichiarare, li', sarebbe una fuga vera invece di un rimedio.
    #
    # La prima stesura di questa funzione, il 2026-08-25, non lo controllava. Il
    # ramo era irraggiungibile per fortuna (nessun fornitore ha UDP usabile) e
    # non per costruzione, che e' esattamente la forma di difetto che questo
    # progetto paga: una condizione la cui sicurezza dipende da un fatto che non
    # verifica.
    from ._proxy import INSTRADIAMO_UDP_NEL_SOCKS
    if not INSTRADIAMO_UDP_NEL_SOCKS:
        return False
    try:
        from ._capacita import capacita
        c = capacita(proxy, uscita_tcp_nota=egress_ip)
    except Exception:  # noqa: BLE001
        return False
    return c.get("udp") is True and c.get("udp_coerente") is True


def _geoip_database(ip: str, proxied: bool) -> Any:
    """The mmdb, or an error saying the DATABASE failed and not the network.

    The distinction this exists to make: acquiring the database and looking an
    address up in it are separate failures with separate remedies, and until
    2026-09-02 the acquisition sat inside the same expression as the lookup,
    under one blanket `except`. A failed download therefore reached the caller
    wearing the face of a proxy that would not answer, and the reflex it invited
    was to go and check the proxy, which was working.

    Both TIMEZONE resolvers go through here rather than keeping a copy each.
    `_coordinate` in `prepare_session_geo` still acquires the database on its own
    line, and that is deliberate: coordinates are best-effort and swallow their
    own failure, so routing them through a helper that RAISES would turn a
    tolerated gap into a refused launch.

    `proxied` selects the advice, and is passed rather than recomputed: the
    caller has already decided it with `_proxy_is_set`, and deciding it twice is
    the duplication that rule 16 names.
    """
    from .download import ensure_geoip_mmdb

    try:
        return ensure_geoip_mmdb()
    except Exception as exc:  # noqa: BLE001
        found = (f"the egress IP is {ip}, so the network path is fine, but "
                 if proxied else f"the egress IP is {ip}, but ")
        raise GeoTimezoneError(
            f"{found}{_remedy('geoip_unavailable', proxied)}: {exc}",
            kind="geoip_unavailable") from exc


def prepare_session_geo(
    timezone: str, proxy: Optional[Dict[str, str]]
) -> SessionGeo:
    """Resolve the session timezone AND the proxy egress IP in ONE round-trip.

    The egress IP is discovered once and reused for both the timezone mapping
    (when ``timezone`` is ``""``/``"auto"``) and the WebRTC public-IP override.
    Timezone precedence is identical to :func:`resolve_session_timezone`; the
    egress IP is best-effort for the WebRTC side (a discovery failure that the
    timezone path doesn't need won't break the launch - but if the timezone
    path *does* need it behind a proxy, that path still fails loudly).
    """
    from .download import ensure_geoip_mmdb

    tz = (timezone or "").strip()
    proxy_set = _proxy_is_set(proxy)

    # One discovery, reused below. Behind a proxy we always want the egress IP
    # (for WebRTC) regardless of the timezone setting.
    egress_ip: Optional[str] = None
    egress_err: Optional[Exception] = None
    if proxy_set:
        try:
            egress_ip = discover_egress_ip(proxy)
        except Exception as exc:  # noqa: BLE001
            egress_err = exc

    # Timezone resolution - same precedence as resolve_session_timezone.
    def _coordinate(ip: "Optional[str]") -> "tuple[Optional[float], Optional[float]]":
        """Le coordinate sono BEST-EFFORT, il fuso no, e la differenza e' voluta.

        Un fuso sbagliato dietro un proxy e' la trappola `tz_mismatch` e deve
        far fallire il lancio. Una posizione ASSENTE invece non e' una
        contraddizione: e' un browser a cui nessuno ha ancora chiesto dove sia,
        e il motore la rifiuta in modo pulito. Far fallire il lancio per questo
        sarebbe piu' fragile senza essere piu' fedele.
        """
        if not ip:
            return None, None
        try:
            return ip_to_coordinates(ip, ensure_geoip_mmdb())
        except Exception:  # noqa: BLE001
            return None, None

    if tz and tz.lower() != "auto":
        lat, lon = _coordinate(egress_ip)
        return SessionGeo(tz, egress_ip, lat, lon,
                          _srflx_soppresso(proxy, egress_ip))  # explicit IANA wins
    try:
        ip = egress_ip if proxy_set else discover_egress_ip(None)
        if ip is None:  # proxy set but discovery failed above
            raise egress_err or GeoTimezoneError("egress IP discovery failed")
        lat, lon = _coordinate(ip)
        # ⛔ SI PORTA `ip`, NON `egress_ip`. Dietro un proxy sono lo stesso
        # valore; senza, `ip` e' il fatto che questo giro di rete ha appena
        # pagato e `egress_ip` e' `None`. Portare il secondo significava
        # buttare la scoperta e farla rifare a `resolve_session_locale`.
        return SessionGeo(ip_to_timezone(ip, _geoip_database(ip, proxy_set)), ip, lat, lon,
                          _srflx_soppresso(proxy, ip))
    except Exception:
        if proxy_set:
            raise  # fail-early behind a proxy (timezone_mismatch trap)
        return SessionGeo("", None)  # no proxy: host TZ is a safe fallback


def resolve_session_timezone(
    timezone: str, proxy: Optional[Dict[str, str]]
) -> str:
    """Map the user's ``timezone`` setting to a concrete IANA zone (or ``""``).

    Timezone-only path (no WebRTC side effects): an explicit IANA zone wins and
    triggers NO network call; ``""``/``"auto"`` resolve from the egress IP. The
    launch path uses :func:`prepare_session_geo` instead (which additionally
    returns the egress IP for WebRTC); this standalone resolver is kept for
    third-party integrations that only want the zone. See the module docstring
    for the precedence table.
    """
    tz = (timezone or "").strip()
    if tz and tz.lower() != "auto":
        return tz  # explicit IANA wins - no egress lookup

    proxy_set = _proxy_is_set(proxy)
    try:
        ip = discover_egress_ip(proxy if proxy_set else None)
        return ip_to_timezone(ip, _geoip_database(ip, proxy_set))
    except Exception:
        if proxy_set:
            raise  # fail-early behind a proxy (timezone_mismatch trap)
        return ""  # no proxy: host TZ is a safe fallback
