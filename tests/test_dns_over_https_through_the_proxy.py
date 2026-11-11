"""The core declares NOTHING about names: no ``network.trr.*`` key is ever produced, with or without a proxy.

Behind a proxy every name must be resolved by Firefox's own DNS-over-HTTPS, through the proxy, and nothing local. That state
(``network.trr.mode``, ``uri``, ``bootstrapAddr``, ``exclude-etc-hosts``, ``excluded-domains``) is derived by the ENGINE
(Juggler) from the proxies it is given: the browser proxy and every context proxy. A proxy given per context is covered
too, which a pref written once at launch could never do. So the core writes no key starting with ``network.trr.``.

What the core still owns: ``network.proxy.allow_bypass`` (False with a proxy, absent without) and
``network.parental_controls_cached_state`` (False). ``socks_remote_dns`` and ``socks5_remote_dns`` are not declared either:
a Juggler session writes no proxy endpoint to the prefs.
"""
from __future__ import annotations

import pathlib

import pytest

from invisible_core import configure_proxy, get_default_stealth_prefs
from invisible_core._fpforge import generate_profile
from invisible_core.prefs import compose_session_prefs, translate_profile_to_prefs

pytestmark = pytest.mark.unit

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "invisible_core"

#: an address and a name, for every scheme the engine takes
_PROXIES = ("socks5://203.0.113.9:1080", "socks4://203.0.113.9:1080", "http://203.0.113.9:8080",
            "https://203.0.113.9:8443", "socks5://[2001:db8::9]:1080",
            "socks5://gw.example.test:1080", "socks4://gw.example.test:1080", "http://proxy.example.test:8080",
            "https://secure.example.test:8443")
_NO_PROXY = (None, {}, {"server": ""}, {"server": "direct://"})


def _trr(prefs):
    return sorted(k for k in prefs if k.startswith("network.trr."))


@pytest.mark.parametrize("server", _PROXIES)
def test_configure_proxy_writes_no_trr_key(server):
    prefs = {}
    returned = configure_proxy({"server": server}, prefs)
    assert returned is not None
    assert not _trr(prefs), _trr(prefs)


@pytest.mark.parametrize("server", _PROXIES)
def test_configure_proxy_writes_no_trr_key_with_credentials(server):
    prefs = {}
    configure_proxy({"server": server, "username": "u", "password": "p", "bypass": "localhost"}, prefs)
    assert not _trr(prefs), _trr(prefs)


@pytest.mark.parametrize("proxy", _NO_PROXY)
def test_configure_proxy_without_a_proxy_writes_no_trr_key(proxy):
    prefs = {}
    assert configure_proxy(proxy, prefs) is None
    assert not _trr(prefs), (proxy, prefs)


@pytest.mark.parametrize("server", _PROXIES)
def test_the_bypass_fallback_stays_closed_with_a_proxy(server):
    prefs = {}
    configure_proxy({"server": server}, prefs)
    assert prefs["network.proxy.allow_bypass"] is False


@pytest.mark.parametrize("proxy", _NO_PROXY)
def test_the_bypass_pref_is_absent_without_a_proxy(proxy):
    prefs = {}
    configure_proxy(proxy, prefs)
    assert "network.proxy.allow_bypass" not in prefs


@pytest.mark.parametrize("server", _PROXIES)
def test_the_composed_prefs_of_a_proxied_session_carry_no_trr_key(server):
    proxied = compose_session_prefs(generate_profile(seed=7), timezone="America/New_York",
                                    proxy={"server": server}).prefs
    assert not _trr(proxied), _trr(proxied)
    assert proxied["network.proxy.allow_bypass"] is False
    assert proxied["network.parental_controls_cached_state"] is False


def test_the_composed_prefs_of_a_direct_session_carry_no_trr_key():
    direct = compose_session_prefs(generate_profile(seed=7), timezone="America/New_York").prefs
    assert not _trr(direct), _trr(direct)
    assert "network.proxy.allow_bypass" not in direct


def test_the_translated_profile_carries_no_trr_key():
    prefs = translate_profile_to_prefs(generate_profile(seed=7), timezone="America/New_York")
    assert not _trr(prefs), _trr(prefs)


@pytest.mark.parametrize("server", [None, *_PROXIES])
def test_the_default_prefs_carry_no_trr_key(server):
    proxy = {"server": server} if server else None
    base = get_default_stealth_prefs(7, proxy=proxy, timezone="America/New_York", locale="en-US")
    assert not _trr(base), _trr(base)


def test_a_launch_plan_writes_no_trr_key(tmp_path):
    """The direct launch has no proxy (it refuses one), and its user.js carries no ``network.trr.`` line either."""
    from invisible_core.launch import build_launch_plan

    plan = build_launch_plan(7, profile_dir=tmp_path / "p", binary_path="/fake/firefox",
                             timezone="America/New_York", locale="en-US")
    text = (plan.profile_dir / "user.js").read_text(encoding="utf-8")
    assert "network.trr." not in text
    assert "network.parental_controls_cached_state" in text


@pytest.mark.parametrize("server", _PROXIES)
def test_a_launch_plan_given_a_proxy_is_refused_and_writes_nothing(tmp_path, server):
    from invisible_core.launch import build_launch_plan

    with pytest.raises(ValueError):
        build_launch_plan(7, profile_dir=tmp_path / "p", binary_path="/fake/firefox",
                          proxy={"server": server}, timezone="America/New_York", locale="en-US")
    assert not (tmp_path / "p" / "user.js").exists()


def test_the_old_dns_gate_switch_is_gone():
    prefs = {}
    configure_proxy({"server": "socks5://h:1080"}, prefs)
    assert "zoom.stealth.dns.no_local_resolution" not in prefs


def test_the_baseline_declares_no_socks_remote_dns():
    base = get_default_stealth_prefs(7, timezone="America/New_York", locale="en-US")
    assert "network.proxy.socks_remote_dns" not in base
    assert "network.proxy.socks5_remote_dns" not in base


def test_no_source_file_spells_a_trr_pref_or_the_gate_switch_or_the_remote_dns_prefs():
    """No string constant of the package IS one of these pref names (comments and prose may still mention them)."""
    import ast

    names = {"zoom.stealth.dns.no_local_resolution", "network.proxy.socks_remote_dns",
             "network.proxy.socks5_remote_dns"}
    spelled = [(p.name, n.value) for p in _SRC.rglob("*.py")
               for n in ast.walk(ast.parse(p.read_bytes()))
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and (n.value in names or n.value.startswith("network.trr."))]
    assert not spelled, spelled


def test_the_cached_parental_controls_state_is_declared_off_once():
    """The engine answers 'parental controls off' from its own service, but upstream reads the cached pref once before
    that answer is written back: a profile that cached ``true`` would send names to the system resolver until then."""
    base = get_default_stealth_prefs(7, timezone="America/New_York", locale="en-US")
    assert base["network.parental_controls_cached_state"] is False
