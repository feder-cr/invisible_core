"""Where a SOCKS5 proxy's UDP leaves from, MEASURED, and declared with the proxy.

A page behind a proxy that grants SOCKS5 UDP ASSOCIATE, but whose UDP leaves from an address other than the TCP
exit, sees a server-reflexive candidate that is not the address of its HTTP: the classic leak. A home line has ONE
address. So the exit discovery the core already makes for the TCP side (:func:`invisible_core._geo.discover_egress_ip`:
address, zone, city) also measures the UDP side through the same proxy, once per proxy, and the answer travels IN THE
PROXY DECLARATION on the pipe: the optional field ``udp`` of ``Browser.setBrowserProxy`` and
``Browser.setContextProxy``, one of

    ``coherent``    a STUN Binding response came back through the relay and its mapped address IS the TCP exit
    ``no``          the proxy refused UDP (ASSOCIATE refused, or the method refused), or the UDP leaves from
                    ANOTHER address
    ``unmeasured``  no response within the budget, or an error before the answer
    (absent)        not a SOCKS5 proxy: nothing measured. The engine reads absent as ``no``

The engine acts on ``coherent`` only; ``no`` and ``unmeasured`` are both "a router that drops UDP". The distinction is
reported, not acted on: a silence is not a refusal.

The measurement is pure sockets (no third-party code), and it never resolves a name on the host: the STUN servers
are IP LITERALS. (The proxy itself is reached the way the TCP discovery reaches it.) The credentials are used on the
socket and nowhere else: not logged, not in the environment, not on a command line.

How it asks, and why
--------------------
TCP connect to the proxy, the greeting (method 0x02 offered with the credentials when there are any), UDP ASSOCIATE
(CMD 0x03, DST 0.0.0.0:0), then a STUN Binding request (RFC 5389/8489: the 20-byte header, the magic cookie
0x2112A442, a random 96-bit transaction id, no attributes) wrapped in the SOCKS5 UDP header (RSV 0x0000, FRAG 0x00,
ATYP 0x01, IPv4, port) and sent to the relay. The control connection stays open for the whole exchange: the
association dies with it.

RETRANSMISSION is ICE's: at 0, 500 and 1000 ms, the same transaction id each time, so a lost first datagram still
gives ``coherent``. EVERY transmission goes to BOTH servers, and not alternately: alternating would give each server
two transmissions at best and a dead server would cost a round; sending to both costs six tiny datagrams and gives
each transmission two independent chances (a server down, a datagram lost, one anycast site slow). The transaction id
is per server and constant across retransmissions, so a reply says which server it came from and a duplicate is
harmless. The whole exchange (connect, handshake, ASSOCIATE, the three rounds, the wait for the last) is bounded by
:data:`BUDGET`, three seconds.

The comparison is on the address after ``ipaddress`` normalisation. IPv4 STUN only: when the TCP exit is IPv6 the
mapped IPv4 address differs from it and the answer is ``no`` (an IPv4-mapped IPv6 result never equals either).

THE SERVERS ARE AN EXPLICIT PARAMETER OF THE LAUNCH, never an environment variable and never a browser declaration.
:data:`DEFAULT_STUN_SERVERS` are the defaults; ``udp_stun_servers`` (a list of ``"ip:port"`` IPv4 literals) overrides them, and
is how a hermetic test points the measurement at a fake STUN only the relay can reach. It is a keyword of
``prepare_session_geo``, ``build_launch_plan``, ``get_default_stealth_prefs``, ``invisible_core.juggler.launch`` and, in the
Playwright wrapper, of ``InvisiblePlaywright(...)`` and ``launch_server(...)``, which keep it on the session so the
launch and every later ``new_context(proxy=...)`` measure with the SAME list. A name, an IPv6 literal or a bad port
raises ``ValueError`` naming ``udp_stun_servers``. The list is used by the Python process alone: it is not written
to the browser's environment, argv, prefs or pipe.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import socket
import struct
import threading
import time
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from ._proxy import UDP_EXIT_VALUES, ProxyEndpoint, parse_proxy

log = logging.getLogger(__name__)

#: The two STUN servers asked when the launch names none: two public anycast STUN addresses, in the form the
#: ``udp_stun_servers`` parameter takes (``"ip:port"``, an IPv4 literal and a port: the core never resolves a name).
#: Two different anycast prefixes, so a lost datagram, a slow site or one prefix refused by the proxy's network does
#: not make the measurement blind. Source and date of verification are recorded in the study notes
#: (invisible_WebRTC/notes), not here. The addresses are not guaranteed by their operator: a change makes the answer
#: ``unmeasured``, never a wrong ``coherent``.
DEFAULT_STUN_SERVERS: Tuple[str, ...] = ("141.101.90.1:3478", "162.159.207.1:3478")

#: The whole measurement is bounded by this, in seconds.
BUDGET = 3.0
#: When each round of the request is sent, in seconds from the first: ICE's 0, 500 and 1000 ms.
SCHEDULE: Tuple[float, ...] = (0.0, 0.5, 1.0)

COHERENT = "coherent"
NO = "no"
UNMEASURED = "unmeasured"
#: The values of the ``udp`` field.
VALUES = UDP_EXIT_VALUES

#: How long a proxy's measurement is kept (seconds): for a context that gives the proxy again, and for the launch that
#: measured it to reach the engine's first command even after a first-run download.
CACHE_SECONDS = 1800.0

_MAGIC = b"\x21\x12\xa4\x42"
_BINDING_REQUEST = b"\x00\x01\x00\x00" + _MAGIC
_BINDING_SUCCESS = 0x0101
_MAPPED_ADDRESS = 0x0001
_XOR_MAPPED_ADDRESS = 0x0020

Server = Tuple[str, int]


# ── the servers ─────────────────────────────────────────────────────────────
def _server(entry: Any) -> Server:
    """One server, from ``"ip:port"`` or from an ``(ip, port)`` pair; an IPv4 literal and a port, or ValueError."""
    try:
        if isinstance(entry, str):
            host, sep, port_text = entry.strip().rpartition(":")
            if not sep:
                raise ValueError("no port")
            port = int(port_text)
        else:
            host, port = entry
            port = int(port)
        address = ipaddress.ip_address(host)
        if address.version != 4:
            raise ValueError("IPv4 only")
        if not 1 <= port <= 65535:
            raise ValueError("port outside 1-65535")
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "udp_stun_servers: %r is not an IPv4 literal with a port (\"ip:port\"), the form the STUN servers "
            "are given in: %s" % (entry, exc))
    return str(address), port


def stun_servers(udp_stun_servers: Optional[Sequence[Any]] = None) -> Tuple[Server, ...]:
    """The STUN servers the measurement asks: the ``udp_stun_servers`` parameter of the launch (``"ip:port"`` IPv4
    literals, validated: a name or a bad port raises ValueError naming the parameter), else :data:`DEFAULT_STUN_SERVERS`.
    ``None`` means the defaults; an EMPTY list is refused (it would measure nothing and say so late)."""
    if udp_stun_servers is None:
        return tuple(_server(entry) for entry in DEFAULT_STUN_SERVERS)
    if isinstance(udp_stun_servers, str):
        raise ValueError("udp_stun_servers: give a list of \"ip:port\" strings, not one string")
    servers = tuple(_server(entry) for entry in udp_stun_servers)
    if not servers:
        raise ValueError("udp_stun_servers: the list is empty; pass None for the defaults")
    return servers


# ── the exchange ────────────────────────────────────────────────────────────
class UdpProbe(NamedTuple):
    """What the exchange saw, before it is compared with the TCP exit.

    ``outcome``: ``mapped`` (a response; ``ip`` is its mapped address), ``refused`` (ASSOCIATE or the method
    refused), ``silent`` (the budget ended with no response) or ``error``."""

    outcome: str
    ip: Optional[str] = None


class _Budget:
    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        return self.end - time.monotonic()


def _read(sock: socket.socket, n: int, budget: _Budget) -> bytes:
    data = b""
    while len(data) < n:
        left = budget.left()
        if left <= 0:
            raise TimeoutError("budget")
        sock.settimeout(left)
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


class _Refused(Exception):
    """The proxy said no to UDP: a reply code other than 0, or no method it accepts."""


def _associate(ctl: socket.socket, endpoint: ProxyEndpoint, budget: _Budget) -> Tuple[str, int]:
    """Greeting, authentication and UDP ASSOCIATE on the control connection; the relay's (address, port)."""
    user = endpoint.username.encode("utf-8")
    password = endpoint.password.encode("utf-8")
    with_credentials = bool(user or password)
    ctl.settimeout(max(budget.left(), 0.001))
    ctl.sendall(b"\x05\x02\x00\x02" if with_credentials else b"\x05\x01\x00")
    version, method = _read(ctl, 2, budget)
    if version != 5:
        raise ValueError("not a SOCKS5 proxy")
    if method == 0x02 and with_credentials:
        if len(user) > 255 or len(password) > 255:
            raise ValueError("credentials too long for the method")
        ctl.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
        _, status = _read(ctl, 2, budget)
        if status != 0:
            raise _Refused()
    elif method != 0x00:
        raise _Refused()
    ctl.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
    version, reply, _rsv, atyp = _read(ctl, 4, budget)
    if version != 5:
        raise ValueError("not a SOCKS5 proxy")
    if reply != 0:
        raise _Refused()
    peer = ctl.getpeername()[0]
    if atyp == 1:
        host = socket.inet_ntoa(_read(ctl, 4, budget))
    elif atyp == 4:
        host = socket.inet_ntop(socket.AF_INET6, _read(ctl, 16, budget))
    elif atyp == 3:
        _read(ctl, _read(ctl, 1, budget)[0], budget)   # a NAME is never resolved here
        host = peer
    else:
        raise ValueError("unknown address type")
    port = struct.unpack("!H", _read(ctl, 2, budget))[0]
    if ipaddress.ip_address(host.split("%", 1)[0]).is_unspecified:
        host = peer          # a relay that names no address is reached where the proxy was
    return host, port


