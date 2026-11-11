"""The host network a session declares to the engine, once, from the seed, proxied or not.

Behind a proxy the engine must not read the interfaces of the machine it runs on: a page counts the host candidates and
reads their ranks, so the count of adapters and the presence of IPv6 would describe the host, not the persona. The core
declares the network in one environment variable, ``STEALTHFOX_WEBRTC_NETWORK``, in the form ``v4=<address>;v6=<0|1>``
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

_FORM = re.compile(r"^v4=(\d+\.\d+\.\d+\.\d+);v6=([01])$")


def test_the_declaration_has_the_form_the_engine_reads():
    text = declared_network(42).env_value()
    m = _FORM.match(text)
    assert m, text
    assert ipaddress.IPv4Address(m.group(1)).is_private
    assert m.group(2) == "0"


def test_the_same_seed_gives_the_same_declaration_everywhere():
    """Pinned values: same seed, same bytes on every machine (the draw is a pure function of the seed)."""
    assert [declared_network(s).env_value() for s in (0, 1, 42, 2**31 - 1, 123456789)] == [
        "v4=172.21.22.218;v6=0",
        "v4=192.168.2.22;v6=0",
        "v4=192.168.1.101;v6=0",
        "v4=192.168.2.138;v6=0",
        "v4=192.168.0.110;v6=0",
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
    assert on.v6 is True and on.env_value().endswith(";v6=1")
    assert on.v4 == declared_network(42).v4, "IPv6 must not move the address"


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
                        lambda tz, proxy, locale: SessionGeo("America/New_York", "203.0.113.7"))
    monkeypatch.delenv("STEALTHFOX_WEBRTC_NETWORK", raising=False)
    plan = build_launch_plan(42, profile_dir=tmp_path, binary_path="firefox", timezone="America/New_York")
    assert plan.env["STEALTHFOX_WEBRTC_NETWORK"] == network_to_declare(42)


def test_the_declaration_travels_in_the_environment_variable_the_registry_names():
    assert environment.WEBRTC_NETWORK == "STEALTHFOX_WEBRTC_NETWORK"
    assert environment.WEBRTC_NETWORK in environment.SETTINGS and environment.WEBRTC_NETWORK in environment.READ
    env = build_launch_env({}, network_declared="v4=192.168.1.9;v6=0", base_env={})
    assert env["STEALTHFOX_WEBRTC_NETWORK"] == "v4=192.168.1.9;v6=0"


def test_a_value_the_caller_already_set_wins():
    env = build_launch_env({}, network_declared="v4=192.168.1.9;v6=0",
                           base_env={"STEALTHFOX_WEBRTC_NETWORK": "v4=10.0.0.5;v6=1"})
    assert env["STEALTHFOX_WEBRTC_NETWORK"] == "v4=10.0.0.5;v6=1"


def test_an_environment_built_by_hand_without_a_declaration_has_no_variable():
    """Only a caller that composes an environment itself can leave it out; the two session builders never do."""
    assert "STEALTHFOX_WEBRTC_NETWORK" not in build_launch_env({}, base_env={})


def test_the_old_host_ip_pref_is_the_same_declaration_not_a_second_one():
    """One declaration of the address (``declared preferences once``): the pref the engine still tests for emptiness
    carries the declared address, so the two can never disagree."""
    from invisible_core.prefs import translate_profile_to_prefs

    for seed in (0, 42, 987654321):
        prefs = translate_profile_to_prefs(generate_profile(seed=seed), timezone="America/New_York")
        assert prefs["zoom.stealth.webrtc.host_ip"] == declared_network(seed).v4


def test_a_dataclass_is_returned():
    d = declared_network(1)
    assert isinstance(d, DeclaredNetwork) and isinstance(d.v6, bool)
