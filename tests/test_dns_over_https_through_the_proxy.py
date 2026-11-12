"""The DoH PROVIDER is a declaration of the core: exactly two ``network.trr.*`` keys, for every session.

Behind a proxy every name must be resolved by Firefox's own DNS-over-HTTPS, through the proxy, and nothing local. The STATE
(``network.trr.mode``, ``exclude-etc-hosts``, ``excluded-domains``) is derived by the ENGINE (Juggler) from the proxies it is
given: the browser proxy and every context proxy, which a pref written once at launch could never cover. The PROVIDER is the
core's: ``network.trr.uri`` and ``network.trr.bootstrapAddr``, declared once for every session (with or without a proxy: the
engine turns TRR on only when a proxy is present, so without one mode stays the default 0). They are USER values on purpose,
because Firefox's DoH heuristics (``DoHController``) stand down when ``network.trr.mode`` or ``network.trr.uri`` has a user
value. ``extra_prefs`` overrides them like any other pref.

What the core still owns besides: ``network.proxy.allow_bypass`` (False with a proxy, absent without) and
``network.parental_controls_cached_state`` (False). ``socks_remote_dns`` and ``socks5_remote_dns`` are not declared: a Juggler
session writes no proxy endpoint to the prefs.
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

#: the provider, declared by the core; the address is one of the two `mozilla.cloudflare-dns.com` answered with on 2026-11-11
_PROVIDER = {"network.trr.uri": "https://mozilla.cloudflare-dns.com/dns-query",
             "network.trr.bootstrapAddr": "162.159.61.4"}
#: the state, written by the engine from the proxies it is given: the core never writes these
_ENGINE_OWNED = ("network.trr.mode", "network.trr.exclude-etc-hosts", "network.trr.excluded-domains")


def _trr(prefs):
    return {k: v for k, v in prefs.items() if k.startswith("network.trr.")}


@pytest.mark.parametrize("server", _PROXIES)
def test_configure_proxy_writes_no_trr_key(server):
    """The provider is not the proxy layer's: configure_proxy writes no ``network.trr.`` key at all."""
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
def test_the_composed_prefs_of_a_proxied_session_declare_exactly_the_provider(server):
    proxied = compose_session_prefs(generate_profile(seed=7), timezone="America/New_York",
                                    proxy={"server": server}).prefs
    assert _trr(proxied) == _PROVIDER, _trr(proxied)
    assert proxied["network.proxy.allow_bypass"] is False
    assert proxied["network.parental_controls_cached_state"] is False


@pytest.mark.parametrize("proxy", _NO_PROXY)
def test_the_composed_prefs_of_a_direct_session_declare_the_same_provider(proxy):
    """One declaration for every session: harmless without a proxy, where the engine leaves TRR at mode 0."""
    direct = compose_session_prefs(generate_profile(seed=7), timezone="America/New_York", proxy=proxy).prefs
    assert _trr(direct) == _PROVIDER, _trr(direct)
    assert "network.proxy.allow_bypass" not in direct


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 12345])
def test_the_provider_is_the_same_for_every_seed(seed):
    """Every real Firefox ships the same provider: a value equal across seeds is equal across users too."""
    plain = compose_session_prefs(generate_profile(seed=seed), timezone="America/New_York").prefs
    proxied = compose_session_prefs(generate_profile(seed=seed), timezone="America/New_York",
                                    proxy={"server": "socks5://203.0.113.9:1080"}).prefs
    assert _trr(plain) == _PROVIDER == _trr(proxied)


@pytest.mark.parametrize("server", [None, *_PROXIES])
def test_the_engine_owned_keys_never_appear(server):
    """mode, exclude-etc-hosts and excluded-domains are the engine's, derived from the proxies: never written by the core."""
    proxy = {"server": server} if server else None
    composed = compose_session_prefs(generate_profile(seed=7), timezone="America/New_York", proxy=proxy).prefs
    base = get_default_stealth_prefs(7, proxy=proxy, timezone="America/New_York", locale="en-US")
    for prefs in (composed, base):
        for name in _ENGINE_OWNED:
            assert name not in prefs, name


