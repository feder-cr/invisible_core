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

THE SERVERS ARE A DECLARATION. :data:`STUN_SERVERS` are the defaults; the environment variable
``STEALTHFOX_UDP_STUN_SERVERS`` (``invisible_core.environment.UDP_STUN_SERVERS``) overrides them with a
comma-separated list of ``ip:port`` IP literals, which is how a hermetic test points the measurement at a fake STUN
only the relay can reach. A value that is not an IP literal with a port is refused with an error naming the variable.
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

from . import environment
from ._proxy import UDP_EXIT_VALUES, ProxyEndpoint, parse_proxy

log = logging.getLogger(__name__)

#: The two STUN servers asked, as IP literals. Source, read on 2026-11-11: Cloudflare Realtime TURN FAQ,
#: https://developers.cloudflare.com/realtime/turn/faq/ (page "Last updated Jul 14, 2026"), which lists the anycast
#: addresses 141.101.90.1 and 162.159.207.1 (IPv4) and says: "TURN service at turn.cloudflare.com will also respond
#: to binding requests ("STUN requests")"; the port is the one the same documentation gives for STUN,
#: https://developers.cloudflare.com/realtime/turn/ (STUN over UDP, 3478/udp). Two different anycast prefixes of the
#: same operator: a lost datagram, a slow site or one prefix refused by the proxy's network does not make the
#: measurement blind. The documentation does not guarantee the addresses ("tries to, but cannot guarantee"); a change
#: makes the answer ``unmeasured``, never a wrong ``coherent``, and the declaration below overrides them.
#: Google's STUN (stun.l.google.com:19302) is NOT used: Google publishes a name, no address, and an address that
#: cannot be confirmed from a primary source is not written here.
STUN_SERVERS: Tuple[Tuple[str, int], ...] = (("141.101.90.1", 3478), ("162.159.207.1", 3478))

#: The whole measurement is bounded by this, in seconds.
BUDGET = 3.0
#: When each round of the request is sent, in seconds from the first: ICE's 0, 500 and 1000 ms.
SCHEDULE: Tuple[float, ...] = (0.0, 0.5, 1.0)

COHERENT = "coherent"
NO = "no"
UNMEASURED = "unmeasured"
#: The values of the ``udp`` field.
VALUES = UDP_EXIT_VALUES

#: How long a proxy's measurement is kept, for a context that gives the proxy again (seconds).
CACHE_SECONDS = 600.0

_MAGIC = b"\x21\x12\xa4\x42"
_BINDING_REQUEST = b"\x00\x01\x00\x00" + _MAGIC
_BINDING_SUCCESS = 0x0101
_MAPPED_ADDRESS = 0x0001
_XOR_MAPPED_ADDRESS = 0x0020

Server = Tuple[str, int]


# ── the servers ─────────────────────────────────────────────────────────────
def _server(text: str) -> Server:
    host, sep, port_text = text.strip().rpartition(":")
    try:
        if not sep:
            raise ValueError("no port")
        address = ipaddress.ip_address(host)
        if address.version != 4:
            raise ValueError("IPv4 only")
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError("port outside 1-65535")
    except ValueError as exc:
        raise ValueError(
            "%s: %r is not an IPv4 literal with a port (ip:port), the form the STUN servers are declared in: %s"
            % (environment.UDP_STUN_SERVERS, text, exc))
    return str(address), port


def declared_stun_servers() -> Tuple[Server, ...]:
    """The STUN servers the measurement asks: the declaration in the environment, else :data:`STUN_SERVERS`."""
    value = os.environ.get(environment.UDP_STUN_SERVERS)
    if value is None or not value.strip():
        return STUN_SERVERS
    return tuple(_server(part) for part in value.split(",") if part.strip())


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
                     servers: Optional[Sequence[Server]] = None, budget: float = BUDGET,
                     schedule: Optional[Sequence[float]] = None) -> Optional[str]:
    """``coherent`` / ``no`` / ``unmeasured`` for a SOCKS5 ``proxy`` (``{"server", "username", "password"}``) whose
    TCP exit is ``tcp_exit_ip``; None for any other proxy (nothing is measured and no field is declared).

    ``servers`` defaults to :func:`declared_stun_servers`; ``budget`` and ``schedule`` default to :data:`BUDGET` and
    :data:`SCHEDULE`. Blocks for at most ``budget`` seconds."""
    endpoint = _endpoint_to_measure(proxy)
    if endpoint is None:
        return None
    probe = probe_udp_exit(endpoint, servers if servers is not None else declared_stun_servers(),
                           budget, SCHEDULE if schedule is None else schedule)
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


