"""Invisible-but-headed browser windows.

Playwright's ``headless=True`` flips Firefox onto a different code path -
no widget tree, software-only rendering, distinct timing - and anti-bot
systems can spot the divergence. Running the browser *headed* but hidden
gives us the real rendering pipeline while keeping the windows off screen.

One idea, two mechanisms: the browser is not hidden, the SCREEN it draws on
is one nobody looks at.

- **Windows**: a fresh Win32 desktop object (``CreateDesktop``) that is never
  switched to. The spawner creates the browser process with
  ``STARTUPINFO.lpDesktop`` naming it, so the whole tree - launcher, parent,
  GPU and content processes - is born there and never appears on the
  interactive desktop, in the taskbar or in alt-tab. The binary is stock: no
  window attribute is touched from inside it.

- **Linux**: a private ``Xvfb`` instance, with ``DISPLAY`` pointed at it
  (X11/Wayland have no per-window cloak that keeps the GPU rendering).

Both objects carry the same moves: ``start()`` creates the surface,
``launch_env()`` names the variables the BROWSER's environment must carry
for it - and, with the value ``None``, the ones it must NOT carry -,
``stop()`` releases the surface. The launcher merges ``launch_env()`` into
the one environment it composes for the child (``_session.build_env`` in the
wrapper), so the fact stays with the session: a second session in the same
process never inherits the first one's surface. Neither object touches
``os.environ``. Until 34.25.0 the Linux one wrote ``DISPLAY`` there in
``start()`` and restored it in ``stop()``, so a headed session opened while
an Xvfb session was still alive in the same process was born on the Xvfb
(B221); the ``None`` half of the contract is what moved the five Wayland
removals out of the process too.

The in-binary cloak (``DWMWA_CLOAK`` gated by ``zoom.stealth.cloak_windows``)
that hid the Windows window from 2026-06-11 to 2026-09-20 is gone, and its
pref is no longer emitted: the engine is stock on this surface again.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import time
from typing import AbstractSet, Optional


# Inherited from WSLg / GNOME / etc. these env vars make Firefox prefer a
# Wayland compositor over the X11 DISPLAY we set, so the window leaks onto
# the real desktop. `launch_env()` names them with None, and the launcher
# drops them from the browser's environment - the session's, not ours.
_WAYLAND_LEAK_VARS = (
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "PULSE_SERVER",
    "WSL2_GUI_APPS_ENABLED",
)

#: The name of the Win32 desktop the browser must be created on. Put into the
#: browser's launch environment by ``_WindowsVirtualDesktop.launch_env()``,
#: read by the spawner that calls ``CreateProcess`` and REMOVED from the
#: environment it hands the browser: the engine never reads it, so it must
#: not travel into the process tree. Never rename: a published wrapper reads
#: exactly this.
DESKTOP_ENV = "INVPW_DESKTOP"


#: Where Linux lists the sockets of this network namespace. Read, never
#: written; a host without them (not Linux, or /proc not mounted) reports
#: nothing listening and the lockfile check stands alone.
_PROC_UNIX = "/proc/net/unix"
_PROC_TCP = ("/proc/net/tcp", "/proc/net/tcp6")
_X_SOCKET = re.compile(r"^@?/tmp/\.X11-unix/X(\d+)$")
_TCP_LISTEN = "0A"
_X_TCP_BASE = 6000


def _read_proc(path: str) -> str:
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _x_displays_listening() -> set:
    """Display numbers in use in this network namespace: any live unix socket
    named for an X display (abstract or path) in /proc/net/unix, whatever its
    state - a conservative reading - or a TCP listener on 6000+n. Only live
    sockets are listed there, so a stale socket file left in /tmp/.X11-unix
    by a server that has exited does not count."""
    busy = set()
    for line in _read_proc(_PROC_UNIX).splitlines()[1:]:
        # Seven fixed columns, then the path, which may itself contain spaces:
        # match the WHOLE path, never its last word.
        cols = line.split(None, 7)
        m = _X_SOCKET.fullmatch(cols[7]) if len(cols) == 8 else None
        if m:
            busy.add(int(m.group(1)))
    for path in _PROC_TCP:
        for line in _read_proc(path).splitlines()[1:]:
            cols = line.split()
            if len(cols) < 4 or cols[3] != _TCP_LISTEN:
                continue
            try:
                port = int(cols[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if _X_TCP_BASE <= port < _X_TCP_BASE + 1000:
                busy.add(port - _X_TCP_BASE)
    return busy


class _LinuxVirtualDisplay:
    """Standalone Xvfb instance owned by this InvisiblePlaywright session."""

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self._geometry = f"{width}x{height}x24"
        self._proc: Optional[subprocess.Popen] = None
        self._display: Optional[str] = None

    def start(self) -> None:
        if not _binary_on_path("Xvfb"):
            raise RuntimeError(
                "invisible_playwright headless=True requires Xvfb. "
                "Install it: sudo apt install xvfb"
            )
        # Retry: when many workers start in parallel they can pick the same
        # display number before any has created its lockfile. Xvfb on the
        # losing side exits immediately - try again with a fresh number.
        #
        # A number that failed is not offered again. Without that, a display
        # held by an X server whose lockfile this process cannot see (one in a
        # container that shares the host network namespace, whose lock is in
        # the container's own /tmp) was picked on every attempt, and all ten
        # failed on the same number.
        last_err: Optional[Exception] = None
        tried: set[str] = set()
        for _ in range(10):
            display = self._pick_display(exclude=tried)
            tried.add(display)
            try:
                self._spawn(display)
                self._wait_until_ready(display)
                self._display = display
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

    def _pick_display(self, exclude: AbstractSet[str] = frozenset()) -> str:
        # A number is taken when its lockfile exists OR something is listening
        # on it. The lockfile alone misses an X server whose /tmp is not this
        # one - a container sharing the host network namespace keeps its lock
        # and its socket file in its own /tmp, but its abstract socket and TCP
        # port are in the namespace this process shares.
        busy = _x_displays_listening()
        for n in range(99, 400):
            display = f":{n}"
            if display in exclude or n in busy:
                continue
            if os.path.exists(f"/tmp/.X{n}-lock"):
                continue
            return display
        raise RuntimeError("no free X display number in :99-:399")

    def _wait_until_ready(self, display: str) -> None:
        # We start Xvfb with -nolisten unix → no /tmp/.X11-unix socket appears.
        # Xvfb creates /tmp/.X{n}-lock immediately though - wait for that.
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

    def launch_env(self) -> dict:
        """What the browser's environment must carry to draw on this Xvfb.

        ``DISPLAY`` names the display, the two GTK/Firefox switches keep the
        toolkit on X11, and the five Wayland variables are named with ``None``
        so the launcher REMOVES them: inherited from WSLg or GNOME they make
        Firefox prefer the compositor over the display we set, and the window
        leaks onto the real desktop. All of it is this session's, none of it
        is the process's.
        """
        if not self._display:
            return {}
        env: dict = {
            "DISPLAY": self._display,
            "MOZ_ENABLE_WAYLAND": "0",
            "GDK_BACKEND": "x11",
        }
        for k in _WAYLAND_LEAK_VARS:
            env[k] = None
        return env

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
        self._proc = None
        self._display = None


class _WindowsVirtualDesktop:
    """A Win32 desktop object nobody switches to, owned by this session.

    Windows has no per-window cloak that leaves a stock binary untouched, but
    it has something Linux lacks: a process is CREATED on a desktop, and every
    process it spawns is born on the same one. Naming a fresh desktop in
    ``STARTUPINFO.lpDesktop`` therefore hides the whole browser tree at once -
    launcher, parent, GPU process, content processes - with no cooperation
    from the binary. The desktop lives on the interactive window station, so
    the GPU is the real one; only ``SwitchDesktop`` would show it, and nothing
    here ever calls that.

    Two facts about that desktop are consequences, not choices, and the prefs
    that follow from them live in ``prefs._WIN_VIRT_DESKTOP_WORKAROUNDS``:
    the GPU process cannot parent its compositor window across desktops under
    the default GPU sandbox, and content processes above sandbox level 4 are
    put on the sandbox's own window station. Both were measured on this exact
    setup in 2026-05 (`22-patch-port-history.md` §P16, `71-bug-archive.md`
    #18 Bug A) and are what ``virtual_display=True`` switches on.

    ⛔ ``SetThreadDesktop`` on the launching thread is NOT enough, and that is
    the mistake this class replaces for the second time: with ``lpDesktop``
    NULL a child inherits the parent PROCESS desktop, not the thread's, so a
    thread-level switch hid nothing (measured 2026-06-11). The name has to
    reach ``CreateProcess`` itself, which is why ``launch_env()`` hands it to
    the environment the spawner reads - the SESSION's, never the process's:
    a headed session opened while a hidden one is still alive must not be
    born on the hidden one's desktop.
    """

    def __init__(self) -> None:
        self._handle: Optional[int] = None
        self._name: Optional[str] = None

    @property
    def name(self) -> Optional[str]:
        return self._name

    def start(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.CreateDesktopW.restype = wintypes.HANDLE
        user32.CreateDesktopW.argtypes = (
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
        # Unique per session, so two sessions in one process - or in two
        # processes of the same logon - never share a desktop and never see
        # each other's windows.
        name = "invpw_" + secrets.token_hex(6)
        generic_all = 0x10000000
        handle = user32.CreateDesktopW(name, None, None, 0, generic_all, None)
        if not handle:
            err = ctypes.get_last_error()
            raise RuntimeError(
                "invisible_playwright headless=True could not create a hidden "
                "desktop (CreateDesktopW failed, WinError %d). A session with "
                "no interactive window station - a service, a scheduled task "
                "with no logon - cannot host a headed browser; run from a "
                "logged-on session." % err)
        self._handle = int(handle)
        self._name = name

    def launch_env(self) -> dict:
        """The one variable the spawner needs: which desktop to create on."""
        return {DESKTOP_ENV: self._name} if self._name else {}

    def stop(self) -> None:
        if self._handle is not None:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.CloseDesktop.argtypes = (wintypes.HANDLE,)
            user32.CloseDesktop.restype = wintypes.BOOL
            # The object itself outlives this handle for as long as a process
            # still runs on it; closing ours only says we are done with it.
            user32.CloseDesktop(wintypes.HANDLE(self._handle))
        self._handle = None
        self._name = None


def make_virtual_display():
    """Return a start()/stop()-able hidden surface for this platform.

    - Linux: a fresh ``Xvfb`` (the launcher start()s/stop()s it).
    - Windows: a fresh Win32 desktop the spawner creates the browser on.
    """
    if sys.platform.startswith("linux"):
        return _LinuxVirtualDisplay()
    if sys.platform == "win32":
        return _WindowsVirtualDesktop()
    raise RuntimeError(
        f"invisible_playwright supports Windows and Linux "
        f"(macOS is no longer a supported platform; got {sys.platform!r})"
    )


def _binary_on_path(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None