def _parse_reply(datagram: bytes, transaction_ids: Dict[bytes, Server]) -> Optional[str]:
    """The mapped address of a Binding success response to one of our requests, wrapped in the SOCKS5 UDP header;
    None for anything else."""
    if len(datagram) < 10 or datagram[0:2] != b"\x00\x00" or datagram[2] != 0:
        return None                           # RSV, and FRAG: a fragment is dropped
    atyp = datagram[3]
    if atyp == 1:
        offset = 10
    elif atyp == 4:
        offset = 22
    elif atyp == 3:
        offset = 5 + datagram[4] + 2
    else:
        return None
    stun = datagram[offset:]
    if len(stun) < 20:
        return None
    message_type, length = struct.unpack("!HH", stun[:4])
    transaction_id = stun[8:20]
    if (message_type != _BINDING_SUCCESS or stun[4:8] != _MAGIC
            or transaction_id not in transaction_ids or 20 + length > len(stun)):
        return None
    mapped = xor_mapped = None
    body, i = stun[20:20 + length], 0
    while i + 4 <= len(body):
        kind, size = struct.unpack("!HH", body[i:i + 4])
        value = body[i + 4:i + 4 + size]
        if len(value) < size:
            break
        if kind in (_MAPPED_ADDRESS, _XOR_MAPPED_ADDRESS) and len(value) >= 4:
            family, raw = value[1], value[4:]
            if kind == _XOR_MAPPED_ADDRESS:
                key = _MAGIC + transaction_id
                raw = bytes(a ^ b for a, b in zip(raw, key))
            try:
                if family == 1 and len(raw) == 4:
                    address = str(ipaddress.IPv4Address(raw))
                elif family == 2 and len(raw) == 16:
                    address = str(ipaddress.IPv6Address(raw))
                else:
                    address = None
            except ValueError:
                address = None
            if address is not None:
                if kind == _XOR_MAPPED_ADDRESS:
                    xor_mapped = address
                else:
                    mapped = address
        i += 4 + ((size + 3) & ~3)
    return xor_mapped or mapped


