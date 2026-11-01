"""Whether WebGL2 exists, and whether it antialiases, is never the host adapter's
decision. Mozilla's Windows blocklist refuses any vendor but Intel/NVIDIA/AMD (e.g. a
Microsoft Basic or Hyper-V adapter, the CI runner):
  - webgl.force-enabled true on every host: webgl2 is not null (before 54.50 it was a
    Linux-only Xvfb workaround, and a Windows runner answered webgl2 = null);
  - gfx.blacklist.webgl.angle = FEATURE_STATUS_OK on every host: the accelerated ANGLE
    display is kept, so antialias is true with SAMPLES 4 as on the declared GPU
    (blocked, both contexts answered antialias false, SAMPLES 0).
Measured on the engine with MOZ_GFX_SPOOF_VENDOR_ID=0x00ba / 0x000b
(coord/host_gate/webgl_blocklist_probe.py, run by candidate_run.sh).
"""
from __future__ import annotations

import sys

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import (
    _LINUX_XVFB_WORKAROUNDS,
    _WIN_VIRT_DESKTOP_WORKAROUNDS,
    translate_profile_to_prefs,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("virtual_display", [False, True])
@pytest.mark.parametrize("seed", [1, 7, 12, 29])
def test_force_enabled_on_every_host(monkeypatch, platform, virtual_display, seed):
    monkeypatch.setattr(sys, "platform", platform)
    prefs = translate_profile_to_prefs(generate_profile(seed=seed), timezone="America/New_York",
                                       virtual_display=virtual_display)
    assert prefs["webgl.force-enabled"] is True
    assert prefs["gfx.blacklist.webgl.angle"] == 1  # nsIGfxInfo::FEATURE_STATUS_OK


def test_declared_once_not_in_a_platform_table():
    # one declaration each, for every host: a platform table would make it host dependent again
    for name in ("webgl.force-enabled", "gfx.blacklist.webgl.angle"):
        assert name not in _LINUX_XVFB_WORKAROUNDS
        assert name not in _WIN_VIRT_DESKTOP_WORKAROUNDS


def test_caller_can_still_override(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    prefs = translate_profile_to_prefs(generate_profile(seed=12), timezone="America/New_York",
                                       extra_prefs={"webgl.force-enabled": False})
    assert prefs["webgl.force-enabled"] is False
