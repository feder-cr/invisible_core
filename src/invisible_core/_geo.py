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

On failure: raise, with a proxy or without one. "auto" promises the zone of
the egress, and the success path resolves it from the egress IP even without a
proxy BECAUSE the host's zone can be wrong: a server kept in UTC whose public
address is in another country. Until 47.41.0 a failure without a proxy fell
back to the host's zone in silence - offline, or a GeoIP database that could
not be written (invisible_dots, a read-only cache) - which handed exactly that
server a zone contradicting its address, with no warning. The error names the
cause and the remedy: an explicit IANA zone, or a writable cache.
"""
from __future__ import annotations

import contextlib
import ipaddress
import re
import socket
import threading
import time
from typing import Any, Dict, NamedTuple, Optional, Sequence
from urllib.parse import quote
# At import, as in prefs.py: zoneinfo resolves its search path from the real
# platform the first time it is imported.
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
import urllib3.util.connection as _u3conn

from . import _udp_exit
from ._geoposition import DeclaredPosition, position_to_declare
from ._locale import SessionLocale, _decide


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

# Echo endpoints that answer over IPv6 ONLY (an AAAA record and no A record). Used
# for one question after the IPv4 echo could not connect: does this host have
# IPv6 and no IPv4 route?
_IPV6_ECHO_ENDPOINTS = (
    "https://api6.ipify.org",
    "https://ipv6.icanhazip.com",
)
_IPV6_PROBE_BUDGET = 6.0

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
    # `{install}` is the one command in this table, and it is not written here:
    # `_remedy` fills it in for the tool that installed the core, because the
    # environment the README makes has no pip (`installer.py`).
    "socks_missing": (
        "a socks:// proxy needs PySocks - {install}",
        "a socks:// proxy needs PySocks - {install}"),
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
    # Never paired with a proxy: with one, the exit is the proxy's and the host's
    # own address family does not matter.
    "ipv6_only_no_proxy": (
        "an IPv6-only host without a proxy is not supported: this host reaches the "
        "internet only over IPv6, and a browser session here uses IPv4, so it would "
        "have no connectivity. Use a proxy, or run on a host that has IPv4",
        "an IPv6-only host without a proxy is not supported: this host reaches the "
        "internet only over IPv6, and a browser session here uses IPv4, so it would "
        "have no connectivity. Use a proxy, or run on a host that has IPv4"),
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
    "geoip_unreadable": (
        "the geoip database is on disk but could not be opened or read - a "
        "damaged file or a path the reader cannot open, NOT a proxy problem",
        "the geoip database is on disk but could not be opened or read - a "
        "damaged file or a path the reader cannot open"),
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
    text = pair[0] if proxied else pair[1]
    if "{install}" in text:
        from .installer import install
        from .pin import CORE_NAME, format_command
        text = text.replace(
            "{install}", format_command(install(CORE_NAME, "requests[socks]")))
    return text


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
        return "endpoint_not_an_ip", f"reply was not an IP ({exc})"

    if any(token in text for token in _DNS_TOKENS):
        return "dns_failure", "a hostname did not resolve"

    # ORDER 2: before the ConnectionError arm, which SSLError subclasses. Without
    # this a certificate failure - the signature of the TLS-intercepting captive
    # portal this classification exists to separate - was reported as a dead TCP
    # port, sending the reader to check a host:port that was answering fine.
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls_failed", f"TLS failed ({exc})"

    # ORDER 3: before the ConnectionError arm, which ProxyError also subclasses.
    # ReadTimeout is not a ConnectionError, but it is kept adjacent so the three
    # ordered branches read as one group.
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "read_timeout", "connected, then no reply before the deadline"

    if isinstance(exc, (requests.exceptions.ProxyError,
                        requests.exceptions.ConnectTimeout,
                        requests.exceptions.ConnectionError)):
        return "connect_failed", "could not open a connection"

    # ⛔ THE CAUSE WHOLE, in this branch and the two above: until 41.36.0 they
    # stopped at 70 and 110 characters, and a diagnosis exists to be read for
    # its cause, which an exception chain names at its end. No constraint asked
    # for the cut (D87: a cap only where one does). The proxy's credentials,
    # which a longer text could carry, are taken out by `_without_credentials`
    # where the attempt is recorded - a cut was never what kept them out.
    return "unknown", repr(exc)


def _without_credentials(text: str, proxy: Optional[Dict[str, str]]) -> str:
    """`text` with the proxy's password, and its `user:password@`, as `***`.

    The proxy URL handed to requests carries the credentials
    (`_proxies_for_requests`), and an exception may quote that URL back. Both
    spellings are taken out, as typed and as the URL quotes them."""
    if not proxy:
        return text
    user = proxy.get("username") or ""
    pwd = proxy.get("password") or ""
    for u, w in ((user, pwd), (quote(user, safe=""), quote(pwd, safe=""))):
        if u or w:
            text = text.replace(f"{u}:{w}@", "***@")
        if w:
            text = text.replace(w, "***")
    return text


def _proxy_is_set(proxy: Optional[Dict[str, str]]) -> bool:
    if not proxy:
        return False
    server = (proxy.get("server") or "").strip()
    return bool(server) and server.lower() != "direct://"


def _proxies_for_requests(proxy: Dict[str, str]) -> Dict[str, str]:
    """Translate our proxy dict into a ``requests`` proxies mapping.

    Every SOCKS scheme maps to the spelling of the library that sends the NAME to
    the proxy: ``socks5h`` for SOCKS5 and ``socks4a`` for SOCKS4 (urllib3 /
    PySocks ``rdns=True``). The plain ``socks5`` and ``socks4`` spellings resolve
    the target on THIS host, which would put the echo hostnames on the host's
    resolver before the browser exists. HTTP/HTTPS pass through unchanged.
    Credentials are URL-encoded.
    """
    server = (proxy.get("server") or "").strip()
    low = server.lower()
    given = low.split("://", 1)[0] if "://" in low else ""
    if given in ("socks5", "socks5h", "socks"):
        scheme = "socks5h"
    elif given in ("socks4", "socks4a"):
        scheme = "socks4a"
    elif given == "https":
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


_family_lock = threading.Lock()


@contextlib.contextmanager
def _only_family(family: "socket.AddressFamily"):
    """Make the connections THIS thread opens use one address family, for the
    duration of the block, through urllib3's own hook (``allowed_gai_family``,
    read by ``create_connection`` on every connect). Other threads keep the
    original answer; the lock keeps two blocks from overwriting each other's
    restore. Opens no socket by itself."""
    owner = threading.get_ident()
    original = _u3conn.allowed_gai_family

    def gated() -> "socket.AddressFamily":
        return family if threading.get_ident() == owner else original()

    with _family_lock:
        _u3conn.allowed_gai_family = gated
        try:
            yield
        finally:
            _u3conn.allowed_gai_family = original


def _host_is_ipv6_only() -> bool:
    """True when an IPv6-only echo answers with an IPv6 address. Asked only after
    the IPv4 echo could not connect, so a True here means: IPv6 yes, IPv4 route no.
    Any failure is False (an offline machine stays an offline machine)."""
    deadline = time.monotonic() + _IPV6_PROBE_BUDGET
    for url in _IPV6_ECHO_ENDPOINTS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with _only_family(socket.AF_INET6):
                resp = requests.get(url, proxies=None,
                                    timeout=(remaining / 2, remaining / 2))
            resp.raise_for_status()
            if ipaddress.ip_address(resp.text.strip()).version == 6:
                return True
        except Exception:  # noqa: BLE001 - not proof of anything
            continue
    return False


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

    Without a proxy the echo is made over IPv4 ONLY: the persona is IPv4-only
    (``v6=0``), so the address that matters is the one the session will use. If no
    IPv4 echo can connect while an IPv6-only echo answers, the host has no IPv4
    route and a direct session on it would have no connectivity: that raises
    ``GeoTimezoneError`` with ``kind="ipv6_only_no_proxy"`` (see
    ``_webrtc_network.py``). If both fail, the failure is the usual one.
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
            with (contextlib.nullcontext() if proxies
                  else _only_family(socket.AF_INET)):
                resp = requests.get(
                    url, proxies=proxies, timeout=(slice_ / 2, slice_ / 2)
                )
            resp.raise_for_status()
            ip = resp.text.strip()
            parsed = ipaddress.ip_address(ip)  # raises ValueError if not an IP
        except Exception as exc:  # noqa: BLE001 - try the next endpoint
            kind, detail = _classify(exc)
            attempts.append(
                _Attempt(url, time.monotonic() - started, kind,
                         _without_credentials(detail, proxy)))
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
    if not proxied and kinds == {"connect_failed"} and _host_is_ipv6_only():
        raise GeoTimezoneError(
            _remedy("ipv6_only_no_proxy", False),
            kind="ipv6_only_no_proxy", attempts=tuple(attempts))
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

    ⛔ THE MODE IS EXPLICIT, AND ``MODE_MMAP`` ON PURPOSE ([B227], 2026-09-25).
    The default picks the C extension, which on Windows opens the file with a
    path in BYTES and cannot find a name that is not ASCII. The cache lives
    under the user's profile, so every Windows user called José, Müller or with
    a CJK account name got ``FileNotFoundError`` on a database that exists, and
    behind a proxy that refused the launch outright. Measured on the same file:
    ``MODE_AUTO`` and ``MODE_MMAP_EXT`` fail, ``MODE_MMAP``, ``MODE_FILE`` and
    ``MODE_MEMORY`` answer. ``MODE_MMAP`` opens the file from Python and maps it
    like the extension does; the handful of lookups a launch makes do not
    notice the pure-Python reader.

    A database that is there but cannot be read is said as such
    (``geoip_unreadable``), not as the bare ``OSError``, which named a missing
    file and sent the reader looking for a download problem.
    """
    import maxminddb

    try:
        with maxminddb.open_database(str(mmdb_path), mode=maxminddb.MODE_MMAP) as reader:
            record = reader.get(ip)
    # `InvalidDatabaseError` is a RuntimeError in maxminddb, so it is named
    # rather than caught through a base class that would also swallow ours.
    except (OSError, ValueError,
            getattr(maxminddb, "InvalidDatabaseError", ValueError)) as exc:
        raise GeoTimezoneError(
            f"the geoip database at {mmdb_path} could not be read - "
            f"{_remedy('geoip_unreadable', False)}: {exc}",
            kind="geoip_unreadable") from exc
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
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise GeoTimezoneError(
            f"geoip returned an invalid IANA zone {tz!r} for {ip}: {exc}",
            kind="invalid_timezone") from exc
    return tz


