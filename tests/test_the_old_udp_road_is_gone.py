"""The core no longer proves UDP, declares an srflx or writes switches for them.

The engine decides UDP itself (a SOCKS5 UDP layer and a real srflx). The core declares the network once
(``STEALTHFOX_WEBRTC_NETWORK``) and nothing else: everything that served only the old road is removed, not switched off.
"""
from __future__ import annotations

import inspect
import pathlib

import pytest

from invisible_core import environment
from invisible_core._fpforge import generate_profile
from invisible_core._geo import SessionGeo
from invisible_core.launch import build_launch_env
from invisible_core.prefs import compose_session_prefs

pytestmark = pytest.mark.unit

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "invisible_core"
_GONE_PREFS = ("network.proxy.socks_remote_udp", "zoom.stealth.webrtc.no_direct_udp")
_GONE_ENV = ("STEALTHFOX_WEBRTC_PUBLIC_IP", "STEALTHFOX_WEBRTC_DISABLE_IPV6")
_GONE_WORDS = ("socks_remote_udp", "no_direct_udp", "WEBRTC_PUBLIC_IP", "WEBRTC_DISABLE_IPV6",
               "UDP_GOES_THROUGH_SOCKS", "srflx_suppressed")


def test_a_proxied_session_writes_no_udp_switch():
    prefs = compose_session_prefs(generate_profile(seed=7), timezone="America/New_York",
                                  proxy={"server": "socks5://gw:1080"}).prefs
    assert prefs.get("network.proxy.allow_bypass") is False  # the proxy layer did run
    assert prefs.get("zoom.stealth.dns.no_local_resolution") is True
    for name in _GONE_PREFS:
        assert name not in prefs, name


def test_the_launch_environment_carries_only_the_network_declaration():
    env = build_launch_env({}, network_declared="v4=192.168.1.5;v6=0", base_env={})
    assert env[environment.WEBRTC_NETWORK] == "v4=192.168.1.5;v6=0"
    for name in _GONE_ENV:
        assert name not in env, name
    assert "srflx_declared" not in inspect.signature(build_launch_env).parameters
    assert not [k for k in env if k.startswith("STEALTHFOX_WEBRTC") and k != environment.WEBRTC_NETWORK]


def test_session_geo_declares_no_srflx():
    geo = SessionGeo("America/New_York", "203.0.113.7")
    assert not hasattr(geo, "srflx_to_declare")
    assert not hasattr(geo, "srflx_suppressed")
    assert geo.egress_ip == "203.0.113.7"  # still the fact timezone and locale come from


def test_the_registry_has_no_name_for_the_old_road():
    assert not hasattr(environment, "WEBRTC_PUBLIC_IP")
    assert not hasattr(environment, "WEBRTC_DISABLE_IPV6")
    assert not set(_GONE_ENV) & set(environment.READ)


def test_there_is_no_udp_probe_module():
    assert not (_SRC / "_capability.py").exists()


def test_no_source_file_spells_the_old_road():
    found = [(p.relative_to(_SRC).as_posix(), word)
             for p in sorted(_SRC.rglob("*.py"))
             for word in _GONE_WORDS if word in p.read_text(encoding="utf-8")]
    assert not found, found
