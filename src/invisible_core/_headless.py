"""Invisible-but-headed browser windows.

Playwright's ``headless=True`` flips Firefox onto a different code path —
no widget tree, software-only rendering, distinct timing — and anti-bot
systems can spot the divergence. Running the browser *headed* but hidden
gives us the real rendering pipeline while keeping the windows off screen.

Two mechanisms, by platform:

- **Windows & macOS**: the patched binary cloaks its OWN chrome windows
  when ``zoom.stealth.cloak_windows`` is set — ``DWMWA_CLOAK`` (Windows)
  / ``NSWindow`` alpha-0 + pinned occlusion-ignore (macOS). The window
  renders on the real GPU but never appears on screen, in the taskbar or
  the Dock. The launcher injects the pref; nothing host-side is spawned.

- **Linux**: spawns its own ``Xvfb`` instance and points ``DISPLAY`` at
  it (X11/Wayland have no per-window cloak that keeps the GPU rendering).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Optional


# Inherited from WSLg / GNOME / etc. these env vars make Firefox prefer a
# Wayland compositor over the X11 DISPLAY we set, so the window leaks onto
# the real desktop. Strip them all before starting.
_WAYLAND_LEAK_VARS = (
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "PULSE_SERVER",
    "WSL2_GUI_APPS_ENABLED",
)


class _LinuxVirtualDisplay:
    """Standalone Xvfb instance owned by this InvisiblePlaywright session."""

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self._geometry = f"{width}x{height}x24"
        self._proc: Optional[subprocess.Popen] = None
        self._display: Optional[str] = None
        self._saved_env: dict[str, Optional[str]] = {}

    def start(self) -> None:
        if not _binary_on_path("Xvfb"):
            raise RuntimeError(
                "invisible_playwright headless=True requires Xvfb. "
                "Install it: sudo apt install xvfb"
            )
        # Retry: when many workers start in parallel they can pick the same
        # display number before any has created its lockfile. Xvfb on the
        # losing side exits immediately — try again with a fresh number.
        last_err: Optional[Exception] = None
        for _ in range(10):
            display = self._pick_display()
            try:
                self._spawn(display)
                self._wait_until_ready(display)
                self._display = display
                self._apply_env(display)
                return
            except RuntimeError as e:
                last_err = e
                if self._proc is not None and self._proc.poll() is None:
                    self._proc.kill()
                self._proc = None
        raise RuntimeError(f"Xvfb failed to start after 10 attempts: {last_err}")

    def _spawn(self, display: str) -> None:
        self._proc = subprocess.Popen(
            [
                "Xvfb", display,
                "-screen", "0", self._geometry,
                "+extension", "GLX",
                "+extension", "RENDER",
                "-nolisten", "unix",
                "-listen", "tcp",
                "-ac",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _pick_display(self) -> str:
        for n in range(99, 400):
            if not os.path.exists(f"/tmp/.X{n}-lock"):
                return f":{n}"
        raise RuntimeError("no free X display number in :99–:399")

    def _wait_until_ready(self, display: str) -> None:
        # We start Xvfb with -nolisten unix → no /tmp/.X11-unix socket appears.
        # Xvfb creates /tmp/.X{n}-lock immediately though — wait for that.
        lockfile = f"/tmp/.X{display[1:]}-lock"
        deadline = time.monotonic() + 3.0
        assert self._proc is not None
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(f"Xvfb {display} exited immediately")
            if os.path.exists(lockfile):
                return
            time.sleep(0.02)
        raise RuntimeError(f"Xvfb {display} did not become ready in 3s")

    def _apply_env(self, display: str) -> None:
        keys = ("DISPLAY", "MOZ_ENABLE_WAYLAND", "GDK_BACKEND") + _WAYLAND_LEAK_VARS
        for k in keys:
            self._saved_env[k] = os.environ.get(k)
        for k in _WAYLAND_LEAK_VARS:
            os.environ.pop(k, None)
        os.environ["DISPLAY"] = display
        os.environ["MOZ_ENABLE_WAYLAND"] = "0"
        os.environ["GDK_BACKEND"] = "x11"

    def stop(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._saved_env.clear()

        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
        self._proc = None
        self._display = None


# Windows & macOS: the patched Firefox cloaks its own chrome windows when this
# pref is set (DWMWA_CLOAK / NSWindow alpha-0 + pinned occlusion-ignore), so the
# window renders on the real GPU but never shows on screen / in the taskbar or
# Dock. window_occlusion_tracking is disabled so a hidden window keeps painting.
CLOAK_PREFS = {
    "zoom.stealth.cloak_windows": True,
    "widget.windows.window_occlusion_tracking.enabled": False,
}


def cloak_prefs() -> dict:
    """Prefs that make the patched binary self-cloak its chrome windows.

    Used on Windows & macOS, where hiding is done inside the binary rather than
    with a host-side virtual display.
    """
    return dict(CLOAK_PREFS)


def make_virtual_display():
    """Return a start()/stop()-able virtual display, or ``None`` when the
    platform hides windows via the in-binary cloak pref instead.

    - Linux: a fresh ``Xvfb`` (the launcher start()s/stop()s it).
    - Windows / macOS: ``None`` — the binary self-cloaks via ``cloak_prefs()``,
      injected by the launcher; nothing host-side needs spawning.
    """
    if sys.platform.startswith("linux"):
        return _LinuxVirtualDisplay()
    if sys.platform in ("win32", "darwin"):
        return None
    raise RuntimeError(
        f"invisible_playwright supports Windows, macOS and Linux "
        f"(got {sys.platform!r})"
    )


def _binary_on_path(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None
