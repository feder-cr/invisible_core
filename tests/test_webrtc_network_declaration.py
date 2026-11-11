"""The host network a session declares to the engine, once, from the seed, proxied or not.

Behind a proxy the engine must not read the interfaces of the machine it runs on: a page counts the host candidates and
reads their ranks, so the count of adapters and the presence of IPv6 would describe the host, not the persona. The core
declares the network in one environment variable, ``STEALTHFOX_WEBRTC_NETWORK``, in the form ``v4=<address>;v6=<0|1>;p6=<prefix or empty>``
(design: ``invisible_WebRTC/notes/declared-interfaces-design.md``):

* ``v4``: one private address, drawn from the seed with the frequencies of a measured population of home LANs;
* ``v6``: ``1`` only when the proxy's exit has IPv6 AND the relay is proven to carry an IPv6 destination. Nothing proves the
  second today, so it is ``0``.

The same seed gives the same declaration on every machine and with or without a proxy: the engine no longer reads the host's
adapters, so a session without a proxy declares the same machine (IPv6 stays 0, a declared residual difference).
"""
from __future__ import annotations

import ipaddress
import re
from collections import Counter

import pytest

from invisible_core import environment
from invisible_core._fpforge import generate_profile
from invisible_core._webrtc_network import (DATASET, DeclaredNetwork, declared_network, network_to_declare)
from invisible_core.launch import build_launch_env

pytestmark = pytest.mark.unit

_FORM = re.compile(r"^v4=(\d+\.\d+\.\d+\.\d+);v6=([01]);p6=(.*)$")


def test_the_declaration_has_the_form_the_engine_reads():
    text = declared_network(42).env_value()
    m = _FORM.match(text)
    assert m, text
    assert ipaddress.IPv4Address(m.group(1)).is_private
    assert m.group(2) == "0"
    assert m.group(3) == "", "no IPv6, no prefix"


def test_the_same_seed_gives_the_same_declaration_everywhere():
    """Pinned values: same seed, same bytes on every machine (the draw is a pure function of the seed)."""
    assert [declared_network(s).env_value() for s in (0, 1, 42, 2**31 - 1, 123456789)] == [
        "v4=172.21.22.218;v6=0;p6=",
        "v4=192.168.2.22;v6=0;p6=",
        "v4=192.168.1.101;v6=0;p6=",
        "v4=192.168.2.138;v6=0;p6=",
        "v4=192.168.0.110;v6=0;p6=",
    ]


def test_different_seeds_give_different_addresses():
    addresses = {declared_network(s).v4 for s in range(500)}
    assert len(addresses) > 300, len(addresses)


def test_a_host_address_is_never_the_network_or_the_gateway_or_the_broadcast():
    for seed in range(2000):
        last = int(declared_network(seed).v4.split(".")[3])
        assert 2 <= last <= 254, (seed, last)


def test_ipv6_needs_the_exit_and_the_relay_both():
    """Nothing in the core proves a relay for IPv6, so the answer is 0 until both are said."""
    assert declared_network(42).v6 is False
    assert declared_network(42, exit_ipv6=True).v6 is False
    assert declared_network(42, relay_ipv6=True).v6 is False
    on = declared_network(42, exit_ipv6=True, relay_ipv6=True)
    assert on.v6 is True and on.env_value().endswith(";v6=1;p6=")
    # With IPv6 the declaration also names the /64 of the exit: the home host address sits in the same /64 as the srflx
    # (no NAT66). The caller says it; nothing is measured here. Ignored while v6 is 0.
    prefix = "2a01:e0a:1f2:3450::/64"
    with_prefix = declared_network(42, exit_ipv6=True, relay_ipv6=True, exit_prefix6=prefix)
    assert with_prefix.env_value().endswith(";v6=1;p6=" + prefix)
    assert declared_network(42, exit_prefix6=prefix).env_value().endswith(";v6=0;p6=")
    assert on.v4 == declared_network(42).v4, "IPv6 must not move the address"


# Copied from the engine, series worktree C:\ff\wt\doh-engine at commit e403bd08bb:
#   dom/media/webrtc/transport/test/gtest/TestStealthDeclaredNetwork.cpp  (tests IPv6IsTheExitPrefixAndATemporaryIdentifierOnTheSameAdapter
#   and APrefixMustBeGlobalUnicast) and the parser it exercises, dom/media/webrtc/transport/nr_stealth_network.cpp (PrefixOf:
#   "<address>/64" or "<address>", global unicast 2000::/3, not Teredo, 6to4 nor documentation).
#   (p6 string, does the engine accept it)
_ENGINE_P6 = [
    ("2a01:e0a:1f2:3450::/64", True),      # gtest: the form the engine tests use
    ("2a01:e0a:1f2:3450::", True),         # PrefixOf: the "/64" is optional
    ("fe80::/64", False),                  # gtest: not global unicast
    ("2001:db8:1::/64", False),            # gtest: documentation
    ("2001:0:53aa::/64", False),           # gtest: Teredo
    ("2002:c000:204::/64", False),         # gtest: 6to4
    ("fd12:3456::/64", False),             # gtest: unique local
    ("2a01:e0a::/48", False),              # gtest: only a /64 is a prefix
    ("10.0.0.1", False),                   # gtest: not IPv6
    ("2001:db8:1234:5678", False),         # the bare four-group form the core used to declare: inet_pton rejects it
]


