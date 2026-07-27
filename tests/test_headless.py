"""Unit tests for the ``_headless`` window-hider dispatcher.

``make_virtual_display`` is pure platform routing:
- Linux: a ``_LinuxVirtualDisplay`` (Xvfb) object the launcher start()s/stop()s.
- Windows / macOS: ``None`` - the patched binary self-cloaks its chrome windows
  via ``cloak_prefs()`` (injected by the launcher), so nothing host-side spawns.
- Anything else: a clear ``RuntimeError`` naming the platform.

``_LinuxVirtualDisplay`` construction does no I/O (Xvfb is only spawned in
``start()``), so it's safe to exercise on any host.
"""
# MOVED FROM invisible_playwright/tests/ ON 2026-07-27.
#
# Every test in this file exercises code in THIS package and reached it through
# a four-line back-compat shim in the wrapper. That is not where coverage for a
# module belongs, and it was not academic: measured on 2026-07-27, six realistic
# one-line breaks in core code SURVIVED the core's own suite and were caught
# only by the wrapper's - `cloak_prefs()` returning {}, SOCKS detection always
# False, the scheme never stripped from a proxy server, `_proxy_is_set` always
# True, the locale always en-US, `get_default_args()` injecting -headless. The
# core's pre-push gate and its publish gate were both green over all six.
#
# `test_no_test_reaches_the_core_through_a_shim` in the wrapper keeps them here.
from __future__ import annotations

import pytest

import invisible_core._headless as headless
from invisible_core._headless import (
    CLOAK_PREFS,
    _LinuxVirtualDisplay,
    cloak_prefs,
    make_virtual_display,
)


@pytest.mark.unit
def test_make_virtual_display_returns_none_on_win32(monkeypatch):
    """Windows hides via the in-binary cloak pref, not a host-side display."""
    monkeypatch.setattr(headless.sys, "platform", "win32")
    assert make_virtual_display() is None


@pytest.mark.unit
def test_make_virtual_display_returns_none_on_darwin(monkeypatch):
    """macOS is now supported - it hides via the same in-binary cloak pref."""
    monkeypatch.setattr(headless.sys, "platform", "darwin")
    assert make_virtual_display() is None


@pytest.mark.unit
def test_make_virtual_display_returns_linux_xvfb_on_linux(monkeypatch):
    """``__init__`` of ``_LinuxVirtualDisplay`` does no I/O - only ``start()``
    spawns Xvfb. Exercising the dispatcher here is safe on any host."""
    monkeypatch.setattr(headless.sys, "platform", "linux")
    assert isinstance(make_virtual_display(), _LinuxVirtualDisplay)


@pytest.mark.unit
def test_make_virtual_display_accepts_linux_variants(monkeypatch):
    """``sys.platform`` can be ``linux2`` on older Pythons / WSL builds.
    The dispatcher uses ``startswith("linux")`` to accept all variants."""
    monkeypatch.setattr(headless.sys, "platform", "linux2")
    assert isinstance(make_virtual_display(), _LinuxVirtualDisplay)


@pytest.mark.unit
def test_make_virtual_display_raises_on_unsupported_platform(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "freebsd14")
    with pytest.raises(RuntimeError, match="Windows, macOS and Linux"):
        make_virtual_display()


@pytest.mark.unit
def test_make_virtual_display_error_mentions_offending_platform(monkeypatch):
    """Error message should include the actual ``sys.platform`` so the
    user can diagnose why their CI / weird container is being rejected."""
    monkeypatch.setattr(headless.sys, "platform", "sunos5")
    with pytest.raises(RuntimeError, match="sunos5"):
        make_virtual_display()


@pytest.mark.unit
def test_cloak_prefs_enables_cloak_and_disables_occlusion():
    """The cloak prefs must turn on the in-binary cloak and turn OFF Windows
    occlusion tracking (so a hidden window keeps painting). Returns a copy."""
    p = cloak_prefs()
    assert p["zoom.stealth.cloak_windows"] is True
    assert p["widget.windows.window_occlusion_tracking.enabled"] is False
    assert p == CLOAK_PREFS and p is not CLOAK_PREFS


# ──────────────────────────────────────────────────────────────────────
#  _LinuxVirtualDisplay - construction-only smoke tests. ``start()`` is
#  E2E because it spawns Xvfb; ``stop()`` is safe to call when no Xvfb
#  was ever started, so we exercise that path explicitly.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_linux_virtual_display_initial_state_is_clean():
    """Construction must not spawn Xvfb or mutate the environment - only
    ``start()`` does. Mirrors the Windows construction-state test."""
    vd = _LinuxVirtualDisplay()
    assert vd._proc is None
    assert vd._display is None
    assert vd._saved_env == {}


@pytest.mark.unit
def test_linux_virtual_display_geometry_default():
    """Default geometry is 1920x1080x24 - matches the profile sampler's
    default screen and avoids the Xvfb default of 1280x1024 which the
    fingerprint pipeline never produces."""
    vd = _LinuxVirtualDisplay()
    assert vd._geometry == "1920x1080x24"


@pytest.mark.unit
def test_linux_virtual_display_custom_geometry():
    """Caller-supplied width/height feed straight into the Xvfb geometry
    spec; the depth is always 24 (Firefox/ANGLE assume true-color)."""
    vd = _LinuxVirtualDisplay(width=2560, height=1440)
    assert vd._geometry == "2560x1440x24"


@pytest.mark.unit
def test_linux_virtual_display_stop_without_start_is_safe():
    """``stop()`` before ``start()`` must be a no-op - supports the
    ``__exit__`` path on a launcher that failed before Xvfb was spawned.
    Verifies no AttributeError on env restore (saved_env is empty)."""
    vd = _LinuxVirtualDisplay()
    vd.stop()
    vd.stop()
    assert vd._proc is None
    assert vd._display is None
