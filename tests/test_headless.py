"""Unit tests for the ``_headless`` window-hider dispatcher.

``make_virtual_display`` is pure platform routing:
- Linux: a ``_LinuxVirtualDisplay`` (Xvfb) object the launcher start()s/stop()s.
- Windows: a ``_WindowsVirtualDesktop`` (a Win32 desktop object) the spawner
  creates the browser on, by name, through ``STARTUPINFO.lpDesktop``.
- Anything else: a clear ``RuntimeError`` naming the platform.

Construction of either object does no I/O (Xvfb is only spawned, and the
desktop only created, in ``start()``), so the routing is safe to exercise on
any host. The Windows ``start()``/``stop()`` pair IS exercised for real on a
Windows host: a desktop object costs nothing to create and is the one thing
this module has to get right there.
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

import os
import sys

import pytest

import invisible_core._headless as headless
from invisible_core._headless import (
    DESKTOP_ENV,
    _LinuxVirtualDisplay,
    _WindowsVirtualDesktop,
    make_virtual_display,
)


@pytest.mark.unit
def test_make_virtual_display_returns_a_win32_desktop_on_win32(monkeypatch):
    """Windows hides by creating the browser on a desktop nobody looks at.

    Until 2026-09-20 this returned ``None`` and the binary cloaked its own
    window through a pref; the owner chose a stock engine on that surface,
    so the hiding place is the desktop again, as it is Xvfb on Linux."""
    monkeypatch.setattr(headless.sys, "platform", "win32")
    assert isinstance(make_virtual_display(), _WindowsVirtualDesktop)


@pytest.mark.unit
def test_make_virtual_display_raises_on_darwin(monkeypatch):
    """macOS is no longer supported: a Mac stops here instead of carrying on.

    Until firefox-20 this returned ``None`` (the binary cloaked itself through
    a pref). From firefox-21 the Mac is not a target any more: the refusal
    belongs at the boundary, with a message naming the why, rather than left
    to an obscure failure further downstream."""
    monkeypatch.setattr(headless.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="macOS is no longer a supported"):
        make_virtual_display()


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
    with pytest.raises(RuntimeError, match="Windows and Linux"):
        make_virtual_display()


@pytest.mark.unit
def test_make_virtual_display_error_mentions_offending_platform(monkeypatch):
    """Error message should include the actual ``sys.platform`` so the
    user can diagnose why their CI / weird container is being rejected."""
    monkeypatch.setattr(headless.sys, "platform", "sunos5")
    with pytest.raises(RuntimeError, match="sunos5"):
        make_virtual_display()


@pytest.mark.unit
def test_the_cloak_is_gone_from_this_module():
    """⛔ Frozen on purpose. ``cloak_prefs`` / ``CLOAK_PREFS`` switched on a
    DWMWA_CLOAK inside the binary from 2026-06-11 to 2026-09-20. The engine is
    stock on that surface now, and a helper that emitted the pref again would
    be a pref the engine no longer reads - a silent no-op, which is the worst
    kind of hiding."""
    assert not hasattr(headless, "cloak_prefs")
    assert not hasattr(headless, "CLOAK_PREFS")


@pytest.mark.unit
def test_the_desktop_variable_name_is_a_contract():
    """The spawner in the published wrapper reads exactly this name and pops
    it before the browser sees it. Renaming it makes an older wrapper create
    the browser on the visible desktop with no error anywhere."""
    assert DESKTOP_ENV == "INVPW_DESKTOP"


# ──────────────────────────────────────────────────────────────────────
#  _WindowsVirtualDesktop
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_windows_virtual_desktop_initial_state_is_clean():
    """Construction must not create a desktop - only ``start()`` does - and
    before that there is nothing to put in a browser's environment."""
    vd = _WindowsVirtualDesktop()
    assert vd.name is None
    assert vd._handle is None
    assert vd.launch_env() == {}


@pytest.mark.unit
def test_the_desktop_never_touches_the_process_environment(monkeypatch):
    """⛔ THE FACT STAYS WITH THE SESSION. A headed session opened while a
    hidden one is still alive in the same process must not be born on the
    hidden one's desktop, which is what a name written into ``os.environ``
    would do (it is what ``DISPLAY`` does on Linux, and that is recorded, not
    copied). The known-bad input is the first draft of this class, which
    wrote ``INVPW_DESKTOP`` into ``os.environ`` in ``start()``."""
    monkeypatch.delenv(DESKTOP_ENV, raising=False)
    vd = _WindowsVirtualDesktop()
    vd._name = "invpw_fake"       # as if start() had run, without a desktop
    assert vd.launch_env() == {DESKTOP_ENV: "invpw_fake"}
    assert DESKTOP_ENV not in os.environ