def _engine_accepts(p6: str) -> bool:
    """A model of ``PrefixOf``, held to the table above by :func:`test_the_model_of_the_engine_parser_agrees_with_its_gtest`."""
    address, _, length = p6.partition("/")
    if length not in ("", "64"):
        return False
    try:
        a = ipaddress.IPv6Address(address)
    except ValueError:
        return False
    b = a.packed
    if (b[0] & 0xE0) != 0x20:
        return False
    return not (b[:4] == b"\x20\x01\x00\x00" or b[:2] == b"\x20\x02" or b[:4] == b"\x20\x01\x0d\xb8")


def test_the_model_of_the_engine_parser_agrees_with_its_gtest():
    for p6, accepted in _ENGINE_P6:
        assert _engine_accepts(p6) is accepted, p6


@pytest.mark.parametrize("p6,accepted", _ENGINE_P6)
def test_the_prefix_the_core_declares_is_one_the_engine_parser_accepts(p6, accepted):
    """The engine rejects a malformed or non-global p6 and, with it, the whole declaration (the IPv4 host address too). So the
    core must refuse what the engine refuses, at the source, and what it emits must be what the engine accepts."""
    if not accepted:
        with pytest.raises(ValueError):
            declared_network(42, exit_ipv6=True, relay_ipv6=True, exit_prefix6=p6)
        return
    text = declared_network(42, exit_ipv6=True, relay_ipv6=True, exit_prefix6=p6).env_value()
    emitted = text.rsplit(";p6=", 1)[1]
    assert _engine_accepts(emitted), text
    assert emitted == "2a01:e0a:1f2:3450::/64", "one canonical form: the /64 network, written with its length"


def test_a_host_address_in_the_prefix_is_reduced_to_its_64():
    got = declared_network(42, exit_ipv6=True, relay_ipv6=True, exit_prefix6="2A01:0E0A:1F2:3450:1:2:3:4/64")
    assert got.prefix6 == "2a01:e0a:1f2:3450::/64"


def test_a_prefix_is_validated_even_when_a_declaration_is_built_by_hand():
    with pytest.raises(ValueError):
        DeclaredNetwork(v4="192.168.1.9", v6=True, prefix6="2001:db8:1234:5678")
    assert DeclaredNetwork(v4="192.168.1.9", v6=True, prefix6="").env_value() == "v4=192.168.1.9;v6=1;p6="


def test_the_frequencies_are_those_of_the_measured_population():
    """30000 seeds drawn against the dataset (``DATASET``: 1.79 million WD Cloud devices, blog.benjojo.co.uk, 2025-06-05).
    The draw is deterministic, so this does not flake; the tolerance is wide enough for a population of this size."""
    n = 30000
    blocks, slash24 = Counter(), Counter()
    for seed in range(n):
        v4 = ipaddress.IPv4Address(declared_network(seed).v4)
        for name, net in (("192.168.0.0/16", "192.168.0.0/16"), ("10.0.0.0/8", "10.0.0.0/8"), ("172.16.0.0/12", "172.16.0.0/12")):
            if v4 in ipaddress.IPv4Network(net):
                blocks[name] += 1
        slash24[".".join(str(v4).split(".")[:3])] += 1
    total = sum(DATASET["blocks"].values())
    for name, count in DATASET["blocks"].items():
        assert abs(blocks[name] / n - count / total) < 0.012, (name, blocks[name] / n, count / total)
    for third, count in DATASET["slash24_in_192_168"].items():
        share = count / total
        assert abs(slash24["192.168.%d" % third] / n - share) < 0.012, (third, slash24["192.168.%d" % third] / n, share)


def test_the_most_common_home_lan_is_the_commonest_draw():
    counts = Counter(".".join(declared_network(s).v4.split(".")[:3]) for s in range(5000))
    assert counts.most_common(1)[0][0] == "192.168.1"


def test_every_session_declares_the_network_with_or_without_a_proxy():
    """No ``proxied`` argument exists: the declaration is a function of the seed alone."""
    import inspect

    assert "proxied" not in inspect.signature(network_to_declare).parameters
    assert network_to_declare(42) == declared_network(42).env_value()