def probe_udp_exit(endpoint: ProxyEndpoint, servers: Sequence[Server], budget: float,
                   schedule: Sequence[float]) -> UdpProbe:
    """The exchange, for a SOCKS5 endpoint. Never raises: whatever goes wrong is an outcome."""
    clock = _Budget(budget)
    ctl: Optional[socket.socket] = None
    udp: Optional[socket.socket] = None
    try:
        ctl = socket.create_connection((endpoint.host, endpoint.port), timeout=max(clock.left(), 0.001))
        relay = _associate(ctl, endpoint, clock)
        udp = socket.socket(socket.AF_INET6 if ":" in relay[0] else socket.AF_INET, socket.SOCK_DGRAM)
        # One transaction id per server, the same across retransmissions (ICE's rule).
        transaction_ids: Dict[bytes, Server] = {os.urandom(12): server for server in servers}
        requests: List[bytes] = []
        for transaction_id, (host, port) in transaction_ids.items():
            requests.append(b"\x00\x00\x00\x01" + socket.inet_aton(host) + struct.pack("!H", port)
                            + _BINDING_REQUEST + transaction_id)
        first = time.monotonic()
        sent = 0
        while True:
            now = time.monotonic()
            left = clock.left()
            if left <= 0:
                return UdpProbe("silent")
            if sent < len(schedule) and now >= first + schedule[sent]:
                for request in requests:
                    udp.sendto(request, relay)
                sent += 1
                continue
            wake = min(left, (first + schedule[sent] - now) if sent < len(schedule) else left)
            udp.settimeout(max(wake, 0.001))
            try:
                datagram, _source = udp.recvfrom(65535)
            except (socket.timeout, BlockingIOError, ConnectionResetError):
                continue   # (Windows reports an ICMP port-unreachable of an earlier send as a reset)
            address = _parse_reply(datagram, transaction_ids)
            if address is not None:
                return UdpProbe("mapped", address)
    except _Refused:
        return UdpProbe("refused")
    except TimeoutError:
        return UdpProbe("silent")
    except Exception as exc:  # noqa: BLE001 - an outcome, not a crash; the TYPE only: no text that could carry a secret
        log.debug("the UDP exit could not be measured: %s", type(exc).__name__)
        return UdpProbe("error")
    finally:
        for sock in (udp, ctl):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


