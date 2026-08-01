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

from invisible_core._proxy import configure_proxy


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
#  CP10-CP13: SOCKS - mutate prefs, return None
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp10_socks5_with_credentials():
    prefs = {}
    proxy = {
        "server": "socks5://host:1080",
        "username": "u",
        "password": "p",
    }
    result = configure_proxy(proxy, prefs)
    assert result is None
    assert prefs["network.proxy.type"] == 1
    assert prefs["network.proxy.socks"] == "host"
    assert prefs["network.proxy.socks_port"] == 1080
    assert prefs["network.proxy.socks_version"] == 5
    assert prefs["network.proxy.socks_username"] == "u"
    assert prefs["network.proxy.socks_password"] == "p"
    assert prefs["network.proxy.socks_remote_dns"] is True


@pytest.mark.unit
def test_cp11_socks4_sets_version_4():
    prefs = {}
    configure_proxy({"server": "socks4://host:1080"}, prefs)
    assert prefs["network.proxy.socks_version"] == 4


@pytest.mark.unit
def test_cp12_bare_socks_defaults_to_v5():
    prefs = {}
    configure_proxy({"server": "socks://host:1080"}, prefs)
    assert prefs["network.proxy.socks_version"] == 5


@pytest.mark.unit
def test_cp13_socks_scheme_is_case_insensitive():
    prefs = {}
    proxy = {"server": "SOCKS5://HOST:1080"}
    result = configure_proxy(proxy, prefs)
    assert result is None
    assert prefs["network.proxy.type"] == 1
    # Host preserves case (only the scheme is case-folded).
    assert prefs["network.proxy.socks"] == "HOST"
    assert prefs["network.proxy.socks_version"] == 5


# ──────────────────────────────────────────────────────────────────────
#  CP14-CP15: edge SOCKS inputs
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp14_socks_without_port_is_refused_not_dropped():
    """This test asserted the opposite until 2026-08-01, and it was wrong.

    A SOCKS server with no port wrote no `network.proxy.*` pref and returned
    None, which the caller reads as "SOCKS handled, Playwright needs nothing".
    The session then went out on the host's own address while the caller
    believed it was proxied. Silent, and for this package the worst outcome
    available: the browser launches, the page loads, the IP is wrong.

    The two parsers disagreed as well - `_geo` builds `socks5h://host` from the
    same dict and gives it to requests - so a malformed endpoint could leave one
    half of a session proxied and the other half not.

    Refusing costs a caller with a typo an exception at launch. That is the
    trade, and it is the right way round.
    """
    prefs = {}
    with pytest.raises(ValueError) as exc:
        configure_proxy({"server": "socks5://hostonly"}, prefs)
    assert "no port" in str(exc.value)
    assert prefs == {}, "a refused proxy must not leave half its prefs behind"


@pytest.mark.unit
def test_cp15_socks_without_credentials_uses_empty_strings():
    prefs = {}
    configure_proxy({"server": "socks5://host:1080"}, prefs)
    assert prefs["network.proxy.socks_username"] == ""
    assert prefs["network.proxy.socks_password"] == ""


@pytest.mark.unit
def test_cp15b_socks_with_none_credentials_uses_empty_strings():
    """`proxy.get("username")` returning None should resolve to ""."""
    prefs = {}
    configure_proxy(
        {"server": "socks5://host:1080", "username": None, "password": None},
        prefs,
    )
    assert prefs["network.proxy.socks_username"] == ""
    assert prefs["network.proxy.socks_password"] == ""


# ──────────────────────────────────────────────────────────────────────
#  CP16: mutation contract - prefs dict mutated in-place
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp16_prefs_mutated_in_place():
    """Caller's prefs dict receives the SOCKS keys directly (not a copy)."""
    prefs = {"existing.pref": "kept"}
    sentinel = prefs
    configure_proxy({"server": "socks5://host:1080"}, prefs)
    # Same object identity - mutated, not replaced.
    assert prefs is sentinel
    # Existing pref preserved.
    assert prefs["existing.pref"] == "kept"
    # SOCKS keys added.
    assert "network.proxy.type" in prefs
    assert "network.proxy.socks" in prefs


# ──────────────────────────────────────────────────────────────────────
#  CP17: boundary - IPv6-style host preserved via rsplit
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cp17_ipv6_bracketed_host_preserved_via_rsplit():
    """rsplit(':', 1) keeps brackets intact for `[::1]:1080`-style hosts."""
    prefs = {}
    configure_proxy({"server": "socks5://[::1]:1080"}, prefs)
    assert prefs["network.proxy.socks"] == "[::1]"
    assert prefs["network.proxy.socks_port"] == 1080


# ──────────────────────────────────────────────────────────────────────
#  Recheck additions - branches discovered while re-reading _proxy.py
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_socks_with_surrounding_whitespace_in_server_stripped():
    """The implementation strips whitespace before scheme checks."""
    prefs = {}
    result = configure_proxy({"server": "  socks5://host:1080  "}, prefs)
    assert result is None
    assert prefs["network.proxy.socks"] == "host"
    assert prefs["network.proxy.socks_port"] == 1080


@pytest.mark.unit
def test_server_key_missing_returns_none():
    """No 'server' key → treated as empty → no-op."""
    prefs = {}
    result = configure_proxy({"username": "u"}, prefs)
    assert result is None
    assert prefs == {}


@pytest.mark.unit
def test_server_key_none_returns_none():
    """`server: None` is normalized to "" by the implementation."""
    prefs = {}
    result = configure_proxy({"server": None}, prefs)
    assert result is None
    assert prefs == {}


@pytest.mark.unit
def test_socks_port_coerced_to_int():
    """Port string is parsed via int() - not a numeric string."""
    prefs = {}
    configure_proxy({"server": "socks5://host:443"}, prefs)
    assert prefs["network.proxy.socks_port"] == 443
    assert isinstance(prefs["network.proxy.socks_port"], int)


@pytest.mark.unit
def test_socks_non_numeric_port_raises_value_error():
    """Non-numeric port is a programmer error - int() raises."""
    prefs = {}
    with pytest.raises(ValueError):
        configure_proxy({"server": "socks5://host:notaport"}, prefs)


@pytest.mark.unit
def test_the_two_proxy_parsers_agree_on_what_is_usable():
    """One dict, two readers, and nothing compared them.

    `configure_proxy` writes the browser's prefs; `_geo._proxies_for` builds the
    requests URL for the egress lookup. A dict that one accepts and the other
    rejects means half a session is proxied - which is what a portless SOCKS
    endpoint used to produce.
    """
    from invisible_core import _geo

    for server in ("socks5://hostonly", "socks4://hostonly", "socks://hostonly"):
        proxy = {"server": server}
        with pytest.raises(ValueError):
            configure_proxy(proxy, {})
        with pytest.raises(ValueError):
            _geo._proxies_for_requests(proxy)

    for server in ("socks5://host:1080", "http://host:8080", "https://host:8080"):
        proxy = {"server": server}
        configure_proxy(proxy, {})          # must not raise
        assert _geo._proxies_for_requests(proxy)     # must produce a usable URL