def test_the_core_own_direct_launch_declares_the_network(monkeypatch, tmp_path):
    """``build_launch_plan`` is a direct session (it refuses a proxy). It must carry the declaration too."""
    from invisible_core import _geo, build_launch_plan
    from invisible_core._geo import SessionGeo

    monkeypatch.setattr(_geo, "prepare_session_geo",
                        lambda tz, proxy, locale, **_kw: SessionGeo("America/New_York", "203.0.113.7"))
    monkeypatch.delenv("STEALTHFOX_WEBRTC_NETWORK", raising=False)
    plan = build_launch_plan(42, profile_dir=tmp_path, binary_path="firefox", timezone="America/New_York")
    assert plan.env["STEALTHFOX_WEBRTC_NETWORK"] == network_to_declare(42)


def test_the_declaration_travels_in_the_environment_variable_the_registry_names():
    assert environment.WEBRTC_NETWORK == "STEALTHFOX_WEBRTC_NETWORK"
    assert environment.WEBRTC_NETWORK in environment.SETTINGS and environment.WEBRTC_NETWORK in environment.READ
    env = build_launch_env({}, network_declared="v4=192.168.1.9;v6=0;p6=", base_env={})
    assert env["STEALTHFOX_WEBRTC_NETWORK"] == "v4=192.168.1.9;v6=0;p6="


def test_a_value_the_caller_already_set_wins():
    env = build_launch_env({}, network_declared="v4=192.168.1.9;v6=0;p6=",
                           base_env={"STEALTHFOX_WEBRTC_NETWORK": "v4=10.0.0.5;v6=1;p6="})
    assert env["STEALTHFOX_WEBRTC_NETWORK"] == "v4=10.0.0.5;v6=1;p6="


def test_an_environment_built_by_hand_without_a_declaration_has_no_variable():
    """Only a caller that composes an environment itself can leave it out; the two session builders never do."""
    assert "STEALTHFOX_WEBRTC_NETWORK" not in build_launch_env({}, base_env={})


def test_the_address_is_declared_once_and_no_pref_repeats_it():
    """One declaration (``declared preferences once``): the engine reads the network from STEALTHFOX_WEBRTC_NETWORK only.
    The pref ``zoom.stealth.webrtc.host_ip`` (a second copy of the address that the engine only tested for emptiness)
    is not written any more, and no source file spells it."""
    import pathlib

    from invisible_core.prefs import translate_profile_to_prefs

    for seed in (0, 42, 987654321):
        prefs = translate_profile_to_prefs(generate_profile(seed=seed), timezone="America/New_York")
        assert not [k for k in prefs if "host_ip" in k], [k for k in prefs if "host_ip" in k]
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "invisible_core"
    spelled = [p.name for p in src.rglob("*.py") if "host_ip" in p.read_text(encoding="utf-8")]
    assert not spelled, spelled


def test_ipv6_is_one_fact_and_the_core_writes_no_pref_for_it(monkeypatch, tmp_path):
    """IPv6 is ONE fact for the whole browser: ``v6=0`` means no IPv6 for DNS, HTTP and ICE alike. The ENGINE derives
    ``network.dns.disableIPv6`` from this same declaration, so the declaration is the single source and the core writes NO
    pref (nor any launch-plan key) that mentions ``disableIPv6``. Pinned for the proxied and the direct session, in the
    prefs the core composes, in the user.js the plan writes, in its argv and environment, and in every pref key the
    source spells."""
    import pathlib

    from invisible_core import _geo, build_launch_plan, configure_proxy
    from invisible_core._geo import SessionGeo
    from invisible_core.prefs import compose_session_prefs, translate_profile_to_prefs

    profile = generate_profile(seed=42)
    keys = set(translate_profile_to_prefs(profile, timezone="America/New_York"))
    for proxy in (None, {"server": "socks5://203.0.113.9:1080"}, {"server": "http://203.0.113.9:8080"}):
        keys |= set(compose_session_prefs(profile, timezone="America/New_York", proxy=proxy, survive_hard_kill=True).prefs)
        extra = {}
        configure_proxy(proxy, extra)
        keys |= set(extra)
    monkeypatch.setattr(_geo, "prepare_session_geo", lambda tz, proxy, locale, **_kw: SessionGeo("America/New_York", "203.0.113.7"))
    plan = build_launch_plan(42, profile_dir=tmp_path, binary_path="firefox", timezone="America/New_York")
    assert [k for k in keys if "disableipv6" in k.lower()] == []
    assert not [k for k, v in plan.env.items() if "disableipv6" in (k + v).lower()], plan.env
    assert not [a for a in plan.argv[:-1] if "disableipv6" in a.lower()], plan.argv
    assert "disableIPv6" not in (tmp_path / "user.js").read_text(encoding="utf-8")
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "invisible_core"
    spelled = [p.name for p in src.rglob("*.py") if re.search(r"""["'][^"'\n]*disableIPv6[^"'\n]*["']""", p.read_text(encoding="utf-8"))]
    assert not spelled, spelled


def test_a_dataclass_is_returned():
    d = declared_network(1)
    assert isinstance(d, DeclaredNetwork) and isinstance(d.v6, bool)
