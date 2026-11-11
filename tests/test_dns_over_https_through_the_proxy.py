"""Behind a proxy every name is resolved by Firefox's own DNS-over-HTTPS, and that request leaves through the proxy.

Today the core writes ``zoom.stealth.dns.no_local_resolution``, a switch for an engine gate that forbids every non-literal
name: the page's ICE server names then never resolve at all. The rule now: the engine has no gate; the session declares
TRR mode 3 (DNS-over-HTTPS only, no fallback to the system resolver), the default Mozilla DoH provider, and the address of
that provider's host so that finding it needs no DNS (``network.trr.bootstrapAddr``). The DoH request is an ordinary channel
and goes through the session's proxy road. Only a proxied session declares it: without a proxy the exit is the machine's own
network and retail resolves with the system resolver (mode 0).

``socks_remote_dns`` and ``socks5_remote_dns`` are not declared either: a Juggler session writes no proxy endpoint to the
prefs (it marks every proxy "resolves the host" itself), so they governed a road that does not exist.
"""
from __future__ import annotations

import ipaddress
import pathlib

import pytest

from invisible_core import configure_proxy, get_default_stealth_prefs
from invisible_core._fpforge import generate_profile
from invisible_core.prefs import compose_session_prefs

pytestmark = pytest.mark.unit

_PROXIES = ("socks5://h:1080", "socks4://h:1080", "http://h:8080", "https://h:8443")
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "invisible_core"


@pytest.mark.parametrize("server", _PROXIES)
def test_a_proxied_session_declares_doh_only(server):
    prefs = {}
    configure_proxy({"server": server}, prefs)
    assert prefs["network.trr.mode"] == 3, "TRR only: no fallback to the resolver of the machine"
    assert prefs["network.trr.uri"] == "https://mozilla.cloudflare-dns.com/dns-query"
    assert ipaddress.ip_address(prefs["network.trr.bootstrapAddr"]), "the DoH host is found without DNS"


@pytest.mark.parametrize("server", _PROXIES)
def test_the_old_dns_gate_switch_is_gone(server):
    prefs = {}
    configure_proxy({"server": server}, prefs)
    assert "zoom.stealth.dns.no_local_resolution" not in prefs


def test_a_session_without_a_proxy_keeps_the_system_resolver():
    """No proxy: the retail default (TRR off, mode 0) and no DoH key at all."""
    for proxy in (None, {}, {"server": ""}, {"server": "direct://"}):
        prefs = {}
        configure_proxy(proxy, prefs)
        assert not [k for k in prefs if k.startswith("network.trr.")], (proxy, prefs)


def test_the_composed_prefs_of_a_proxied_and_of_a_direct_session():
    proxied = compose_session_prefs(generate_profile(seed=7), timezone="America/New_York",
                                    proxy={"server": "socks5://gw:1080"}).prefs
    direct = compose_session_prefs(generate_profile(seed=7), timezone="America/New_York").prefs
    assert proxied["network.trr.mode"] == 3
    assert not [k for k in direct if k.startswith("network.trr.")]


def test_the_baseline_declares_no_socks_remote_dns():
    base = get_default_stealth_prefs(7, timezone="America/New_York", locale="en-US")
    assert "network.proxy.socks_remote_dns" not in base
    assert "network.proxy.socks5_remote_dns" not in base


def test_no_source_file_declares_the_gate_switch_or_the_remote_dns_prefs():
    """No string constant of the package IS one of these pref names (comments and prose may still mention them)."""
    import ast

    names = {"zoom.stealth.dns.no_local_resolution", "network.proxy.socks_remote_dns",
             "network.proxy.socks5_remote_dns"}
    spelled = [(p.name, n.value) for p in _SRC.rglob("*.py")
               for n in ast.walk(ast.parse(p.read_bytes()))
               if isinstance(n, ast.Constant) and n.value in names]
    assert not spelled, spelled
