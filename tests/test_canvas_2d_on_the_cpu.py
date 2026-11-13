"""Canvas 2D is drawn on the CPU (Skia) on every host: gfx.canvas.accelerated false.

Measured 2026-11-01 on official Firefox 159 for Windows with the GPU process up
(coord/gfxinfo/canvas): one control scene, accelerated against CPU, differs in
28% of the bytes at 128 px (max 87) and 22% at 512 px (max 108), and the
accelerated bytes follow the host's GPU; below 128 px they are identical. With
the GPU process down, the pref at true still changed 6-122 bytes. So the value is
declared on every host, never left to whether a GPU is there.
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


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("virtual_display", [False, True])
@pytest.mark.parametrize("seed", [1, 12, 29])
def test_canvas_is_not_accelerated_on_any_host(monkeypatch, platform, virtual_display, seed):
    monkeypatch.setattr(sys, "platform", platform)
    prefs = translate_profile_to_prefs(generate_profile(seed=seed), timezone="America/New_York",
                                       virtual_display=virtual_display)
    assert prefs["gfx.canvas.accelerated"] is False


def test_declared_once_not_in_a_platform_table():
    assert "gfx.canvas.accelerated" not in _WIN_VIRT_DESKTOP_WORKAROUNDS
