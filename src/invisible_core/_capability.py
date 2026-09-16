"""What this session's exit can REALLY do, measured before the launch.

⛔ THIS IS PRODUCT, NOT MEASURING APPARATUS, and the difference shows in one
line: what this module answers DECIDES how the browser is configured. It was
born on the bench and lived there for a day; that file is now a pointer to this
one, because a fact computed in two places drifts.

**The rule the owner dictated on 2026-08-25, which simplifies everything:** what
is detected BEFORE the browser starts is the truth of the session, and it is
absolute for its lifetime. It is not re-checked, drift is not chased, and
whether the proxy stays sticky does not matter. Measure once, configure
accordingly.

⛔ CAPABILITIES ARE A PROPERTY OF THE GATEWAY, NOT OF THE SESSION, and that is
what makes probing them practical. Measured 2026-08-25 over 500 sessions: 95 live
peers on one provider all answer `rep=7`, 100 out of 100 on another, and two
gateways of the same account share 71 addresses out of 71 while giving DIFFERENT
answers. Changing peer does not change the answer; changing gateway does. So it
is probed once per endpoint and reused, rather than paying seconds at every
launch. Measured cost: 4-19 seconds, once every 24 hours per gateway.

⛔ AND ABSENCE OF PROOF ALWAYS FALLS ON THE CAUTIOUS SIDE. If the probe does not
answer, or answers ambiguously, the configuration is the usual one. A capability
is only exploited once it is DEMONSTRATED, because erring in that direction costs
the worst sentence a detector can write: measured by reading a real detector's
code, a browser that emits no candidates is given "Javascript is manipulated"
instead of "VPN detected".

No function here ever prints a credential.
"""
from __future__ import annotations

import json
import os
import re
import socket
import struct
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

#: Where what has been measured is remembered. Outside the git tree, like the
#: other secrets: the key contains the provider's hostname.
CACHE = os.path.join(os.environ.get("TEMP", "/tmp"), "exit_capability.json")

#: How long a measurement is worth before it is taken again. A gateway's
#: capabilities change when the provider changes infrastructure, not hour to
#: hour.
VALID_FOR_S = 24 * 3600

#: The SOCKS5 reply codes, in the words RFC 1928 uses for them.
_REP = {0: "ok", 1: "general failure", 2: "not allowed", 3: "network unreachable",
        4: "host unreachable", 5: "connection refused", 6: "ttl expired",
        7: "command not supported", 8: "address type not supported"}


# --------------------------------------------------------------------------
# The building blocks: one question each, and none of them interprets.
# --------------------------------------------------------------------------

def _greet_socks5(s, user: str, secret: str) -> Optional[str]:
    s.sendall(b"\x05\x01\x02")
    r = s.recv(2)
    if len(r) != 2 or r[0] != 5:
        return "not a SOCKS5 greeting"
    if r[1] == 2:
        u, p = user.encode(), secret.encode()
        s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        a = s.recv(2)
        if len(a) != 2 or a[1] != 0:
            return "authentication refused"
    elif r[1] == 0xFF:
        return "no acceptable authentication method"
    return None


def _connect_socks5(s, host: str, port: int) -> Optional[int]:
    h = host.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(h)]) + h + struct.pack("!H", port))
    r = s.recv(10)
    return r[1] if len(r) >= 2 and r[0] == 5 else None


