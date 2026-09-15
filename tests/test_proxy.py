"""Unit tests for `invisible_core._proxy.configure_proxy`.

Decision-table coverage of every input partition: None/empty/direct,
SOCKS4/5/default, HTTP/HTTPS, case variants, malformed, mutation contract.
"""
# MOVED FROM invisible_playwright/tests/ ON 2026-07-27.
#
# Every test in this file exercises code in THIS package and reached it through
# a four-line back-compat shim in the wrapper. That is not where coverage for a
# module belongs, and it was not academic: measured on 2026-07-27, six realistic
# one-line breaks in core code SURVIVED the core's own suite and were caught
# only by the wrapper's - `cloak_prefs()` returning {}, SOCKS detection always
# False, the scheme never stripped from a proxy server, `_proxy_is_set` always
# True, the locale always en-US, `get_default_args()` injecting -headless. The
# core's pre-push gate and its publish gate were both green over all six.
#
# `test_no_test_reaches_the_core_through_a_shim` in the wrapper keeps them here.
import pytest

from invisible_core._proxy import configure_proxy, parse_proxy


# ──────────────────────────────────────────────────────────────────────
#  CP1-CP7: no-op cases - return None, do NOT mutate prefs
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp1_none_proxy_returns_none():
    prefs = {}
    assert configure_proxy(None, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp2_empty_dict_returns_none():
    prefs = {}
    assert configure_proxy({}, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp3_empty_server_returns_none():
    prefs = {}
    assert configure_proxy({"server": ""}, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp4_whitespace_server_returns_none():
    prefs = {}
    assert configure_proxy({"server": "  "}, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp5_direct_scheme_returns_none():
    prefs = {}
    assert configure_proxy({"server": "direct://"}, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp6_direct_scheme_uppercase_returns_none():
    prefs = {}
    assert configure_proxy({"server": "DIRECT://"}, prefs) is None
    assert prefs == {}


@pytest.mark.unit
def test_cp7_direct_scheme_mixed_case_returns_none():
    prefs = {}
    assert configure_proxy({"server": "DiReCt://"}, prefs) is None
    assert prefs == {}


# ──────────────────────────────────────────────────────────────────────
#  CP8-CP9: HTTP/HTTPS - passthrough (return proxy unchanged, no mutation)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp8_http_proxy_passthrough():
    prefs = {}
    proxy = {"server": "http://proxy:8080"}
    result = configure_proxy(proxy, prefs)
    assert result == proxy
    # No SOCKS-related mutations.
    assert "network.proxy.type" not in prefs
    assert "network.proxy.socks" not in prefs


@pytest.mark.unit
def test_cp9_https_proxy_passthrough():
    prefs = {}
    proxy = {"server": "https://proxy:8080"}
    result = configure_proxy(proxy, prefs)
    assert result == proxy
    assert "network.proxy.type" not in prefs


@pytest.mark.unit
def test_cp8b_http_with_username_password_passthrough():
    """HTTP proxies preserve username/password for Playwright to consume."""
    prefs = {}
    proxy = {"server": "http://proxy:8080", "username": "user", "password": "pw"}
    result = configure_proxy(proxy, prefs)
    assert result == proxy
    assert "network.proxy.type" not in prefs


# ──────────────────────────────────────────────────────────────────────
#  One road: `parse_proxy` reads, the engine's command routes
#
#  The tests that used to sit here asserted the opposite - that one
#  scheme wrote `network.proxy.*` and another returned a dict - and they
#  were correct while there were three roads. They were deleted with the
#  roads, not adapted: a test describing a mechanism that no longer
#  exists is worse than no test, because it passes.
#
#  What those tests really protected survives here: no endpoint is
#  dropped in silence, and no scheme is treated differently from another.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("server,expected", [
    ("socks5://host:1080", ("socks", "host", 1080)),
    ("SOCKS5://HOST:1080", ("socks", "HOST", 1080)),   # the scheme is case-blind
    ("socks4://host:1080", ("socks4", "host", 1080)),
    ("socks://host:1080",  ("socks", "host", 1080)),
    ("http://host:8080",   ("http", "host", 8080)),
    ("https://host:3128",  ("https", "host", 3128)),
    ("host:9999",          ("http", "host", 9999)),    # no scheme: http
    ("socks5://host",      ("socks", "host", 1080)),   # no port: the default
    ("http://host",        ("http", "host", 80)),
    ("  socks5://host:1080  ", ("socks", "host", 1080)),
])
def test_every_scheme_is_read_by_the_SAME_reader(server, expected):
    """Four types, one reader. This was the difference that did the damage."""
    ep = parse_proxy({"server": server})
    assert (ep.type, ep.host, ep.port) == expected


@pytest.mark.unit
def test_a_bracketed_ipv6_stays_whole():
    """The reader strips the brackets; the colons inside are not the port."""
    ep = parse_proxy({"server": "socks5://[2001:db8::1]:1080"})
    assert ep.host == "2001:db8::1" and ep.port == 1080


@pytest.mark.unit
def test_credentials_reach_the_command_on_EVERY_scheme():
    """The HTTP branch REFUSED them, because prefs cannot carry them.

    The engine's command can, and the same line now holds for all four: the
    limitation belonged to the road, not to the protocol.
    """
    for server in ("http://h:1", "https://h:1", "socks5://h:1", "socks4://h:1"):
        cmd = parse_proxy({"server": server, "username": "u",
                           "password": "p"}).as_engine_command()
        assert cmd["username"] == "u" and cmd["password"] == "p", server


@pytest.mark.unit
def test_an_empty_credential_is_not_sent():
    """The engine declares them Optional: "" is a value, not an absence."""
    cmd = parse_proxy({"server": "http://h:1", "username": "",
                       "password": None}).as_engine_command()
    assert "username" not in cmd and "password" not in cmd


@pytest.mark.unit
@pytest.mark.parametrize("server", [
    "ftp://h:1",        # a scheme the engine cannot express
    "http://:80",       # no host - read as host "80" by the reader's first
                        # version, and caught by the known-bad case
    "socks5://h:abc",   # a port that is not a number
    "socks5://h:0",     # out of range
    "http://h:99999",
])
def test_an_unreadable_endpoint_RAISES_instead_of_vanishing(server):
    """The shape of the defect, in miniature.

    A caller cannot tell "no proxy" apart from "the proxy was thrown away", and
    the second case has to stop the launch.
    """
    with pytest.raises(ValueError):
        parse_proxy({"server": server})


@pytest.mark.unit
def test_no_scheme_writes_routing_prefs():
    """The CLASS, not the single name: no routing `network.proxy.*` at all.

    Asserted on all four schemes together, because the regression came from
    exactly the fact that two schemes did different things.
    """
    forbidden = ("network.proxy.type", "network.proxy.socks",
               "network.proxy.socks_port", "network.proxy.socks_version",
               "network.proxy.socks_username", "network.proxy.socks_password",
               "network.proxy.socks_remote_dns", "network.proxy.http",
               "network.proxy.http_port", "network.proxy.ssl",
               "network.proxy.ssl_port")
    for server in ("socks5://h:1080", "socks4://h:1080", "http://h:8080",
                   "https://h:3128"):
        prefs = {}
        returned = configure_proxy({"server": server, "username": "u",
                                    "password": "p"}, prefs)
        assert returned is not None, server
        for name in forbidden:
            assert name not in prefs, (server, name)


@pytest.mark.unit
def test_the_escape_channel_prefs_stay_on_EVERY_scheme():
    """What was not routing was left untouched by the unification."""
    for server in ("socks5://h:1080", "http://h:8080"):
        prefs = {}
        configure_proxy({"server": server}, prefs)
        assert prefs["network.proxy.allow_bypass"] is False, server
        assert prefs["zoom.stealth.dns.no_local_resolution"] is True, server
        assert prefs["zoom.stealth.webrtc.no_direct_udp"] is True, server


@pytest.mark.unit
def test_the_direct_launch_REFUSES_a_proxy_instead_of_inventing_a_road():
    """It has no protocol connection, so it has no proxy.

    It used to write prefs of its own, and that is the third road: the one that
    made forgetting one possible. Refusing is the honest alternative, and it
    arrives BEFORE timezone and locale are resolved through that proxy.
    """
    from invisible_core.launch import build_launch_plan

    with pytest.raises(ValueError) as caught:
        build_launch_plan(1, profile_dir="/does/not/matter",
                          proxy={"server": "http://h:8080"})
    message = str(caught.value)
    assert "invisible_playwright" in message, message


@pytest.mark.unit
def test_the_direct_launch_without_a_proxy_is_untouched():
    """The case that must NOT fire: `direct://` and absence are not a proxy."""
    from invisible_core import parse_proxy as _pp

    for nothing in (None, {}, {"server": ""}, {"server": "direct://"}):
        assert _pp(nothing) is None


@pytest.mark.unit
def test_behind_any_proxy_the_direct_fallback_is_off():
    """Firefox's fallback from a failing proxy is a DIRECT connection.

    `network.proxy.allow_bypass` is `true` by default in our build, and a
    channel asking for `bypassProxy` skips `ResolveProxy()`: it resolves with
    the user's resolver and connects from their IP, precisely while the proxy is
    failing. The two consumers are Remote Settings' `fallbackOrReject` and
    telemetry's `retryRequest`, and Remote Settings runs on every session.

    Measured 2026-08-25 with a local SOCKS5 that refuses those two hosts on
    purpose: without the pref, 43 refusals and the Remote Settings host resolved
    13 times locally; with the pref, 45 refusals and ZERO local resolutions, that
    is, the same set of names as a SOCKS5 that does not fail. Browsing stays fine
    in both arms.

    It is asserted on EVERY SCHEME, not on SOCKS alone: the defect does not
    depend on the SOCKS version, nor even on it being SOCKS. Owner decision
    2026-08-25, "we have to allow using all the protocols": the defence holds
    everywhere, instead of being a reason to exclude a scheme.
    """
    from invisible_core import configure_proxy

    for server in ("socks5://h:1080", "socks4://h:1080", "socks://h:1080",
                   "http://h:8080", "https://h:8443"):
        prefs = {}
        configure_proxy({"server": server}, prefs)
        assert prefs.get("network.proxy.allow_bypass") is False, (
            f"{server}: without this pref, a proxy failure sends Firefox out "
            f"directly with the real IP")


def test_behind_any_proxy_local_dns_is_declared_forbidden():
    """`allow_bypass` closes the CHANNELS. This closes what is not a channel.

    Three surfaces call `AsyncResolveNative` directly and pass through no filter:
    `NetworkConnectivityService`, the DoH heuristics probes, and the ICE
    resolver. The engine's gate would stop them
    (`netwerk/dns/DNSServiceBase.cpp`, `DNSForbiddenByActiveProxy`) but it only
    recognises a proxy written into `network.proxy.*`, and on the HTTP branch we
    write none. So the core declares the fact: rule 1.

    The worst of the three is the ICE resolver, because THE PAGE chooses the name
    via `iceServers`: without this pref, behind an HTTP proxy a site can make the
    browser resolve a name of its own and watch which resolver it asks - the one
    at home - while the HTTP leaves through the proxy.

    Measured 2026-08-25 behind an HTTP proxy, two identical runs: without the
    pref 21 names resolved locally, among them `stunprobe.invalid` 6 times; with
    the pref **3**, that is `local`, `localhost` and the proxy's endpoint - the
    exact same shape as the SOCKS arm. The no-proxy arm stays at 28 names: the
    pref is not written there and the engine behaves like upstream.
    """
    from invisible_core import configure_proxy

    for server in ("socks5://h:1080", "socks4://h:1080", "socks://h:1080",
                   "http://h:8080", "https://h:8443"):
        prefs = {}
        configure_proxy({"server": server}, prefs)
        assert prefs.get("zoom.stealth.dns.no_local_resolution") is True, (
            f"{server}: without this declaration a PAGE can make the browser "
            f"resolve a name of its choosing on the resolver at home")


def test_without_a_proxy_the_fallback_is_not_declared():
    """The case that must NOT fire, and it is worth as much as the one that does.

    With no proxy there is nothing to bypass, so the pref has nothing to govern
    and declaring it would be a divergence from retail paid for nothing. Same
    shape for `direct://`, which is a way of saying "no proxy".
    """
    from invisible_core import configure_proxy

    for proxy in (None, {}, {"server": ""}, {"server": "direct://"}):
        prefs = {}
        configure_proxy(proxy, prefs)
        assert "network.proxy.allow_bypass" not in prefs, (
            f"{proxy!r}: pref declared where there is no proxy to bypass")
        assert "zoom.stealth.dns.no_local_resolution" not in prefs, (
            f"{proxy!r}: with no proxy, local DNS is the NORMAL path, and "
            f"forbidding it would be a divergence from retail paid for nothing")


def test_a_malformed_endpoint_leaves_no_half_written_prefs():
    """A refusal has to leave the dict the way it found it.

    The first draft wrote `allow_bypass` BEFORE validating the port, so a
    malformed endpoint raised leaving a pref behind: a partially configured dict
    the caller can use believing it untouched. Two existing tests went red and
    they were the ones in the right.

    ⛔ The input changed on 2026-08-30 and the reason should be said: it used to
    be `socks5://noport`, because a SOCKS with no port was REFUSED. Now a missing
    port takes the scheme's default, as Playwright documents, and it can do so
    without risk precisely because of the unification: there is no longer a path
    that starts anyway without a proxy - either the command is sent, or the
    launch fails. So a genuinely unreadable endpoint is what is needed here.
    """
    import pytest
    from invisible_core import configure_proxy

    prefs = {}
    with pytest.raises(ValueError):
        configure_proxy({"server": "socks5://host:not-a-number"}, prefs)
    assert prefs == {}, f"prefs dirtied by the refusal: {prefs}"
