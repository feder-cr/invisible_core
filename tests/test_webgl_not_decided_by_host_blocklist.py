"""Whether WebGL2 exists is never the host adapter's decision: webgl.force-enabled is
true on every host, so Mozilla's Windows blocklist (any vendor but Intel/NVIDIA/AMD,
e.g. a Microsoft Basic or Hyper-V adapter) cannot null webgl2 for a declared GPU that
has it on retail. Before 54.50 the pref was a Linux-only Xvfb workaround, and a
Windows runner answered webgl2 = null.
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


def test_declared_once_not_in_a_platform_table():
    # one declaration, for every host: a platform table would make it host dependent again
    assert "webgl.force-enabled" not in _LINUX_XVFB_WORKAROUNDS
    assert "webgl.force-enabled" not in _WIN_VIRT_DESKTOP_WORKAROUNDS


def test_caller_can_still_override(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    prefs = translate_profile_to_prefs(generate_profile(seed=12), timezone="America/New_York",
                                       extra_prefs={"webgl.force-enabled": False})
    assert prefs["webgl.force-enabled"] is False