def tcp_exit(proxy: Dict[str, str], timeout: float = 25) -> Optional[str]:
    """The address TCP leaves from, read in the clear over a single connection."""
    u = urlparse(proxy["server"])
    try:
        s = socket.create_connection((u.hostname, u.port), timeout=timeout)
        s.settimeout(timeout)
        # ⛔ An `https` scheme means TLS to the PROXY, before any request at all.
        # The first draft spoke cleartext HTTP to a TLS port and returned `None`,
        # which the summary showed as "stickiness undetermined": a defect of the
        # instrument dressed up as a property of the endpoint. The certificate is
        # verified properly, because that is what tells the right host from one
        # that merely looks like it.
        if u.scheme == "https":
            import ssl
            s = ssl.create_default_context().wrap_socket(s, server_hostname=u.hostname)
        try:
            if u.scheme.startswith("socks"):
                if _greet_socks5(s, proxy.get("username", ""), proxy.get("password", "")):
                    return None
                if _connect_socks5(s, "api.ipify.org", 80) != 0:
                    return None
                s.sendall(b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\n"
                          b"Connection: close\r\nUser-Agent: curl/8\r\n\r\n")
            else:
                import base64
                cred = base64.b64encode(
                    ("%s:%s" % (proxy.get("username", ""),
                                proxy.get("password", ""))).encode()).decode()
                s.sendall(("GET http://api.ipify.org/ HTTP/1.1\r\n"
                           "Host: api.ipify.org\r\nProxy-Authorization: Basic %s\r\n"
                           "Connection: close\r\nUser-Agent: curl/8\r\n\r\n" % cred).encode())
            data = b""
            while len(data) < 4096:
                p = s.recv(4096)
                if not p:
                    break
                data += p
            m = re.search(rb"\b(?:\d{1,3}\.){3}\d{1,3}\b", data.split(b"\r\n\r\n")[-1])
            return m.group(0).decode() if m else None
        finally:
            s.close()
    except OSError:
        return None


def udp_associate(proxy: Dict[str, str], timeout: float = 25):
    """`(supported, explanation)`. SOCKS5 only: HTTP has no such command."""
    u = urlparse(proxy["server"])
    if not u.scheme.startswith("socks"):
        return False, "scheme %s: SOCKS UDP ASSOCIATE does not exist in the protocol" % u.scheme
    try:
        s = socket.create_connection((u.hostname, u.port), timeout=timeout)
        s.settimeout(timeout)
        try:
            e = _greet_socks5(s, proxy.get("username", ""), proxy.get("password", ""))
            if e:
                return False, e
            s.sendall(b"\x05\x03\x00\x01" + b"\x00" * 4 + b"\x00\x00")
            r = s.recv(4)
            if not r:
                return False, "the gateway closed without answering"
            if len(r) < 4 or r[0] != 5:
                return False, "answer could not be read"
            if r[1] != 0:
                return False, "rep=%d %s" % (r[1], _REP.get(r[1], "?"))
            atyp = r[3]
            addr = (socket.inet_ntoa(s.recv(4)) if atyp == 1 else
                    s.recv(s.recv(1)[0]).decode() if atyp == 3 else
                    socket.inet_ntop(socket.AF_INET6, s.recv(16)))
            port = struct.unpack("!H", s.recv(2))[0]
            if addr in ("0.0.0.0", "::"):
                addr = u.hostname
            return True, "%s:%d" % (addr, port)
        finally:
            s.close()
    except OSError as ex:
        return False, "error %s" % type(ex).__name__


def udp_exit(proxy: Dict[str, str], timeout: float = 12) -> Optional[str]:
    """The address UDP leaves from, asked of STUN. `None` if UDP does not pass.

    This is the half almost nobody measures, and without it "the proxy carries
    UDP" is not enough: if this address is not the same as the TCP one, using it
    for WebRTC produces two addresses in one session, which is exactly the
    disagreement detectors look for.
    """
    ok, where = udp_associate(proxy)
    if not ok:
        return None
    host, _, port = where.rpartition(":")
    u = urlparse(proxy["server"])
    try:
        ctrl = socket.create_connection((u.hostname, u.port), timeout=timeout)
        ctrl.settimeout(timeout)
        if _greet_socks5(ctrl, proxy.get("username", ""), proxy.get("password", "")):
            ctrl.close()
            return None
        ctrl.sendall(b"\x05\x03\x00\x01" + b"\x00" * 4 + b"\x00\x00")
        ctrl.recv(10)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            h = b"stun.l.google.com"
            testa = b"\x00\x00\x00\x03" + bytes([len(h)]) + h + struct.pack("!H", 19302)
            s.sendto(testa + b"\x00\x01\x00\x00\x21\x12\xa4\x42" + os.urandom(12),
                     (host, int(port)))
            d, _ = s.recvfrom(2048)
            if len(d) < 10 or d[2] != 0:
                return None
            atyp, i = d[3], 4
            i += 4 if atyp == 1 else (1 + d[4] if atyp == 3 else 16)
            c = d[i + 2:]
            if len(c) < 20 or c[0:2] != b"\x01\x01":
                return None
            j, fine = 20, 20 + struct.unpack("!H", c[2:4])[0]
            while j + 4 <= min(fine, len(c)):
                t, ln = struct.unpack("!HH", c[j:j + 4])
                v = c[j + 4:j + 4 + ln]
                if t == 0x0020 and len(v) >= 8:
                    ip = bytes(a ^ b for a, b in zip(v[4:8], b"\x21\x12\xa4\x42"))
                    return socket.inet_ntoa(ip)
                j += 4 + ln + ((4 - ln % 4) % 4)
            return None
        finally:
            s.close()
            ctrl.close()
    except OSError:
        return None


