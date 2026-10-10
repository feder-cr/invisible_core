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
import select
import subprocess
import sys
import time
from collections.abc import Set as AbstractSet
from typing import Optional

from ._owned_dirs import owned_dir, sweep_owned_dirs


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


#: Where Linux lists the unix sockets of this network namespace. Read, never
#: written; a host without it (/proc not mounted, or unreadable) reports
#: nothing listening and the lockfile check stands alone.
_PROC_UNIX = "/proc/net/unix"
_X_SOCKET = re.compile(r"@?/tmp/\.X11-unix/X(\d+)")

#: Numbers the picker offers: from :99, as xvfb-run does, so the low numbers a
#: desktop session uses (:0, and WSLg's :0) are never even tried.
_FIRST_DISPLAY = 99
_LAST_DISPLAY = 399
_ATTEMPTS = 10

#: The directory holding one display's Xauthority file (`_owned_dirs`).
XAUTH_PREFIX = "invpw-xauth-"

#: The one authorization protocol Xvfb and every X client share.
_COOKIE_NAME = b"MIT-MAGIC-COOKIE-1"
#: libXau's FamilyWild: the entry matches any address. With an empty display
#: number it also matches any display, so one entry serves whatever number the
#: picker lands on.
_FAMILY_WILD = 0xFFFF


def _write_xauthority(path: str, cookie: bytes) -> None:
    """Write a one-entry Xauthority file, readable by its owner only.

    The format is libXau's: family, then address, display number, auth name
    and auth data, each a 16-bit big-endian length and its bytes. Written here
    instead of with the ``xauth`` tool, which would be one more package to
    install for nothing.
    """
    def field(b: bytes) -> bytes:
        return len(b).to_bytes(2, "big") + b

    record = (_FAMILY_WILD.to_bytes(2, "big") + field(b"") + field(b"")
              + field(_COOKIE_NAME) + field(cookie))
    # O_BINARY: on Windows `os.open` is a TEXT-mode descriptor by default, and a
    # random cookie byte 0x0a went out as "\r\n", one byte longer than the
    # length field says (Windows CI, one run in eight, 2026-10-08). It exists
    # only on Windows; elsewhere the flag is 0 and changes nothing.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, record)
    finally:
        os.close(fd)


#: How long a spawned Xvfb has to report that it is listening. Death is seen
#: at once (end of file on the pipe), so this bounds only a server that
#: neither reports nor exits, and that one is not retried. It is generous
#: because a loaded host must not have a healthy server killed under it: 30
#: sessions starting together on a 16-thread host took up to 0.36 s, and a
#: small or busy CI runner can be many times slower than that.
_READY_TIMEOUT = 30.0

#: How long the Xvfb stays up after its last client has gone (``-terminate``).
#: The browser is the client, so the display ends with it, and the browser
#: already ends with whoever drives it: Juggler closes it when its pipe
#: reaches end of file. ``stop()`` is the ordinary end; this is the end when
#: ``stop()`` never runs, because the owner died of SIGKILL or of a SIGTERM
#: Python does not handle (``timeout``, ``docker stop``, a cancelled CI job).
#: Until this existed that Xvfb lived forever, with its display number taken:
#: measured, both signals left the server running and every browser process
#: gone. The delay is cancelled by any client that connects, so a client that
#: comes and goes BEFORE the browser (a refused one included, measured) does
#: not take the display away from the browser that is still starting.
_TERMINATE_DELAY = 10


def _read_proc(path: str) -> str:
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _x_displays_listening() -> set:
    """Display numbers this network namespace already has an X server on.

    Any unix socket named for a display in /proc/net/unix, abstract or path,
    in any state, since an accepted connection also carries the name and
    still means a live server. The table lists live sockets only, so a stale
    socket file left in /tmp/.X11-unix does not count, and it covers servers
    whose /tmp is not this one (a container sharing the network namespace
    keeps its lockfile in its own /tmp). A path socket counts although this
    Xvfb opens none: a client of that server asking for :n tries the
    abstract name first, and would reach ours.

    TCP is not read: this Xvfb does not listen on it (see ``_spawn``), so a
    listener on 6000+n is no concern of this display.

    A filter, never the judge: whether a number is free is decided by the
    Xvfb started on it (see ``_spawn``). But it is what keeps sessions of
    this package apart, because an Xvfb started with ``-displayfd`` writes no
    lockfile, so the lockfile check cannot see them. Measured without it:
    with 100 sessions alive, 11 of 51 attempts landed on a taken number, and
    with 250 a start ran out of its ten attempts.
    """
    busy = set()
    for line in _read_proc(_PROC_UNIX).splitlines()[1:]:
        # Seven fixed columns, then the path, which may itself contain spaces:
        # match the WHOLE path, never its last word.
        cols = line.split(None, 7)
        m = _X_SOCKET.fullmatch(cols[7]) if len(cols) == 8 else None
        if m:
            busy.add(int(m.group(1)))
    return busy


