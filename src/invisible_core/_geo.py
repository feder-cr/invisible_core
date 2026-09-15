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
            # to the CONNECT phase and then again to the READ phase, so
            # `timeout=10` can spend twenty seconds in a single call and blow a
            # budget of fifteen on its own. Measured 2026-08-10 with a proxy that
            # had stopped routing: the error reported `20.1s` against
            # `budget=15` and "1 of 3 endpoints" - the first one ate everything
            # and the redundancy of the other two never came into play. The
            # function's comment already described the right intent - the budget
            # bounds the whole step - and it was the code implementing a
            # different one.
            #
            # Half per phase guarantees that ONE call cannot exceed what is left,
            # so the loop really does reach the second and third endpoint when
            # the first goes quiet.
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
    """The ONE place that opens the database and reads a record.

    The three functions below - timezone, locale and coordinates - read the SAME
    record of the SAME IP, and before this function two of them repeated the same
    three lines. Adding the third would have made three copies of how the
    database is read, which is rule 16 broken while applying it.

    Returns ``None`` when the IP is not there: the caller decides whether that is
    fatal (the timezone, which behind a proxy must fail loudly) or not (the
    locale, which has a declared fallback).
    """
    import maxminddb

    with maxminddb.open_database(str(mmdb_path)) as reader:
        record = reader.get(ip)
    return record if isinstance(record, dict) else None


def ip_to_coordinates(ip: str, mmdb_path: Any) -> "tuple[float, float]":
    """Map ``ip`` -> (latitude, longitude) from the same record as the timezone.

    ⛔ It is the ONE source of the declared position: the engine no longer asks
    the hardware anything (no WiFi, no GPS, no cell) and asks Google nothing. The
    position comes out of the proxy's exit IP, exactly as the timezone and the
    language do, so the three cannot contradict one another.

    ⛔ THE ACCURACY DOES NOT COME FROM HERE, and that is not an oversight:
    measured 2026-08-20 on a real record, ``location`` carries ``latitude``,
    ``longitude`` and ``time_zone`` and **not** ``accuracy_radius``. Declaring it
    is a separate decision and lives in the Profile: a position derived from an
    IP with GPS-grade accuracy would be incoherent by construction.

    Raises :class:`GeoTimezoneError` - the same class as the timezone, because it
    is the same failure - when the IP is missing or the record carries no
    coordinates. **No fallback is invented**: without a declaration the engine
    refuses, which is rule 7.
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
        # ⛔ WHAT THE CALLER ALREADY HAS IS REUSED: `prepare_session_geo` has
        # already paid this round trip and now carries the result even without a
        # proxy. Discovery happens only when there is nothing to reuse - the case
        # of an explicit timezone, where nobody has asked the network anything
        # yet.
        #
        # And discovery stays FORBIDDEN behind a proxy: if `egress_ip` is missing
        # there, discovery has failed, and falling back to the direct address
        # would derive the language from the HOME country while the timezone says
        # the proxy's. `en-US` is better than a contradiction between two fields.
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

    ⛔ ``egress_ip`` IS A FACT, NOT A DECISION: the address this session really
    leaves from, discovered once, with or without a proxy. Whether that value is
    then DECLARED to the engine as an srflx is decided by
    :meth:`srflx_to_declare`, and without a proxy the answer is no.

    Until 2026-08-26 this field was ``None`` without a proxy, and the "no" was
    expressed by that very ``None``. One field for two meanings: the effect was
    that ``prepare_session_geo`` discovered the address for the timezone, **threw
    it away**, and ``resolve_session_locale`` had to discover it again. Two
    identical requests to an external service, from the real address, before the
    browser exists - where a real user makes zero. The fact is carried now, and
    the "no" lives where the decision already lived.
    """

    timezone: str
    egress_ip: Optional[str]
    #: The declared position, from the SAME record of the exit IP the timezone
    #: and the language come from. ``None`` when there is no IP to derive it from
    #: (no proxy, or discovery failed): in that case the engine receives NO
    #: declaration and refuses, rather than asking the hardware.
    #:
    #: They have defaults because ``SessionGeo`` is built positionally with two
    #: arguments in six places across code and tests: adding them without
    #: defaults would have been a breaking change to an exported type.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    #: ⛔ A SWITCH, AND ITS DEFAULT IS THE CAUTIOUS ONE.
    #:
    #: The first draft was a field carrying the ADDRESS to declare, defaulting to
    #: ``None``. Wrong, and a test caught it immediately: ``SessionGeo`` is built
    #: positionally in six places across code and tests, and everyone who did not
    #: know about the new field stopped declaring the srflx **by accident**. That
    #: is, the default fell on the side that leads to the worst sentence a
    #: detector can write.
    #:
    #: Inverted: ``False`` means "declare", which is the behaviour it has always
    #: had, and switching it off has to be said. It is ``True`` ONLY when the exit
    #: has UDP both demonstrated AND coherent, because there the real srflx is
    #: already born with the right address and declaring one would add a
    #: candidate with no matching allocation.
    srflx_suppressed: bool = False

    def srflx_to_declare(self) -> Optional[str]:
        """The address the engine must announce as its srflx, or ``None``.

        ⛔ THE ONE PLACE where this question is answered. The two env builders -
        ``launch.build_launch_env`` in the core and ``_session.build_env`` in the
        wrapper - call it rather than recomputing it: they were already two
        landing points, and the same rule written twice could have drifted.
        """
        return None if self.srflx_suppressed else self.egress_ip