def has_ipv6(proxy: Dict[str, str], timeout: float = 20) -> Optional[bool]:
    """`True`/`False`, or `None` when the question was not settled.

    ⛔ The `None` is NOT a `False`: a network failure and an exit without IPv6
    are different things, and flattening them would switch IPv6 off over a
    timeout.
    """
    u = urlparse(proxy["server"])
    if not u.scheme.startswith("socks"):
        return None
    try:
        s = socket.create_connection((u.hostname, u.port), timeout=timeout)
        s.settimeout(timeout)
        try:
            if _greet_socks5(s, proxy.get("username", ""), proxy.get("password", "")):
                return None
            if _connect_socks5(s, "api6.ipify.org", 80) != 0:
                return False
            s.sendall(b"GET / HTTP/1.1\r\nHost: api6.ipify.org\r\n"
                      b"Connection: close\r\nUser-Agent: curl/8\r\n\r\n")
            data = b""
            while len(data) < 4096:
                p = s.recv(4096)
                if not p:
                    break
                data += p
            corpo = data.split(b"\r\n\r\n")[-1]
            return b":" in corpo and len(corpo.strip()) > 2
        finally:
            s.close()
    except OSError:
        return None


def is_sticky(proxy: Dict[str, str], rounds: int = 6):
    """`(sticky, addresses)`. Six reads with the SAME credential.

    Six and not two: two identical draws in a row from an urn do not prove the
    urn holds a single ball, and that is a mistake this project has already made
    and written down.
    """
    visti = [tcp_exit(proxy) for _ in range(rounds)]
    buoni = [x for x in visti if x]
    distinti = sorted(set(buoni))
    if not buoni:
        return None, []
    return len(distinti) == 1, distinti


# --------------------------------------------------------------------------
# The verdict, and what is remembered
# --------------------------------------------------------------------------

def udp_is_usable(udp_allowed: Optional[bool],
                  udp_exit_ip: Optional[str],
                  tcp_exit_ip: Optional[str]) -> Optional[bool]:
    """Is this gateway's UDP USABLE, rather than merely GRANTED? Three answers.

    Measured, and the whole reason the distinction exists: a gateway grants the
    UDP ASSOCIATE and hands back a relay, and through that relay STUN never
    answers. Granted is not usable, so the grant on its own must never be read
    as a capability - `_geo._srflx_suppressed` requires this AND the grant
    before it stops declaring a candidate, and getting that wrong costs the
    worst sentence a detector writes.

    The three answers are not two:

    * ``None``  - the question does not apply. The gateway did not grant UDP
      ASSOCIATE, so there is nothing that could have been usable. Flattening it
      to ``False`` erases the difference between a gateway that refused and one
      that granted and then failed to carry a packet, which are different facts
      about the provider and different things to do about them.
    * ``False`` - granted and not usable: no UDP exit came back at all, or the
      one that did is not the address TCP leaves from.
    * ``True``  - granted, and the two exits are the same address.

    A top-level function, and not the inline expression it was inside
    ``measure``, because ``measure`` does network and so cannot be called by
    anything that wants to check this rule. The workbench gate that exists to
    prove exactly this line had copied it instead, and the copy fell behind the
    day the core was translated and the field names moved. One place computes
    it now; a caller that cannot afford the network calls this.
    """
    if not udp_allowed:
        return None
    return bool(udp_exit_ip) and udp_exit_ip == tcp_exit_ip