def judge(probe: UdpProbe, tcp_exit_ip: Optional[str]) -> str:
    """What a probe means for a proxy whose TCP exit is ``tcp_exit_ip``."""
    if probe.outcome == "refused":
        return NO
    if probe.outcome != "mapped" or probe.ip is None:
        return UNMEASURED
    try:
        exit_ip = ipaddress.ip_address((tcp_exit_ip or "").strip())
        mapped = ipaddress.ip_address(probe.ip)
    except ValueError:
        return UNMEASURED
    return COHERENT if mapped == exit_ip else NO


def _endpoint_to_measure(proxy: Optional[Dict[str, Any]]) -> Optional[ProxyEndpoint]:
    """The endpoint when it is a SOCKS5 proxy, else None (nothing to measure; also for one that cannot be read:
    the launch refuses that on its own)."""
    try:
        endpoint = parse_proxy(proxy)
    except ValueError:
        return None
    return endpoint if endpoint is not None and endpoint.type == "socks" else None


def measure_udp_exit(proxy: Optional[Dict[str, Any]], tcp_exit_ip: Optional[str], *,
                     udp_stun_servers: Optional[Sequence[Any]] = None, budget: float = BUDGET,
                     schedule: Optional[Sequence[float]] = None) -> Optional[str]:
    """``coherent`` / ``no`` / ``unmeasured`` for a SOCKS5 ``proxy`` (``{"server", "username", "password"}``) whose
    TCP exit is ``tcp_exit_ip``; None for any other proxy (nothing is measured and no field is declared).

    ``udp_stun_servers`` (``"ip:port"`` IPv4 literals) defaults to :data:`DEFAULT_STUN_SERVERS`; ``budget`` and ``schedule``
    default to :data:`BUDGET` and :data:`SCHEDULE`. Blocks for at most ``budget`` seconds."""
    servers = stun_servers(udp_stun_servers)
    endpoint = _endpoint_to_measure(proxy)
    if endpoint is None:
        return None
    probe = probe_udp_exit(endpoint, servers, budget, SCHEDULE if schedule is None else schedule)
    verdict = judge(probe, tcp_exit_ip)
    log.debug("the UDP exit through the proxy: %s", verdict)
    return verdict