def test_the_translated_profile_declares_exactly_the_provider():
    prefs = translate_profile_to_prefs(generate_profile(seed=7), timezone="America/New_York")
    assert _trr(prefs) == _PROVIDER, _trr(prefs)


@pytest.mark.parametrize("server", [None, *_PROXIES])
def test_the_default_prefs_declare_exactly_the_provider(server):
    proxy = {"server": server} if server else None
    base = get_default_stealth_prefs(7, proxy=proxy, timezone="America/New_York", locale="en-US")
    assert _trr(base) == _PROVIDER, _trr(base)


def test_the_provider_is_declared_in_one_place_only():
    """One declaration: each of the two names is spelled by exactly one string constant of the package."""
    import ast

    spelled = {}
    for p in _SRC.rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_bytes())):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("network.trr."):
                spelled.setdefault(n.value, []).append(p.name)
    assert spelled == {"network.trr.uri": ["prefs.py"], "network.trr.bootstrapAddr": ["prefs.py"]}, spelled


def test_an_extra_prefs_override_wins_for_each_provider_key():
    """An e2e declares a local DoH: the caller's value replaces the core's, the other key stays declared."""
    local = {"network.trr.uri": "https://127.0.0.1:8443/dns-query", "network.trr.bootstrapAddr": "127.0.0.1"}
    for server in (None, "socks5://203.0.113.9:1080"):
        proxy = {"server": server} if server else None
        both = compose_session_prefs(generate_profile(seed=7), timezone="America/New_York", proxy=proxy,
                                     extra_prefs=dict(local)).prefs
        assert _trr(both) == local, _trr(both)
        one = compose_session_prefs(generate_profile(seed=7), timezone="America/New_York", proxy=proxy,
                                    extra_prefs={"network.trr.uri": local["network.trr.uri"]}).prefs
        assert _trr(one) == {"network.trr.uri": local["network.trr.uri"],
                             "network.trr.bootstrapAddr": _PROVIDER["network.trr.bootstrapAddr"]}


def test_a_none_extra_prefs_value_removes_the_declaration():
    """The overlay's own sentinel (None deletes) works on these keys like on any other."""
    prefs = compose_session_prefs(generate_profile(seed=7), timezone="America/New_York",
                                  extra_prefs={"network.trr.bootstrapAddr": None}).prefs
    assert _trr(prefs) == {"network.trr.uri": _PROVIDER["network.trr.uri"]}


def test_a_launch_plan_writes_the_provider_and_no_engine_owned_line(tmp_path):
    """The direct launch has no proxy (it refuses one); its user.js carries the two provider lines and nothing else."""
    from invisible_core.launch import build_launch_plan

    plan = build_launch_plan(7, profile_dir=tmp_path / "p", binary_path="/fake/firefox",
                             timezone="America/New_York", locale="en-US")
    text = (plan.profile_dir / "user.js").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if "network.trr." in ln]
    assert sorted(ln.split('"')[1] for ln in lines) == sorted(_PROVIDER), lines
    for name in _ENGINE_OWNED:
        assert name not in text
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


def test_no_source_file_spells_the_gate_switch_the_remote_dns_prefs_or_an_engine_owned_trr_pref():
    """No string constant of the package IS one of these pref names (comments and prose may still mention them)."""
    import ast

    names = {"zoom.stealth.dns.no_local_resolution", "network.proxy.socks_remote_dns",
             "network.proxy.socks5_remote_dns", *_ENGINE_OWNED}
    spelled = [(p.name, n.value) for p in _SRC.rglob("*.py")
               for n in ast.walk(ast.parse(p.read_bytes()))
               if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in names]
    assert not spelled, spelled


def test_the_cached_parental_controls_state_is_declared_off_once():
    """The engine answers 'parental controls off' from its own service, but upstream reads the cached pref once before
    that answer is written back: a profile that cached ``true`` would send names to the system resolver until then."""
    base = get_default_stealth_prefs(7, timezone="America/New_York", locale="en-US")
    assert base["network.parental_controls_cached_state"] is False