def ip_to_country(ip: str, mmdb_path: Any) -> Optional[str]:
    """The ISO 3166 country of ``ip`` (MaxMind ``country.iso_code``), upper case,
    or None when the DB does not know the IP or the record has no code.

    Only the COUNTRY: which language a browser there reports is not a geo
    question but a Firefox one (which build people there install), and it is
    answered in one place, `_locale._COUNTRY_FIREFOX_BUILDS`.
    """
    record = _geo_record(ip, mmdb_path)
    cc = ((record.get("country") or {}).get("iso_code") or "") if record else ""
    return cc.upper() or None


class _Egress(NamedTuple):
    """Where a session comes from: the address and its country, together,
    because the language decision needs both (the country picks the builds,
    the address picks one of them)."""

    ip: str
    country: str


def _egress_country(egress_ip: Optional[str], proxy: Optional[Dict[str, str]],
                    *, may_discover: bool = True,
                    discovery_failure: Optional[BaseException] = None) -> Optional[_Egress]:
    """The egress address and country, for ``locale="auto"``; None when the
    country cannot be known.

    PRIVATE, and only :func:`invisible_core._locale.decide_session_locale`
    calls it: the country is an INPUT to the decision, not a value any consumer
    may read. Until 36.x a tag took its place (the public
    `resolve_session_locale`), and every consumer that read the raw tag derived
    values the table then contradicted.

    Behind a proxy it reuses the already-discovered ``egress_ip`` (no extra
    round-trip); without a proxy it discovers the host's public IP when it was
    not handed one and ``may_discover`` allows it. On any failure it returns
    None and says so on stderr; the decision then takes en-US (never breaks
    launch - locale is cosmetic, unlike timezone which traps a foreign-proxy
    mismatch).
    """
    from .download import ensure_geoip_mmdb

    try:
        # ⛔ WHAT THE CALLER ALREADY HAS IS REUSED: `prepare_session_geo` has
        # already paid this round trip and now carries the result even without a
        # proxy. Discovery happens only when there is nothing to reuse - the
        # language decided on its own (`decide_session_locale`), where nobody
        # has asked the network anything yet - and never a second time after
        # `prepare_session_geo` already failed to discover
        # (``may_discover=False``). Until 49.46.0 an explicit timezone was a
        # second such case: `prepare_session_geo` did not ask without a proxy.
        #
        # And discovery stays FORBIDDEN behind a proxy: if `egress_ip` is missing
        # there, discovery has failed, and falling back to the direct address
        # would derive the language from the HOME country while the timezone says
        # the proxy's. `en-US` is better than a contradiction between two fields.
        ip = egress_ip
        if ip is None and may_discover and not _proxy_is_set(proxy):
            ip = discover_egress_ip(None)
        if ip is None:
            why = (f"{type(discovery_failure).__name__}: {discovery_failure}"
                   if discovery_failure is not None
                   else "no egress IP was resolved")
            _warn_locale_fallback(proxy, why)
            return None
        country = ip_to_country(ip, ensure_geoip_mmdb())
        if country is None:
            _warn_locale_fallback(proxy, f"the geo DB has no country for {ip}")
            return None
        return _Egress(ip, country)
    except Exception as exc:  # noqa: BLE001
        _warn_locale_fallback(proxy, f"{type(exc).__name__}: {exc}")
        return None


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

    ``egress_ip`` is the address this session really leaves from, discovered once,
    with or without a proxy: the timezone, the language and the position come from
    it. Nothing here is declared to the engine as an srflx: the engine decides UDP.
    """

    timezone: str
    egress_ip: Optional[str]
    #: The DECLARED position of the session, the one the engine is told
    #: (``Browser.setGeolocationOverride {latitude, longitude, accuracy}``): the
    #: exit IP's point from the SAME geoip record the timezone and the language
    #: come from, moved by the session seed and given the accuracy of an
    #: IP-based fix (:mod:`invisible_core._geoposition`). ``None`` when there is
    #: nothing to declare it from (no seed, the egress discovery failed - it runs
    #: with or without a proxy - or the record has no point): in that case NO
    #: override is sent and nothing is invented.
    #:
    #: They have defaults because ``SessionGeo`` is built positionally with two
    #: arguments in six places across code and tests: adding them without
    #: defaults would have been a breaking change to an exported type.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    #: The session language, decided from the SAME egress as the timezone, by
    #: the one decision (`invisible_core._locale.decide_session_locale`).
    #: :func:`prepare_session_geo` always fills it; a consumer reads it and
    #: passes it on (to the prefs, to the cookie builder) and never derives a
    #: language of its own. ``None`` only on a SessionGeo built by hand, which
    #: is why it has a default at all: the type is built positionally in
    #: tests, and a field without one would have broken every such call.
    locale: Optional["SessionLocale"] = None
    #: The accuracy of the declared position, in metres (see ``latitude``);
    #: ``None`` exactly when the position is.
    accuracy: Optional[float] = None
    #: The MEASURED UDP exit of a SOCKS5 proxy (:mod:`invisible_core._udp_exit`): ``coherent``, ``no`` or
    #: ``unmeasured``. It travels in the proxy's declaration on the pipe (``configure_proxy(..., udp=)``). ``None``
    #: when nothing was measured: no proxy, or one that is not SOCKS5.
    udp_exit: Optional[str] = None

    def geolocation_override(self) -> "Optional[Dict[str, float]]":
        """The ``geolocation`` object of ``Browser.setGeolocationOverride``:
        exactly ``latitude``, ``longitude`` and ``accuracy`` (the command has no
        field for altitude, heading or speed), or ``None`` when nothing is
        declared."""
        if self.latitude is None or self.longitude is None or self.accuracy is None:
            return None
        return {"latitude": self.latitude, "longitude": self.longitude,
                "accuracy": self.accuracy}


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
    timezone: str, proxy: Optional[Dict[str, str]], locale: Optional[str] = "auto",
    *, seed: Optional[int] = None, udp_stun_servers: Optional[Sequence[str]] = None,
) -> SessionGeo:
    """Resolve the session timezone, egress IP AND language in ONE round-trip.

    The egress IP is discovered once and reused for the timezone mapping (when
    ``timezone`` is ``""``/``"auto"``) and the
    language. Timezone precedence is identical to
    :func:`resolve_session_timezone`; the egress IP is best-effort
    (a discovery failure that the timezone path doesn't need won't
    break the launch - but if the timezone path *does* need it behind a proxy,
    that path still fails loudly).

    ``locale`` is what the caller asked for, a tag or "auto", and the returned
    ``SessionGeo.locale`` is the decision (`decide_session_locale`). It is
    decided HERE so that a consumer makes one call and gets the three facts
    that must agree with one another, instead of repeating an
    ``if locale == "auto"`` branch of its own: until 36.x every consumer did,
    five copies across four packages.

    ``seed`` is the session seed. The position is declared FROM it
    (:func:`invisible_core._geoposition.declared_position`): the exit city's
    point moved per seed, with the accuracy of an IP-based fix. Without a seed
    there is no machine to place, so NOTHING is declared (``latitude``,
    ``longitude`` and ``accuracy`` stay ``None``); the same when the exit has
    no point.

    ``udp_stun_servers`` is where a SOCKS5 proxy's UDP exit is measured from: a list of ``"ip:port"`` IPv4
    literals, ``None`` for the two public defaults (:data:`invisible_core._udp_exit.DEFAULT_STUN_SERVERS`). A name or
    a bad port raises ``ValueError`` naming the parameter, with or without a proxy. It is a parameter of the Python
    launch alone: it goes nowhere near the browser's environment, argv, prefs or pipe.
    """
    from .download import ensure_geoip_mmdb

    stun_servers = _udp_exit.stun_servers(udp_stun_servers)
    tz = (timezone or "").strip()
    proxy_set = _proxy_is_set(proxy)

    # ⛔ ONE DISCOVERY, WITH A PROXY OR WITHOUT ONE, reused by the zone, the
    # language and the position. The session's geography is the EXIT's, and
    # without a proxy the exit is the host's public address, never its
    # operating system (the owner's decision of 2026-10-17,
    # 28-valori-dell-host.md A16). Until 49.46.0 this asked only behind a
    # proxy, so a session without one and with an explicit zone had no exit at
    # all: no position, and a language only if "auto" went and asked on its
    # own. A failure is kept, not raised: an explicit zone does not need the
    # address, and the "auto" branch below raises with it.
    egress_ip: Optional[str] = None
    egress_err: Optional[Exception] = None
    # ⛔ THE UDP EXIT IS MEASURED BESIDE THE TCP EXIT, through the same proxy, on a thread: the two are independent
    # until they are compared, so a launch pays the longer of the two and not their sum. SOCKS5 only; None otherwise.
    pending = _udp_exit.start_measurement(proxy, stun_servers) if proxy_set else None
    try:
        egress_ip = discover_egress_ip(proxy if proxy_set else None)
    except GeoTimezoneError as exc:
        if exc.kind == "ipv6_only_no_proxy":
            raise  # no route for the browser: not a geo gap an explicit zone covers
        egress_err = exc
    except Exception as exc:  # noqa: BLE001
        egress_err = exc
    udp_exit = pending.result(egress_ip) if pending is not None else None
    if egress_ip is not None:
        _udp_exit.remember(proxy, udp_exit, stun_servers)   # a context given this proxy later does not measure it again

    # Timezone resolution - same precedence as resolve_session_timezone.
    def _coordinate(ip: "Optional[str]") -> "Optional[DeclaredPosition]":
        """Coordinates are BEST-EFFORT, the zone is not, and the gap is meant.

        A wrong timezone behind a proxy is the `tz_mismatch` trap and has to fail
        the launch. A MISSING position, on the other hand, is not a
        contradiction: it is a browser nobody has asked where it is yet, and the
        engine refuses it cleanly. Failing a launch over that would be more
        brittle without being more faithful.
        """
        if not ip or seed is None:
            return None
        try:
            base_lat, base_lon = ip_to_coordinates(ip, ensure_geoip_mmdb())
        except Exception:  # noqa: BLE001
            return None
        return position_to_declare(seed, base_lat, base_lon)

    if tz and tz.lower() != "auto":
        where = _coordinate(egress_ip)
        # The address was asked once above; the language reuses it and never
        # asks again, and a failure there is the en-US fallback it announces.
        return SessionGeo(tz, egress_ip,  # explicit IANA wins
                          where.latitude if where else None,
                          where.longitude if where else None,
                          _decide(locale, egress_ip=egress_ip, proxy=proxy,
                                  may_discover=False, discovery_failure=egress_err),
                          where.accuracy if where else None,
                          udp_exit)
    ip: Optional[str] = egress_ip
    try:
        if ip is None:  # the discovery above failed
            raise egress_err or GeoTimezoneError("egress IP discovery failed")
        where = _coordinate(ip)
        zone = ip_to_timezone(ip, _geoip_database(ip, proxy_set))
    except GeoTimezoneError:
        raise  # "auto" could not be honoured: see the module docstring
    except Exception as exc:  # noqa: BLE001
        raise GeoTimezoneError(f"the egress timezone could not be resolved: {exc}",
                               kind="geoip_unavailable") from exc
    return SessionGeo(zone, ip,
                      where.latitude if where else None,
                      where.longitude if where else None,
                      _decide(locale, egress_ip=ip, proxy=proxy, may_discover=False),
                      where.accuracy if where else None,
                      udp_exit)


def resolve_session_timezone(
    timezone: str, proxy: Optional[Dict[str, str]]
) -> str:
    """Map the user's ``timezone`` setting to a concrete IANA zone, or raise
    GeoTimezoneError when "auto" cannot be resolved (never the host's zone).

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
    # A failure raises, with a proxy or without one: see the module docstring.
    ip = discover_egress_ip(proxy if proxy_set else None)
    return ip_to_timezone(ip, _geoip_database(ip, proxy_set))
