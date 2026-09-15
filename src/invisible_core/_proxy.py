"""Proxy translation: ONE reading of the endpoint, ONE road to the engine.

⛔ THERE USED TO BE THREE ROADS FOR ONE FACT - where does this session's
traffic go - and the scheme picked between them. SOCKS was routed by writing
``network.proxy.*``; HTTP/HTTPS was handed back for the Playwright driver to
route per channel; HTTP/HTTPS without a driver got different prefs and a
refusal whenever credentials were present. When the Node driver was removed the
middle road stopped existing, and ``proxy=`` with an ``http`` scheme was
accepted and dropped: the page resolved its own DNS and went out on the host
address while the timezone, the locale and the WebRTC candidate had all been
resolved THROUGH the proxy. Announcing one country and connecting from another
is the exact signal this package exists to avoid, manufactured by the package.

Reported from outside on 2026-08-30, and the report is what settled the design:
adding the missing call would have left two roads, which is the shape that
produced the defect. So the other roads are DELETED, not fixed.

**The one road is the engine's ``Browser.setBrowserProxy``.** It has always been
there, it carries credentials, and it covers ``http``, ``https``, ``socks`` and
``socks4`` alike - so the scheme decides nothing any more. ``parse_proxy`` reads
the endpoint once; whoever holds the protocol connection sends the command. A
caller with no connection is not given a second road: ``build_launch_plan``
refuses a proxy and names the wrapper.

What stays here is what was never routing: the prefs that close the channels a
proxied session can still leak through.
"""
from __future__ import annotations

from typing import Any, Dict, NamedTuple, Optional


#: What a caller may write on the left, the four names the engine's
#: ``setBrowserProxy`` declares on the right. ``socks5`` is what everybody
#: writes and is NOT one of them.
_PROXY_TYPES = {"http": "http", "https": "https",
                "socks": "socks", "socks5": "socks", "socks4": "socks4"}

#: The port Playwright documents as the default for each scheme.
_DEFAULT_PORT = {"http": 80, "https": 443, "socks": 1080, "socks4": 1080}


class ProxyEndpoint(NamedTuple):
    """One proxy, in the terms the engine uses. ``type`` is one of the four."""

    type: str
    host: str
    port: int
    username: str
    password: str
    bypass: tuple

    @property
    def is_socks(self) -> bool:
        return self.type in ("socks", "socks4")

    @property
    def has_credentials(self) -> bool:
        return bool(self.username or self.password)

    def as_engine_command(self) -> Dict[str, Any]:
        """The params of ``Browser.setBrowserProxy`` / ``setContextProxy``.

        Credentials are OMITTED rather than sent empty: the engine declares
        them Optional, and an empty string is a value, not an absence.
        """
        params: Dict[str, Any] = {"type": self.type, "host": self.host,
                                  "port": self.port, "bypass": list(self.bypass)}
        if self.username:
            params["username"] = self.username
        if self.password:
            params["password"] = self.password
        return params


def parse_proxy(proxy: Optional[Dict[str, Any]]) -> Optional[ProxyEndpoint]:
    """The ONE place that reads what the caller wrote. Raises, never guesses.

    ``None`` means "no proxy was asked for", which is the only case a caller may
    treat as "carry on". Anything present and unreadable RAISES, because the
    caller cannot otherwise tell that apart from a proxy that was silently
    dropped - and a dropped proxy has to stop the launch.
    """
    if not proxy:
        return None
    server = str(proxy.get("server") or "").strip()
    if not server or server.lower() == "direct://":
        return None

    rest = server
    scheme = "http"                     # Playwright's default for a bare host
    if "://" in rest:
        scheme, rest = rest.split("://", 1)
        scheme = scheme.lower()
    if scheme not in _PROXY_TYPES:
        raise ValueError(
            "proxy server %r uses scheme %r, which the engine cannot express. "
            "It takes one of: %s"
            % (server, scheme, ", ".join(sorted(_PROXY_TYPES))))
    kind = _PROXY_TYPES[scheme]

    rest = rest.split("/", 1)[0]
    if rest.startswith("["):            # an IPv6 literal keeps its brackets
        host, _, tail = rest.partition("]")
        host, port_text = host[1:], (tail[1:] if tail.startswith(":") else "")
    elif ":" in rest:
        # ⛔ `rpartition` ON THE COLON, and the branch has to be on whether a
        # colon EXISTS rather than on whether the head came back empty: with
        # the second test `http://:80` read as the host "80" on the default
        # port, which is a proxy nobody wrote. Caught by the known-bad case,
        # not by review.
        host, _, port_text = rest.rpartition(":")
    else:
        host, port_text = rest, ""
    if not host:
        raise ValueError("proxy server %r names no host" % server)

    if port_text:
        try:
            port = int(port_text)
        except ValueError:
            raise ValueError(
                "proxy server %r has a port that is not a number" % server)
        if not 1 <= port <= 65535:
            raise ValueError(
                "proxy server %r has a port outside 1-65535" % server)
    else:
        port = _DEFAULT_PORT[kind]

    bypass = proxy.get("bypass")
    if isinstance(bypass, str):
        bypass = [b.strip() for b in bypass.split(",") if b.strip()]
    return ProxyEndpoint(type=kind, host=host, port=port,
                         username=str(proxy.get("username") or ""),
                         password=str(proxy.get("password") or ""),
                         bypass=tuple(bypass or ()))