def _key(proxy: Dict[str, str]) -> str:
    u = urlparse(proxy["server"])
    return "%s://%s:%s" % (u.scheme, u.hostname, u.port)


def measure(proxy: Dict[str, str],
           *, known_tcp_exit: Optional[str] = None) -> Dict[str, Any]:
    """Everything that can be known about an exit without launching a browser.

    ``known_tcp_exit`` avoids measuring again what the caller already has.
    ``prepare_session_geo`` discovers the exit IP for the timezone and the
    locale, with one round trip through the proxy: doing it again here would be
    the same fact computed in two places, plus one more trip to an echo endpoint,
    which is non-browser traffic on the same exit.

    ⛔ STICKINESS IS NO LONGER HERE, and that is the owner's decision of
    2026-08-25: whether proxies are sticky is not interesting, because the truth
    is what is detected before the launch and it is absolute for the session.
    Removing it costs nothing and is worth it twice over:

    * it was **six network rounds out of eight** of this probe's cost;
    * and it **lied**. It reported `sticky = yes` for all four providers, while
      the same endpoint had been measured rotating **8 times in 25 minutes**. Six
      consecutive reads take a few seconds and rotation happens on a scale of
      minutes: that field could not see the phenomenon it was named after.

    ``is_sticky`` stays available for anyone who wants to MEASURE rotation over a
    suitable window, but it no longer enters any decision the product makes.
    """
    ip_tcp = known_tcp_exit or tcp_exit(proxy)
    ok_udp, why = udp_associate(proxy)
    ip_udp = udp_exit(proxy) if ok_udp else None
    return {
        "endpoint": _key(proxy),
        "schema": urlparse(proxy["server"]).scheme,
        "tcp_exit": ip_tcp,
        "udp": ok_udp,
        "udp_perche": why,
        "udp_exit": ip_udp,
        # Usable, not merely granted. The rule and the measurement behind it are
        # in `udp_is_usable`, which is where they live now: this used to be the
        # expression itself, and a gate that cannot call `measure` had a second
        # copy of it that went stale.
        "udp_matches_tcp": udp_is_usable(ok_udp, udp_exit_ip=ip_udp,
                                         tcp_exit_ip=ip_tcp),
        "ipv6": has_ipv6(proxy),
        "measured_at": int(time.time()),
    }


def capability(proxy: Dict[str, str], *, remeasure: bool = False,
             known_tcp_exit: Optional[str] = None) -> Dict[str, Any]:
    """The measurement, from cache when it is recent.

    Capabilities belong to the gateway rather than to the session, which is what
    makes caching them sound at all.
    """
    key = _key(proxy)
    remembered = {}
    try:
        with open(CACHE, encoding="utf-8") as f:
            remembered = json.load(f)
    except (OSError, ValueError):
        pass
    entry = remembered.get(key)
    if entry and not remeasure and (time.time() - entry.get("measured_at", 0)) < VALID_FOR_S:
        entry["from_cache"] = True
        return entry
    entry = measure(proxy, known_tcp_exit=known_tcp_exit)
    remembered[key] = entry
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(remembered, f, indent=1)
    except OSError:
        pass
    entry["from_cache"] = False
    return entry


def summarise(c: Dict[str, Any]) -> str:
    def _s(v):
        return "si" if v is True else ("no" if v is False else "non determinato")
    return ("%-46s udp=%-16s coerente=%-16s ipv6=%s"
            % (c["endpoint"], _s(c["udp"]), _s(c["udp_matches_tcp"]), _s(c["ipv6"])))
