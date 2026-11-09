"""The CPU vendor a persona declares (zoom.stealth.cpu.vendor).

The engine picks the reciprocal / reciprocal square root tables of Skia's highp blending by it
(RCPPS and RSQRTPS give other bits on Intel and on AMD; a page reads them through the blends
colour-dodge, colour-burn, hue, saturation, colour, luminosity and the bicubic image filter), so
the vendor must be declared, never the host's. An Intel GPU is an Intel CPU's integrated graphics,
an AMD APU is an AMD CPU's, and a discrete card goes with either, by the Steam survey shares.

Known-bad input: the vendor drawn without looking at the device (an Intel iGPU with an AMD CPU is
a machine that does not exist), or not declared at all (the engine then reads the host's).

    python -m pytest tests/test_cpu_vendor_is_declared.py
"""
import collections

import pytest

from invisible_core import prefs as P
from invisible_core._fpforge import generate_profile
from invisible_core._webgl_personas import CPU_VENDOR_SHARES, choose_cpu_vendor

pytestmark = pytest.mark.unit

TZ = "America/New_York"
SEEDS = range(1200)


def _declared(seed, monkeypatch):
    monkeypatch.setattr(P.sys, "platform", "win32")
    profile = generate_profile(seed)
    return profile, P.translate_profile_to_prefs(profile, timezone=TZ)


def test_every_launch_declares_intel_or_amd(monkeypatch):
    for seed in (0, 42, 1561645783):
        _, prefs = _declared(seed, monkeypatch)
        assert prefs["zoom.stealth.cpu.vendor"] in ("intel", "amd")


def test_the_vendor_follows_the_integrated_graphics(monkeypatch):
    seen = collections.defaultdict(collections.Counter)
    for seed in SEEDS:
        profile, prefs = _declared(seed, monkeypatch)
        seen[profile.gpu.vendor][prefs["zoom.stealth.cpu.vendor"]] += 1
    assert set(seen["Google Inc. (Intel)"]) == {"intel"}, seen["Google Inc. (Intel)"]
    amd = seen["Google Inc. (AMD)"]
    assert amd["amd"] > 0 and amd["intel"] > 0, "the discrete AMD cards go with either vendor"


def test_a_discrete_card_draws_by_the_steam_shares(monkeypatch):
    drawn = collections.Counter()
    for seed in range(4000):
        drawn[choose_cpu_vendor({"vendor_id": "0x10de", "pool_family": "nvidia_turing"}, seed)] += 1
    total = sum(w for _, w in CPU_VENDOR_SHARES)
    for vendor, weight in CPU_VENDOR_SHARES:
        assert abs(drawn[vendor] / 4000 - weight / total) < 0.03, (vendor, drawn)


def test_an_amd_apu_is_an_amd_cpu_and_an_intel_device_an_intel_one():
    for seed in range(200):
        assert choose_cpu_vendor({"vendor_id": "0x1002", "pool_family": "amd_vega_igpu"}, seed) == "amd"
        assert choose_cpu_vendor({"vendor_id": "0x1002", "pool_family": "amd_igpu_graphics"}, seed) == "amd"
        assert choose_cpu_vendor({"vendor_id": "0x8086", "pool_family": "intel_gen9"}, seed) == "intel"


def test_the_vendor_is_the_seed_s_and_nothing_else(monkeypatch):
    first = _declared(7, monkeypatch)[1]["zoom.stealth.cpu.vendor"]
    assert all(_declared(7, monkeypatch)[1]["zoom.stealth.cpu.vendor"] == first for _ in range(3))

def test_every_device_of_the_pool_gets_the_vendor_its_graphics_imply():
    """The rule is read against the real devices of win_gfx_devices.json, so a device added to the
    pool with another name for its family cannot slip into the draw as a discrete card."""
    from invisible_core._webgl_personas import _gfx_devices
    apus = []
    for devices in _gfx_devices().values():
        for device in devices:
            vendor = choose_cpu_vendor(device, 123)
            if device["vendor_id"] == "0x8086":
                assert vendor == "intel", device["label"]
            elif "igpu" in (device.get("pool_family") or ""):
                assert vendor == "amd", device["label"]
                apus.append(device["label"])
    assert {"renoir_21925", "raven_21925", "raphael_32015"} <= set(apus), apus