def configure_proxy(
    proxy: Optional[Dict[str, str]],
    prefs: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    """Validate the endpoint, close the leak channels, hand it on. NO routing.

    ⛔ THIS WRITES NO ``network.proxy.*``, AND THAT IS THE POINT. Routing is the
    engine command, sent by whoever holds the connection, for every scheme
    alike - see the module docstring for the three roads this replaced and the
    defect they produced.

    What remains is what was never routing: the prefs below close the channels
    a proxied session can still leak through. They were already applied to both
    schemes and they still are, unchanged.

    Returns the endpoint dict so the caller can hand it to the client that
    sends the command, ``None`` when no proxy was asked for. An unreadable
    endpoint RAISES: a caller cannot tell "none" from "dropped", and dropped
    has to stop the launch.
    """
    if parse_proxy(proxy) is None:
        return None

    # ⛔ `network.dns.disableIPv6 = True` WAS TRIED HERE AND REMOVED: IT IS INERT
    # BEHIND A PROXY, and a patch that moves no measurement is not a patch.
    #
    # The idea was to make the profile IPv4-only throughout: IPv6 WebRTC
    # candidates are already dropped behind a proxy (an IPv6 srflx is not
    # obscured by mDNS and would carry the REAL global address), but HTTP could
    # still leave over IPv6 - measured 2026-08-25 on a dual-stack residential
    # peer: we declared `73.209.132.45` while the same page reached an echo
    # service on `2603:300a:92e:8600:...`.
    #
    # The pref changes nothing, and the counterfactual proves it: applied and
    # READ BACK from the browser's profile (`network.dns.disableIPv6 true`), the
    # echo still came out on the same IPv6. The reason is `socks_remote_dns`:
    # Firefox hands the proxy the NAME, not an address, so it is the proxy that
    # resolves and picks the family. Firefox's resolver is never consulted at
    # all, and the same holds for an HTTP proxy, where the name travels inside
    # the CONNECT.
    #
    # Worth knowing: **the address family behind a proxy is not ours to decide.**
    # If the peer has IPv6 and the site is dual-stack, we leave over IPv6 while
    # WebRTC announces an IPv4. Actually closing that means ALSO declaring an
    # IPv6 srflx with the proxy's IPv6 exit (discoverable: `api6.ipify.org`
    # answers through the proxy), not switching IPv6 off from this side.

    result = dict(proxy)

    # ⛔ WHEN THE PROXY STUMBLES, FIREFOX'S FALLBACK IS TO GO OUT IN THE CLEAR.
    #
    # `network.proxy.allow_bypass` is `true` by default - read from our build's
    # GENERATED header, `dist/include/mozilla/StaticPrefList_network.h`, not from
    # the yaml - and a channel asking for `bypassProxy` skips `ResolveProxy()`
    # entirely (`netwerk/protocol/http/nsHttpChannel.cpp`, the `!BypassProxy()`
    # guard). That channel then resolves the name with the user's resolver,
    # because without `mProxyInfo` the DNS is FORCED with
    # `RESOLVE_IGNORE_SOCKS_DNS` (`DnsAndConnectSocket.cpp`; the upstream comment
    # says it: "force resolution despite global proxy-DNS configuration"). It is
    # not only DNS: it is a DIRECT connection, with the real IP, and it starts at
    # the very moment the proxy is already failing.
    #
    # Who uses it: `services/settings/Utils.sys.mjs` (`fallbackOrReject`, on
    # onerror/ontimeout/onabort) and `TelemetrySend.sys.mjs` (`retryRequest`).
    # Remote Settings runs every session, so the occasion is not rare.
    #
    # MEASURED 2026-08-25 with a local SOCKS5 that refuses those two hosts on
    # purpose and logs every CONNECT - and the log IS the control, because 98
    # CONNECTs all by NAME prove remote resolution was working:
    #
    #   without this pref ->  43 refusals, and `firefox.settings.services.mozilla.com`
    #                         RESOLVED 13 times on the home resolver
    #   with this pref    ->  45 refusals, and ZERO local resolutions; only
    #                         `127.0.0.1`, `local` and `localhost` remain, which
    #                         is the same set as a SOCKS5 that does not fail
    #
    # In both arms browsing stays fine and the relay forwards detectportal,
    # push.services, normandy and the rest as usual: the pref removes the
    # FALLBACK, not the traffic.
    #
    # ⛔ IT HOLDS FOR EVERY SCHEME, HTTP INCLUDED, and it sits AFTER the fork on
    # purpose: the two validations (the port, and credentials that cannot be
    # delivered) must be able to raise FIRST, leaving the dict as they found it.
    # The first draft put it before and dirtied the prefs on a malformed
    # endpoint: two existing tests went red and they were right.
    #
    # On the HTTP branch this is NOT a routing pref and does not touch
    # authentication: the contract pinned by
    # `test_the_delegating_path_does_NOT_change_behaviour` said "whoever
    # delegates writes no prefs" to protect ROUTING and 407, and it now says that
    # in those words instead of with `prefs == {}`.
    prefs["network.proxy.allow_bypass"] = False

    # ⛔ AND THE SECOND HALF, BECAUSE THE FIRST IS NOT ENOUGH ON HTTP.
    #
    # `allow_bypass` closes the CHANNELS that fall back to direct. But three
    # surfaces are not channels and call `AsyncResolveNative` without passing any
    # filter: `NetworkConnectivityService`, the DoH heuristic's probes, and the
    # ICE resolver. The engine's own gate
    # (`netwerk/dns/DNSServiceBase.cpp`, `DNSForbiddenByActiveProxy`) would stop
    # them, but it can only recognise a proxy written into the `network.proxy.*`
    # prefs - and on the HTTP branch we write none, because Playwright routes
    # per channel. So `network.proxy.type` stays 5 and the gate never arms.
    #
    # The engine cannot deduce it: we tell it. That is rule 1 - the core
    # declares, the engine obeys - applied to DNS.
    #
    # MEASURED 2026-08-25 behind an HTTP proxy, two identical rounds, with only
    # `allow_bypass` already active: `example.org` 28 remained, `ipv4only.arpa`
    # 12, `cloudflare-dns.com` 6 and - the worst - `stunprobe.invalid` 6, which
    # is a name chosen BY THE PAGE through `iceServers`. Behind SOCKS the same
    # names were already at 0.
    #
    # The proxy's own endpoint still resolves: both the SOCKS layer
    # (`nsSOCKSIOLayer`) and `DnsAndConnectSocket` ask for their resolution with
    # `RESOLVE_IGNORE_SOCKS_DNS`, which the gate exempts first. That is not an
    # exception we add: it is the one already holding up the SOCKS branch.
    prefs["zoom.stealth.dns.no_local_resolution"] = True

    # ⛔ AND ICE's UDP LEAVES ANYWAY, if the server is written as a NUMERIC
    # ADDRESS rather than a name. The DNS gate above cannot stop it, because a
    # literal never reaches the resolver.
    #
    # Measured 2026-08-26 across three providers and two schemes: with a numeric
    # STUN the engine receives the MAPPED-ADDRESS carrying the machine's REAL
    # address, while the page sees the proxy's exit because the real candidate is
    # rewritten. A leak no page-side check can see, and that only whoever runs
    # the STUN does.
    #
    # The two prefs sit here together on purpose: they tell the engine the same
    # thing from two sides - behind a proxy nothing leaves by a road the proxy
    # does not cover - and one decision emits both.
    prefs["zoom.stealth.webrtc.no_direct_udp"] = True
    return result


#: ⛔ NO, and this constant exists because the answer is not obvious and a
#: decision depends on it. The engine has the code for it
#: (`netwerk/socket/nsSOCKSUDPIOLayer.{h,cpp}`, hooked up in `nsUDPSocket.cpp`)
#: but it sits behind `network.proxy.socks_remote_udp`, which we do not set.
#: Without it, **UDP goes around the proxy**, and a STUN reached that way answers
#: with the machine's REAL address.
#:
#: Who reads it: `_geo._srflx_soppresso`. Giving up on declaring an srflx only
#: makes sense if the REAL one will be born with the right address, and with UDP
#: going around the proxy it would be born with the home address. In other words
#: the condition "the exit carries coherent UDP" is NOT enough: the browser also
#: has to send that UDP over there.
#:
#: The day the pref is switched on, this constant moves with it - and they are
#: one fact written in one place.
UDP_GOES_THROUGH_SOCKS = False

