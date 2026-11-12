"""Whether WebGL2 exists, and whether it antialiases, is never the host adapter's
decision. Mozilla's Windows blocklist refuses any vendor but Intel/NVIDIA/AMD (a
Microsoft Basic or Hyper-V adapter, the CI runner). Until 2026-11-01 the core held it
off with two prefs, webgl.force-enabled (webgl2 not null) and gfx.blacklist.webgl.angle
= OK (accelerated ANGLE, antialias with SAMPLES 4). The engine now answers every gfx
feature from zoom.stealth.gfx.feature_status before any blocklist or gfx.blacklist.*
pref, so the two left the core and the declaration carries WEBGL2 and WEBGL_ANGLE = 1
(tests/test_gfx_device_is_declared.py holds every outcome).
Measured on the engine with MOZ_GFX_SPOOF_VENDOR_ID=0x00ba / 0x000b
(coord/host_gate/webgl_blocklist_probe.py, run by candidate_run.sh).
"""
from __future__ import annotations

import sys

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import (
    _WIN_VIRT_DESKTOP_WORKAROUNDS,
    translate_profile_to_prefs,
)

pytestmark = pytest.mark.unit


def _status(prefs):
    return dict(e.split("|")[:2] for e in prefs["zoom.stealth.gfx.feature_status"].split(","))


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("virtual_display", [False, True])
@pytest.mark.parametrize("seed", [1, 7, 12, 29])
def test_webgl_is_declared_ok_on_every_host(monkeypatch, platform, virtual_display, seed):
    monkeypatch.setattr(sys, "platform", platform)
    prefs = translate_profile_to_prefs(generate_profile(seed=seed), timezone="America/New_York",
                                       virtual_display=virtual_display)
    st = _status(prefs)
    for feature in ("WEBGL", "WEBGL2", "WEBGL_ANGLE", "WEBGL_USE_HARDWARE"):
        assert st[feature] == "1", feature  # nsIGfxInfo::FEATURE_STATUS_OK
    # the engine ignores them now: emitting them would be lines that move nothing
    assert "webgl.force-enabled" not in prefs
    assert "gfx.blacklist.webgl.angle" not in prefs


def test_no_platform_table_brings_them_back():
    for name in ("webgl.force-enabled", "gfx.blacklist.webgl.angle"):
        assert name not in _WIN_VIRT_DESKTOP_WORKAROUNDS