class PendingMeasurement:
    """A measurement running beside the TCP discovery. :meth:`result` joins it and judges it."""

    def __init__(self, endpoint: ProxyEndpoint, servers: Sequence[Server], budget: float,
                 schedule: Sequence[float]) -> None:
        self._budget = budget
        self._probe = UdpProbe("error")
        self._thread = threading.Thread(target=self._run, args=(endpoint, tuple(servers), budget, tuple(schedule)),
                                        name="invisible-udp-exit", daemon=True)
        self._thread.start()

    def _run(self, endpoint, servers, budget, schedule) -> None:
        self._probe = probe_udp_exit(endpoint, servers, budget, schedule)

    def result(self, tcp_exit_ip: Optional[str]) -> str:
        self._thread.join(self._budget + 1.0)
        verdict = judge(self._probe, tcp_exit_ip) if not self._thread.is_alive() else UNMEASURED
        log.debug("the UDP exit through the proxy: %s", verdict)
        return verdict


def start_measurement(proxy: Optional[Dict[str, Any]],
                      udp_stun_servers: Optional[Sequence[Any]] = None) -> Optional[PendingMeasurement]:
    """Start measuring a SOCKS5 proxy's UDP exit on a thread, to be joined with the TCP exit once it is known;
    None when the proxy is not SOCKS5."""
    servers = stun_servers(udp_stun_servers)
    endpoint = _endpoint_to_measure(proxy)
    if endpoint is None:
        return None
    return PendingMeasurement(endpoint, servers, BUDGET, SCHEDULE)


# ── once per proxy ──────────────────────────────────────────────────────────
def cache_key(endpoint: ProxyEndpoint, servers: Tuple[Server, ...] = ()) -> Tuple[Any, ...]:
    """The endpoint, a hash of the credentials (never the secret itself) and the servers asked."""
    secret = hashlib.sha256(("%s\0%s" % (endpoint.username, endpoint.password)).encode("utf-8")).hexdigest()
    return (endpoint.type, endpoint.host, endpoint.port, secret, tuple(servers))


_cache: Dict[Tuple[Any, ...], Tuple[float, Optional[str]]] = {}
_cache_lock = threading.Lock()


def remember(proxy: Optional[Dict[str, Any]], verdict: Optional[str],
             udp_stun_servers: Optional[Sequence[Any]] = None) -> None:
    """Keep a measurement the launch already made, so a context given the same proxy does not repeat it."""
    servers = stun_servers(udp_stun_servers)
    endpoint = _endpoint_to_measure(proxy)
    if endpoint is not None and verdict is not None:
        with _cache_lock:
            _cache[cache_key(endpoint, servers)] = (time.monotonic(), verdict)