class _XvfbExited(RuntimeError):
    """The Xvfb started on a number exited before it was listening: the
    number was taken, by something the filters could not see. Another number
    can succeed, so ``start()`` tries one."""


def _read_ready_line(fd: int, timeout: float) -> str | None:
    """The line Xvfb writes on ``-displayfd`` once its sockets are open, or
    ``None`` at end of file: every copy of the write end is closed, which,
    with ours closed after the spawn, means the server has exited."""
    deadline = time.monotonic() + timeout
    buf = b""
    while not buf.endswith(b"\n"):
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([fd], [], [], left)[0]:
            raise TimeoutError
        chunk = os.read(fd, 64)
        if not chunk:
            return None
        buf += chunk
    return buf.decode("ascii", errors="replace").strip()


class _LinuxVirtualDisplay:
    """Standalone Xvfb instance owned by this InvisiblePlaywright session."""

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self._geometry = f"{width}x{height}x24"
        self._proc: Optional[subprocess.Popen] = None
        self._display: Optional[str] = None
        self._auth_dir: Optional[str] = None
        self._auth_file: Optional[str] = None

    def start(self) -> None:
        if not _binary_on_path("Xvfb"):
            raise RuntimeError(
                "invisible_playwright headless=True requires Xvfb. "
                "Install it: sudo apt install xvfb"
            )
        # ⛔ ACCESS CONTROL STAYS ON. The server used to run with ``-ac``: any
        # process in this network namespace, of any user, could connect to
        # the abstract socket (abstract sockets have no file permissions) and
        # read or drive the browser's screen. Now the server loads a random
        # cookie from a file only this user can read, and only the browser,
        # whose environment names that file, presents it.
        #
        # Named with this process's pid and swept at the next start, like the
        # session's profile: a display whose owner was killed never reaches
        # `stop()`, and its cookie directory stayed for good (B268).
        sweep_owned_dirs((XAUTH_PREFIX,))
        self._auth_dir = owned_dir(XAUTH_PREFIX)
        self._auth_file = os.path.join(self._auth_dir, "Xauthority")
        _write_xauthority(self._auth_file, secrets.token_bytes(16))
        try:
            self._start_server()
        except BaseException:
            self._remove_auth()
            raise

    def _start_server(self) -> None:
        # The filters in _pick_display cannot see everything: two sessions
        # can pick the same number before either server has its sockets, and
        # an X server can hold a number in a way no table here shows. Either
        # way our Xvfb exits, _spawn sees it, and the next attempt takes
        # another number. A number that failed is never offered again: two
        # hidden holders must not be alternated between, and one must not be
        # picked on every attempt.
        last_err: Optional[Exception] = None
        tried: set[str] = set()
        for _ in range(_ATTEMPTS):
            display = self._pick_display(exclude=tried)
            tried.add(display)
            try:
                self._display = self._spawn(display)
                return
            except _XvfbExited as e:
                last_err = e
        raise RuntimeError(
            f"Xvfb failed to start after {_ATTEMPTS} attempts: {last_err}")

    def _spawn(self, display: str) -> str:
        """Start Xvfb on ``display`` and return the display it is serving.

        Ready means the SERVER says so: with ``-displayfd`` it writes its
        display number on the pipe after its listening sockets are open and
        its SIGTERM handler is installed, and if it exits first the pipe
        reports end of file. Nothing a third party leaves in /tmp can stand
        in for that. Until this change ready meant "/tmp/.X{n}-lock exists",
        which an Xvfb creates BEFORE opening its sockets: one that then
        failed to open them was taken for a live server, and the loser of a
        race between two sessions read the winner's lockfile as its own -
        both ended on one Xvfb, one of them with a dead one. And a stop()
        right after that kind of ready reached the server before its SIGTERM
        handler, so it died without removing its lockfile.

        The number is passed explicitly: ``-displayfd`` alone makes the
        server scan from :0, which under WSLg takes :0 in front of the
        desktop's own display. Explicit number plus ``-displayfd`` is honoured
        since xorg-server 1.16 (2014). ``-displayfd`` also means the server
        writes no lockfile (``nolock``, in every version), which is why
        _pick_display reads the socket table.

        The one socket it opens is the abstract ``@/tmp/.X11-unix/X{n}``,
        which is what a client asking for ``:n`` reaches first: no socket
        file in /tmp, and no TCP. Until this change it also listened on TCP
        6000+n, on every interface, with access control off (``-ac``): any
        host that could reach the port could read and drive the browser's
        screen, and the browser never used it (measured: Firefox on the
        display holds one unix connection and no TCP one, with TCP on or
        off). It was also one more way to collide: a listener on [::]:6000+n
        made this Xvfb exit.

        Access control is on: ``-auth`` loads the session's cookie (see
        ``start``). Until 37.33.0 it was ``-ac``, so with TCP gone the abstract
        socket was still open, without credentials, to every local process.

        And the server ends with its last client (``-terminate``, see
        ``_TERMINATE_DELAY``), which is the browser: a session whose owner is
        killed no longer leaves an Xvfb behind. The browser opens the display
        before anything else of its own does (``XRE_mainStartup``; the
        ``glxtest`` probe is fired later), so the probe leaving never empties
        the server while the browser is up. A ``start_new_session`` child is
        not in the owner's process group, so without this nothing else would
        ever stop it.
        """
        read_end, write_end = os.pipe()
        try:
            self._proc = subprocess.Popen(
                [
                    "Xvfb", display,
                    "-displayfd", str(write_end),
                    "-screen", "0", self._geometry,
                    "+extension", "GLX",
                    "+extension", "RENDER",
                    "-nolisten", "unix",
                    "-nolisten", "tcp",
                    "-auth", self._auth_file,
                    "-terminate", str(_TERMINATE_DELAY),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=(write_end,),
            )
        except BaseException:
            os.close(read_end)
            raise
        finally:
            # Ours must be closed, or end of file never comes: only the
            # server's copy may keep the pipe open.
            os.close(write_end)
        try:
            line = _read_ready_line(read_end, _READY_TIMEOUT)
        except TimeoutError:
            self._reap()
            raise RuntimeError(
                f"Xvfb {display} neither reported ready nor exited in "
                f"{_READY_TIMEOUT:g}s") from None
        except BaseException:
            self._reap()            # an interrupt must not leave it running
            raise
        finally:
            os.close(read_end)
        if line is None:
            self._reap()
            raise _XvfbExited(f"Xvfb {display} exited before it was listening")
        if not line.isdigit():
            self._reap()
            raise RuntimeError(f"Xvfb {display} reported {line!r} on -displayfd")
        return f":{line}"

    def _reap(self) -> None:
        """Kill our Xvfb if it still runs, and wait for it: a child nobody
        waits for stays a zombie for the life of this process."""
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()
        self._proc = None

    def _pick_display(self, exclude: AbstractSet[str] = frozenset()) -> str:
        # A candidate, not a verdict: _spawn decides. A number is skipped when
        # it failed in this start(), when the socket table shows a server on
        # it, or when its lockfile exists - the lockfile is how an X server
        # in this /tmp announces a number, including one still starting up.
        #
        # The choice among the free ones is random, because sessions starting
        # together see the same free set: taking the lowest, every loser of a
        # collision moved to the same next number and collided again, so the
        # k-th of N simultaneous sessions needed k attempts and the eleventh
        # ran out of them (measured: 15 at once, 5 failed after 10 attempts).
        busy = _x_displays_listening()
        free = [
            n for n in range(_FIRST_DISPLAY, _LAST_DISPLAY + 1)
            if f":{n}" not in exclude
            and n not in busy
            and not os.path.exists(f"/tmp/.X{n}-lock")
        ]
        if not free:
            raise RuntimeError(
                f"no free X display number in :{_FIRST_DISPLAY}-:{_LAST_DISPLAY}")
        return f":{secrets.choice(free)}"

    def launch_env(self) -> dict:
        """What the browser's environment must carry to draw on this Xvfb.

        ``DISPLAY`` names the display, ``XAUTHORITY`` the file holding the
        cookie the server asks for, the two GTK/Firefox switches keep the
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
            "XAUTHORITY": self._auth_file,
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
        self._remove_auth()

    def _remove_auth(self) -> None:
        """Delete the cookie file and its private directory: the cookie is
        this server's, and dies with it."""
        if self._auth_file is not None:
            try:
                os.remove(self._auth_file)
            except OSError:
                pass
            self._auth_file = None
        if self._auth_dir is not None:
            try:
                os.rmdir(self._auth_dir)
            except OSError:
                pass
            self._auth_dir = None


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