def _srflx_suppressed(proxy: Optional[Dict[str, str]],
                     egress_ip: Optional[str]) -> bool:
    """Declare a synthetic srflx, or let the real one through?

    The criterion comes from a real detector's code, read rather than deduced:
    its configuration contains no STUN at all, only TURN, and the allocation's
    username is the same identifier its verification POST sends to the backend.
    So in an honest browser **an srflx can only come from a successful
    allocation**, and the client-side candidate and the server-side proof
    coincide by construction. It is that implication a declared candidate
    breaks.

    Da qui la regola, che ha tre rami:

    * **no proxy**: the real STUN answers with the address traffic actually
      leaves from, so the srflx is born correct and with its own allocation.
      Nothing is declared. Until 2026-08-26 this branch was not written here: the
      same outcome fell out of ``egress_ip`` being ``None`` without a proxy - the
      decision was encoded in the ABSENCE of a fact. The two are separated now,
      and this branch says out loud what used to be obtained as a side effect.
    * **UDP demonstrated and coherent** (UDP leaves from the same address as
      TCP): the real srflx will already be born with the right address. Nothing
      is declared: declaring would add a candidate with no matching allocation.
    * **everything else**: the exit IP is declared, which is the behaviour it has
      always had. Without an srflx the detector writes *"Javascript is
      manipulated"*, which accuses the browser; with one it writes *"VPN/PROXY
      detected"*, which accuses the network. The difference between those two
      sentences is this line.

    ⛔ AND THE PROBE CANNOT FAIL A LAUNCH. Whatever goes wrong - network, a
    timeout, a field that is not there - falls back to the cautious branch. A
    capability is only exploited once it is DEMONSTRATED.
    """
    if not egress_ip:
        return False  # nothing to declare anyway: the cautious branch
    if not _proxy_is_set(proxy):
        return True  # direct connection: the real srflx already IS the truth
    # ⛔ BEFORE THE PROBE, because if this is false the probe is pointless.
    #
    # That the EXIT carries coherent UDP is not enough: the BROWSER has to be the
    # one sending us the UDP. With `network.proxy.socks_remote_udp` off, UDP goes
    # around the proxy, so a real srflx would be born with the HOME address - and
    # to stop declaring there would be a real leak instead of a remedy.
    #
    # The first draft of this function, on 2026-08-25, did not check it. The
    # branch was unreachable by luck (no provider has usable UDP) and not by
    # construction, which is exactly the shape of defect this project pays for: a
    # condition whose safety rests on a fact it does not verify.
    from ._proxy import UDP_GOES_THROUGH_SOCKS
    if not UDP_GOES_THROUGH_SOCKS:
        return False
    try:
        from ._capability import capability
        c = capability(proxy, known_tcp_exit=egress_ip)
    except Exception:  # noqa: BLE001
        return False
    return c.get("udp") is True and c.get("udp_matches_tcp") is True


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
        """Coordinates are BEST-EFFORT, the zone is not, and the gap is meant.

        A wrong timezone behind a proxy is the `tz_mismatch` trap and has to fail
        the launch. A MISSING position, on the other hand, is not a
        contradiction: it is a browser nobody has asked where it is yet, and the
        engine refuses it cleanly. Failing a launch over that would be more
        brittle without being more faithful.
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
                          _srflx_suppressed(proxy, egress_ip))  # explicit IANA wins
    try:
        ip = egress_ip if proxy_set else discover_egress_ip(None)
        if ip is None:  # proxy set but discovery failed above
            raise egress_err or GeoTimezoneError("egress IP discovery failed")
        lat, lon = _coordinate(ip)
        # ⛔ IT CARRIES `ip`, NOT `egress_ip`. Behind a proxy they are the same
        # value; without one, `ip` is the fact this round-trip has just paid for
        # and `egress_ip` is `None`. Carrying the second meant throwing the
        # discovery away and making `resolve_session_locale` do it again.
        return SessionGeo(ip_to_timezone(ip, _geoip_database(ip, proxy_set)), ip, lat, lon,
                          _srflx_suppressed(proxy, ip))
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
