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
    assert prefs["network.trr.exclude-etc-hosts"] is False, "the hosts file of the machine answers nothing"


@pytest.mark.parametrize("server", _PROXIES)
def test_the_old_dns_gate_switch_is_gone(server):
    prefs = {}
    configure_proxy({"server": server}, prefs)
    assert "zoom.stealth.dns.no_local_resolution" not in prefs


@pytest.mark.parametrize("server,host", [("socks5://gw.example.test:1080", "gw.example.test"),
                                         ("http://proxy.example.test:8080", "proxy.example.test"),
                                         ("https://secure.example.test:8443", "secure.example.test")])
def test_a_proxy_named_by_a_name_is_the_one_name_the_system_resolver_answers(server, host):
    """Resolving the proxy's own name through DoH would need the proxy: circular. Like an app at home configured with a
    proxy, Firefox resolves ONLY that name with the system resolver, so it goes in ``network.trr.excluded-domains`` (the
    built-in ``localhost,local`` list is a separate pref). The TLS name of an https proxy stays the name."""
    prefs = {}
    configure_proxy({"server": server}, prefs)
    assert prefs["network.trr.excluded-domains"] == host


@pytest.mark.parametrize("server", ["socks5://203.0.113.9:1080", "http://203.0.113.9:8080", "https://203.0.113.9:8443",
                                    "socks5://[2001:db8::9]:1080"])
def test_a_proxy_given_by_an_address_excludes_no_name(server):
    prefs = {}
    configure_proxy({"server": server}, prefs)
    assert "network.trr.excluded-domains" not in prefs


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


def test_the_cached_parental_controls_state_is_declared_off_once():
    """The engine answers 'parental controls off' from its own service, but upstream reads the cached pref once before
    that answer is written back: a profile that cached ``true`` would send names to the system resolver until then."""
    base = get_default_stealth_prefs(7, timezone="America/New_York", locale="en-US")
    assert base["network.parental_controls_cached_state"] is False
