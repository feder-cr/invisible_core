"""A SOCKS5 gateway with a UDP relay, and a STUN responder, on loopback.

For the tests of the UDP-exit measurement (`invisible_core._udp_exit`). Nothing
here touches the internet: the "STUN servers" are sockets on 127.0.0.1 that
only the relay can be asked to reach, and every address a test reports as an
exit is chosen by the test.

What a test can make the gateway do:

* ``associate_reply``  the reply code of UDP ASSOCIATE (0 = granted, 7 = command not supported);
* ``drop_first``       the relay silently drops the first N datagrams sent to EACH destination
                       (a lost first datagram, a lost second one...);
* ``silent``           the relay swallows everything (a firewall that drops UDP);
* ``user``/``password`` the gateway demands them (method 0x02); without them it offers only 0x00 and
                       answers 0xFF to a client that does not offer it;
* ``unspecified_bnd``  BND.ADDR of the grant is 0.0.0.0, so the client must use the address it
                       reached the proxy at.

Everything it saw is kept: ``associates``, ``datagrams`` (destination, payload, as the relay received
them), ``credentials_seen``, and ``control_open_during_udp`` (was the TCP control connection still
open when a datagram came in: the association dies with it).
"""
from __future__ import annotations

import socket
import struct
import threading
from typing import Dict, List, Optional, Tuple

_MAGIC = b"\x21\x12\xa4\x42"


