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
    *,
    delegates_auth: bool = True,
) -> Optional[Dict[str, str]]:
    """Mutate ``prefs`` for SOCKS auth; return what to pass to Playwright.

    * ``None`` proxy → returns ``None``.
    * SOCKS proxy → writes the auth prefs and returns ``None`` (Playwright
      gets nothing; Firefox does the rest).
    * HTTP / HTTPS proxy → returns the dict unchanged for Playwright.

    ``delegates_auth`` is the caller stating a fact about ITSELF: whether it has
    a Playwright to hand an HTTP/HTTPS endpoint to. The wrapper does, so it
    leaves the default. ``build_launch_plan`` does not - it spawns the binary
    with ``subprocess`` - and passes ``False``.

    Why the caller declares it instead of this function guessing: for six weeks
    the direct-launch path called this function, discarded the returned dict
    because it had nowhere to put it, and launched a browser with NO proxy
    configuration at all. The session then went out on the host's own address
    while ``_geo`` had already resolved timezone and locale THROUGH the proxy,
    so the page announced one country and connected from another. Nothing
    raised, nothing logged. Same failure shape the no-port branch below was
    fixed for on 2026-08-01, on the other scheme.
    """
    if not proxy:
        return None

    server = (proxy.get("server") or "").strip()
    if not server or server.lower() == "direct://":
        return None
    if not _is_socks_scheme(server):
        return _configure_http_like(proxy, prefs, server, delegates_auth)

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


def _configure_http_like(
    proxy: Dict[str, str],
    prefs: Dict[str, Any],
    server: str,
    delegates_auth: bool,
) -> Optional[Dict[str, str]]:
    """An HTTP/HTTPS endpoint, for a caller that may or may not have Playwright.

    Routing and authentication are two different problems here, and only the
    second one needs Playwright:

    * ROUTING is pure prefs, and it was measured working on the shipped binary:
      with ``network.proxy.type`` plus ``http``/``http_port``/``ssl``/``ssl_port``
      the browser goes to the proxy and does NOT fall back to direct.
    * AUTHENTICATION is not. The credentials we write for SOCKS reach the
      ``nsProxyInfo``, but nothing builds a ``Proxy-Authorization`` header out of
      them, so an authenticated endpoint stops at the 407 and Gecko reports
      ``NS_ERROR_PROXY_CONNECTION_REFUSED``. Playwright's own proxy support is
      what answers that challenge today.

    Hence: a caller that can delegate keeps the previous behaviour exactly, so
    the path measured green stays untouched. A caller that cannot gets the
    routing prefs, and a REFUSAL when credentials are present - because the one
    thing that must never happen again is launching unproxied while believing
    otherwise.
    """
    if delegates_auth:
        return proxy

    host_port = _strip_scheme(server)
    if ":" not in host_port:
        raise ValueError(
            f"proxy server {server!r} has no port. An HTTP endpoint needs "
            f"host:port - e.g. http://host:8080")
    host, port_str = host_port.rsplit(":", 1)

    if proxy.get("username") or proxy.get("password"):
        raise ValueError(
            f"proxy server {server!r} carries credentials, and this launch path "
            f"cannot deliver them: it starts the binary directly, so there is no "
            f"Playwright to answer the proxy's 407, and the browser has no pref "
            f"that injects Proxy-Authorization (only SOCKS has that). Use a SOCKS "
            f"endpoint here, or drive this proxy through invisible_playwright, "
            f"which does answer the challenge. Refusing rather than launching "
            f"unproxied, which is what this used to do silently")

    prefs["network.proxy.type"]      = 1
    prefs["network.proxy.http"]      = host
    prefs["network.proxy.http_port"] = int(port_str)
    prefs["network.proxy.ssl"]       = host
    prefs["network.proxy.ssl_port"]  = int(port_str)
    return None


def _is_socks_scheme(server: str) -> bool:
    return server.lower().startswith(_SOCKS_SCHEMES)


def _strip_scheme(server: str) -> str:
    return server.split("://", 1)[1] if "://" in server else server