def start_measurement(proxy: Optional[Dict[str, Any]]) -> Optional[PendingMeasurement]:
    """Start measuring a SOCKS5 proxy's UDP exit on a thread, to be joined with the TCP exit once it is known;
    None when the proxy is not SOCKS5."""
    endpoint = _endpoint_to_measure(proxy)
    if endpoint is None:
        return None
    return PendingMeasurement(endpoint, declared_stun_servers(), BUDGET, SCHEDULE)


# ── once per proxy ──────────────────────────────────────────────────────────
def cache_key(endpoint: ProxyEndpoint) -> Tuple[Any, ...]:
    """The endpoint and a hash of the credentials: never the secret itself."""
    secret = hashlib.sha256(("%s\0%s" % (endpoint.username, endpoint.password)).encode("utf-8")).hexdigest()
    return (endpoint.type, endpoint.host, endpoint.port, secret)


_cache: Dict[Tuple[Any, ...], Tuple[float, Optional[str]]] = {}
_cache_lock = threading.Lock()


def remember(proxy: Optional[Dict[str, Any]], verdict: Optional[str]) -> None:
    """Keep a measurement the launch already made, so a context given the same proxy does not repeat it."""
    endpoint = _endpoint_to_measure(proxy)
    if endpoint is not None and verdict is not None:
        with _cache_lock:
            _cache[cache_key(endpoint)] = (time.monotonic(), verdict)


def measure_proxy_udp(proxy: Optional[Dict[str, Any]]) -> Optional[str]:
    """The ``udp`` field to declare for ``proxy``, discovering what it needs: the TCP exit through the proxy (the
    core's own discovery) and, beside it, the UDP exit. ONCE per proxy (endpoint and a hash of the credentials) for
    :data:`CACHE_SECONDS`. None for a proxy that is not SOCKS5.

    For a proxy given to a context (``new_context(proxy=...)``): called BEFORE ``Browser.setContextProxy`` is sent. It
    costs the TCP discovery (one to three round trips through the proxy) with the UDP measurement beside it, so
    about the time of the longer of the two; nothing at all the second time."""
    endpoint = _endpoint_to_measure(proxy)
    if endpoint is None:
        return None
    key = cache_key(endpoint)
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    from . import _geo
    pending = start_measurement(proxy)
    exit_ip: Optional[str] = None
    try:
        exit_ip = _geo.discover_egress_ip(proxy)
    except Exception:  # noqa: BLE001 - without the TCP exit nothing can be compared: unmeasured
        exit_ip = None
    verdict = pending.result(exit_ip) if pending is not None else None
    if exit_ip is not None:          # a comparison that could not be made is not kept
        remember(proxy, verdict)
    return verdict


def declare_context_proxy(proxy: Dict[str, Any]) -> Dict[str, Any]:
    """``proxy`` as a context is to be given it: the same dict with the ``udp`` slot holding what was MEASURED
    (:func:`measure_proxy_udp`), whatever the caller wrote there replaced, and removed for a proxy that is not
    SOCKS5. Call it BEFORE ``Browser.setContextProxy`` is sent; ``parse_proxy(...).as_engine_command()`` then carries
    the field."""
    declared = dict(proxy)
    declared.pop("udp", None)
    verdict = measure_proxy_udp(proxy)
    if verdict is not None:
        declared["udp"] = verdict
    return declared


def declare_launch_proxy(proxy: Dict[str, Any]) -> Dict[str, Any]:
    """The session proxy as ``Browser.setBrowserProxy`` is to be given it, WITHOUT touching the network.

    The measurement belongs to the launch's geo step (`prepare_session_geo`, beside the TCP discovery), which hands it
    on through ``configure_proxy(..., udp=)`` into the ``udp`` key of the dict: a proxy that carries it is kept as it
    is. A SOCKS5 proxy that does not (a client that composed no session itself, whose server composed one) gets what
    that geo step remembered for the same endpoint, and ``unmeasured`` when nothing was: it is never claimed
    ``coherent`` without a measurement. Any other proxy carries no field."""
    endpoint = _endpoint_to_measure(proxy)
    if endpoint is None:
        return proxy
    if proxy.get("udp") not in (None, ""):
        return proxy
    with _cache_lock:
        hit = _cache.get(cache_key(endpoint))
    verdict = hit[1] if hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS else UNMEASURED
    return dict(proxy, udp=verdict or UNMEASURED)
