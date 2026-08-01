"""Proxy translation shared by sync and async launchers.

SOCKS proxies are driven entirely by the patched Firefox prefs (the
``nsProtocolProxyService`` patch reads ``network.proxy.socks_username``
and ``socks_password``). HTTP/HTTPS proxies go through Playwright's own
``proxy=`` kwarg so it can negotiate Basic auth.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


_SOCKS_SCHEMES = ("socks5://", "socks4://", "socks://")


def configure_proxy(
    proxy: Optional[Dict[str, str]],
    prefs: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    """Mutate ``prefs`` for SOCKS auth; return what to pass to Playwright.

    * ``None`` proxy → returns ``None``.
    * SOCKS proxy → writes the auth prefs and returns ``None`` (Playwright
      gets nothing; Firefox does the rest).
    * HTTP / HTTPS proxy → returns the dict unchanged for Playwright.
    """
    if not proxy:
        return None

    server = (proxy.get("server") or "").strip()
    if not server or server.lower() == "direct://":
        return None
    if not _is_socks_scheme(server):
        return proxy

    host_port = _strip_scheme(server)
    if ":" not in host_port:
        # It used to `return None  # malformed, drop silently`, and a test named
        # test_cp14_socks_without_port_dropped_silently pinned that. Changed
        # 2026-08-01 after reading what the silence costs: the caller asked for a
        # proxy, no network.proxy.* pref is written, and the session goes out on
        # the host's own address believing it is proxied. For this package that
        # is the worst outcome there is, and it is invisible - the browser
        # launches, the page loads, the IP is wrong.
        #
        # The other parser disagreed too: _geo builds `socks5h://host` from the
        # same dict and hands it to requests, so one half of a session was
        # proxied and the other was not.
        raise ValueError(
            f"proxy server {server!r} has no port. A SOCKS endpoint needs "
            f"host:port - e.g. socks5://host:1080. Refusing rather than "
            f"launching unproxied, which is what this used to do silently")

    host, port_str = host_port.rsplit(":", 1)
    prefs["network.proxy.type"]            = 1
    prefs["network.proxy.socks"]           = host
    prefs["network.proxy.socks_port"]      = int(port_str)
    prefs["network.proxy.socks_version"]   = 4 if server.lower().startswith("socks4://") else 5
    prefs["network.proxy.socks_username"]  = proxy.get("username") or ""
    prefs["network.proxy.socks_password"]  = proxy.get("password") or ""
    prefs["network.proxy.socks_remote_dns"] = True
    return None


def _is_socks_scheme(server: str) -> bool:
    return server.lower().startswith(_SOCKS_SCHEMES)


def _strip_scheme(server: str) -> str:
    return server.split("://", 1)[1] if "://" in server else server