def _recv_exact(s: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


class FakeStun:
    """A UDP STUN server on loopback that answers every Binding request with
    ``mapped_ip``. ``wrong_txid`` answers with a transaction id that is not the
    request's; ``mode`` is ``xor`` (XOR-MAPPED-ADDRESS) or ``plain`` (MAPPED-ADDRESS)."""

    def __init__(self, mapped_ip: str, mapped_port: int = 40000, *, mode: str = "xor",
                 wrong_txid: bool = False, answer: bool = True):
        self.mapped_ip, self.mapped_port = mapped_ip, mapped_port
        self.mode, self.wrong_txid, self.answer = mode, wrong_txid, answer
        self.requests = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self.address: Tuple[str, int] = self._sock.getsockname()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                data, peer = self._sock.recvfrom(2048)
            except OSError:
                return
            if len(data) < 20 or data[0:2] != b"\x00\x01" or data[4:8] != _MAGIC:
                continue
            self.requests += 1
            if not self.answer:
                continue
            txid = data[8:20]
            if self.wrong_txid:
                txid = bytes(b ^ 0xFF for b in txid)
            ip = socket.inet_aton(self.mapped_ip)
            if self.mode == "xor":
                attr = (struct.pack("!HH", 0x0020, 8) + b"\x00\x01"
                        + struct.pack("!H", self.mapped_port ^ 0x2112)
                        + bytes(a ^ b for a, b in zip(ip, _MAGIC)))
            else:
                attr = (struct.pack("!HH", 0x0001, 8) + b"\x00\x01"
                        + struct.pack("!H", self.mapped_port) + ip)
            reply = b"\x01\x01" + struct.pack("!H", len(attr)) + _MAGIC + txid + attr
            try:
                self._sock.sendto(reply, peer)
            except OSError:
                return

    def close(self) -> None:
        self._sock.close()


class UdpGateway:
    def __init__(self, *, associate_reply: int = 0, drop_first: int = 0, silent: bool = False,
                 user: Optional[str] = None, password: Optional[str] = None,
                 unspecified_bnd: bool = False):
        self.associate_reply, self.drop_first, self.silent = associate_reply, drop_first, silent
        self.user, self.password = user, password
        self.unspecified_bnd = unspecified_bnd
        self.associates = 0
        self.accepts = 0
        self.datagrams: List[Tuple[str, int, bytes]] = []
        self.credentials_seen: List[Tuple[str, str]] = []
        self.control_open_during_udp: List[bool] = []
        self._per_destination: Dict[Tuple[str, int], int] = {}
        self._control_open = False
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self.port: int = self._listener.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    # --- what the test hands to the product ---
    @property
    def proxy(self) -> dict:
        out = {"server": "socks5://127.0.0.1:%d" % self.port}
        if self.user is not None:
            out.update(username=self.user, password=self.password)
        return out

    def close(self) -> None:
        self._closed = True
        try:
            self._listener.close()
        except OSError:
            pass

    def __enter__(self) -> "UdpGateway":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- the TCP side ---
    def _accept(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            self.accepts += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _greet(self, c: socket.socket) -> bool:
        ver, n = _recv_exact(c, 2)
        methods = _recv_exact(c, n)
        if ver != 5:
            return False
        if self.user is not None:
            if 2 not in methods:
                c.sendall(b"\x05\xff")
                return False
            c.sendall(b"\x05\x02")
            _recv_exact(c, 1)
            u = _recv_exact(c, _recv_exact(c, 1)[0]).decode()
            p = _recv_exact(c, _recv_exact(c, 1)[0]).decode()
            self.credentials_seen.append((u, p))
            ok = (u, p) == (self.user, self.password)
            c.sendall(b"\x01" + (b"\x00" if ok else b"\x01"))
            return ok
        if 0 not in methods:
            c.sendall(b"\x05\xff")
            return False
        c.sendall(b"\x05\x00")
        return True

    def _handle(self, c: socket.socket) -> None:
        try:
            c.settimeout(30)
            if not self._greet(c):
                return
            _ver, cmd, _rsv, atyp = _recv_exact(c, 4)
            if atyp == 1:
                _recv_exact(c, 4)
            elif atyp == 4:
                _recv_exact(c, 16)
            else:
                _recv_exact(c, _recv_exact(c, 1)[0])
            _recv_exact(c, 2)
            if cmd == 3:
                self._associate(c)
            else:
                c.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
        except (OSError, ConnectionError):
            pass
        finally:
            self._control_open = False
            try:
                c.close()
            except OSError:
                pass

    # --- the UDP side ---
    def _associate(self, c: socket.socket) -> None:
        self.associates += 1
        if self.associate_reply != 0:
            c.sendall(b"\x05" + bytes([self.associate_reply]) + b"\x00\x01\x00\x00\x00\x00\x00\x00")
            return
        relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        relay.bind(("127.0.0.1", 0))
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        out.bind(("127.0.0.1", 0))
        client: List[Optional[Tuple[str, int]]] = [None]
        bnd = "0.0.0.0" if self.unspecified_bnd else "127.0.0.1"
        self._control_open = True
        c.sendall(b"\x05\x00\x00\x01" + socket.inet_aton(bnd) + struct.pack("!H", relay.getsockname()[1]))

        def inbound() -> None:
            while True:
                try:
                    d, peer = relay.recvfrom(65536)
                except OSError:
                    return
                if len(d) < 10 or d[0:3] != b"\x00\x00\x00" or d[3] != 1:
                    continue
                self.control_open_during_udp.append(self._control_open)
                client[0] = peer
                host, dport = socket.inet_ntoa(d[4:8]), struct.unpack("!H", d[8:10])[0]
                self.datagrams.append((host, dport, d[10:]))
                seen = self._per_destination.get((host, dport), 0) + 1
                self._per_destination[(host, dport)] = seen
                if self.silent or seen <= self.drop_first:
                    continue
                try:
                    out.sendto(d[10:], (host, dport))
                except OSError:
                    pass

        def outbound() -> None:
            while True:
                try:
                    d, src = out.recvfrom(65536)
                except OSError:
                    return
                if client[0] is None:
                    continue
                hdr = b"\x00\x00\x00\x01" + socket.inet_aton(src[0]) + struct.pack("!H", src[1])
                try:
                    relay.sendto(hdr + d, client[0])
                except OSError:
                    pass

        threading.Thread(target=inbound, daemon=True).start()
        threading.Thread(target=outbound, daemon=True).start()
        try:  # the association lives as long as the control connection
            c.settimeout(None)
            while c.recv(1024):
                pass
        except OSError:
            pass
        finally:
            relay.close()
            out.close()