def measure_proxy_udp(proxy: Optional[Dict[str, Any]],
                      udp_stun_servers: Optional[Sequence[Any]] = None) -> Optional[str]:
    """The ``udp`` field to declare for ``proxy``, discovering what it needs: the TCP exit through the proxy (the
    core's own discovery) and, beside it, the UDP exit. ONCE per proxy (endpoint and a hash of the credentials) for
    :data:`CACHE_SECONDS`. None for a proxy that is not SOCKS5.

    For a proxy given to a context (``new_context(proxy=...)``): called BEFORE ``Browser.setContextProxy`` is sent. It
    costs the TCP discovery (one to three round trips through the proxy) with the UDP measurement beside it, so
    about the time of the longer of the two; nothing at all the second time."""
    servers = stun_servers(udp_stun_servers)
    endpoint = _endpoint_to_measure(proxy)
    if endpoint is None:
        return None
    key = cache_key(endpoint, servers)
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    from . import _geo
    pending = start_measurement(proxy, servers)
    exit_ip: Optional[str] = None
    try:
        exit_ip = _geo.discover_egress_ip(proxy)
    except Exception:  # noqa: BLE001 - without the TCP exit nothing can be compared: unmeasured
        exit_ip = None
    verdict = pending.result(exit_ip) if pending is not None else None
    if exit_ip is not None:          # a comparison that could not be made is not kept
        remember(proxy, verdict, servers)
    return verdict


def _command(proxy: Dict[str, Any], udp: Any) -> Optional[Dict[str, Any]]:
    """The engine command of ``proxy`` with ``udp`` as the measurement says. ``parse_proxy`` is the ONE reader of
    what the caller wrote and it never reads a ``udp`` key: the field comes from here and from nowhere else."""
    endpoint = parse_proxy(proxy)
    if endpoint is None:
        return None
    if endpoint.type == "socks":
        endpoint = endpoint._replace(udp=udp)
    return endpoint.as_engine_command()


def context_proxy_command(proxy: Dict[str, Any],
                          udp_stun_servers: Optional[Sequence[Any]] = None) -> Optional[Dict[str, Any]]:
    """The params of ``Browser.setContextProxy`` (without the context id) for ``proxy``: the proxy as
    ``parse_proxy(...).as_engine_command()`` renders it, and for a SOCKS5 one the ``udp`` MEASURED now by
    :func:`measure_proxy_udp` (``unmeasured`` when the measurement could not run). Call it BEFORE the command is sent.
    Whatever the caller wrote under ``udp`` is not read. ValueError for a proxy that cannot be read."""
    endpoint = parse_proxy(proxy)
    if endpoint is None or endpoint.type != "socks":
        return _command(proxy, None)
    return _command(proxy, measure_proxy_udp(proxy, udp_stun_servers) or UNMEASURED)


def launch_proxy_command(proxy: Dict[str, Any],
                         udp_stun_servers: Optional[Sequence[Any]] = None) -> Optional[Dict[str, Any]]:
    """The params of ``Browser.setBrowserProxy`` for ``proxy``, WITHOUT touching the network.

    The measurement belongs to the launch's geo step (``prepare_session_geo``, beside the TCP discovery), which
    remembers it for the proxy and the servers it asked; this renders the command with what was remembered, and
    ``unmeasured`` when nothing was (a client that composed no session, an entry that expired): never ``coherent``
    without a measurement. Whatever the caller wrote under ``udp`` is not read."""
    endpoint = parse_proxy(proxy)
    if endpoint is None or endpoint.type != "socks":
        return _command(proxy, None)
    servers = stun_servers(udp_stun_servers)
    with _cache_lock:
        hit = _cache.get(cache_key(endpoint, servers))
    fresh = hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS
    return _command(proxy, (hit[1] if fresh else None) or UNMEASURED)