@pytest.mark.unit
def test_windows_virtual_desktop_stop_without_start_is_safe():
    """``stop()`` before ``start()`` must be a no-op - the ``__exit__`` path
    on a launcher that failed before the desktop was created."""
    vd = _WindowsVirtualDesktop()
    vd.stop()
    vd.stop()
    assert vd.name is None
    assert vd._handle is None


@pytest.mark.unit
@pytest.mark.skipif(sys.platform != "win32", reason="creates a real Win32 desktop")
def test_windows_virtual_desktop_start_names_a_fresh_desktop_and_stop_releases_it(monkeypatch):
    """The real thing, because it is cheap: a desktop object is created, its
    name is what ``launch_env()`` hands the spawner, and ``stop()`` lets it
    go without ever having touched ``os.environ``."""
    import ctypes
    from ctypes import wintypes

    monkeypatch.setenv(DESKTOP_ENV, "somebody-elses")
    vd = _WindowsVirtualDesktop()
    vd.start()
    try:
        assert vd.name and vd.name.startswith("invpw_")
        assert vd.launch_env() == {DESKTOP_ENV: vd.name}
        assert os.environ[DESKTOP_ENV] == "somebody-elses"
        # It exists, and it is not the one the caller is on.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        desktop_read_objects = 0x0001
        h = user32.OpenDesktopW(vd.name, 0, False, desktop_read_objects)
        assert h, "the desktop the session named cannot be opened"
        user32.CloseDesktop(wintypes.HANDLE(h))
        assert vd.name != "Default"
    finally:
        vd.stop()
    assert os.environ[DESKTOP_ENV] == "somebody-elses"
    assert vd.name is None
    assert vd.launch_env() == {}


@pytest.mark.unit
@pytest.mark.skipif(sys.platform != "win32", reason="creates real Win32 desktops")
def test_two_sessions_never_share_a_desktop():
    """Two sessions in one process must not see each other's windows: the
    name carries a random token, so the second ``CreateDesktop`` makes a
    second object rather than opening the first."""
    a, b = _WindowsVirtualDesktop(), _WindowsVirtualDesktop()
    a.start()
    try:
        b.start()
        try:
            assert a.name != b.name
        finally:
            b.stop()
    finally:
        a.stop()


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


@pytest.mark.unit
def test_occlusion_tracking_is_off_for_every_session():
    """⛔ Il test che avrebbe trovato il buco, e che non esisteva.

    `widget.windows.window_occlusion_tracking.enabled` stava in `CLOAK_PREFS`,
    che `compose_session_prefs` fondeva SOLO col cloak acceso - e il cloak
    richiedeva `headless=True`. So the default path, headful, ran with the
    tracker ON, and a page in that session could read a browser put into the
    background: `requestAnimationFrame` at 1 Hz, `setTimeout` clamped to
    1000 ms, `visibilityState` hidden, `enumerateDevices()` never resolving.
    Those are SUPPRESSED signals on surfaces a detector reads, that is, a FAIL
    under rule 12, not a performance detail.

    The cloak is gone (2026-09-20); the pref stays, for every session, headed
    or on a hidden desktop, because the observable list above never depended
    on the cloak. The known-bad input is a composition that ties it to any
    flag: this assertion is green only if it is there with NO flag at all.
    """
    from invisible_core._fpforge import generate_profile
    from invisible_core.prefs import compose_session_prefs

    profile = generate_profile(seed=4242)
    key = "widget.windows.window_occlusion_tracking.enabled"

    plain = compose_session_prefs(profile).prefs
    assert plain[key] is False, (
        "with no flag the tracker would stay on, and the page would read a "
        "browser in the background")

    hidden = compose_session_prefs(profile, virtual_display=True).prefs
    assert hidden[key] is False

    # And an explicit caller override must still win: it is a setdefault.
    forced = compose_session_prefs(profile, extra_prefs={key: True}).prefs
    assert forced[key] is True
