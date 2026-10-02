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
    assert vd.launch_env() == {}


@pytest.mark.unit
def test_the_display_never_touches_the_process_environment(monkeypatch):
    """⛔ The Linux half of B221. Until 34.25.0 ``start()`` wrote ``DISPLAY``
    into ``os.environ`` and popped the Wayland variables from it, so a headed
    session opened while an Xvfb session was alive in the same process was
    born on the Xvfb. Now the whole fact travels in ``launch_env()``: the
    variables to set, and - with ``None`` - the five to remove. The process
    environment is the same before, during and after.

    Xvfb itself is stubbed out (no display on this host); what is real is
    the object's bookkeeping, which is what the wrapper reads."""
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(headless, "_binary_on_path", lambda name: True)
    fake_proc = type("P", (), {"poll": lambda self: 0})()

    def spawn(self, display):
        self._proc = fake_proc
        return display

    monkeypatch.setattr(_LinuxVirtualDisplay, "_spawn", spawn)
    monkeypatch.setattr(_LinuxVirtualDisplay, "_pick_display", lambda self, exclude=frozenset(): ":123")
    before = dict(os.environ)

    vd = _LinuxVirtualDisplay()
    vd.start()
    env = vd.launch_env()
    assert env["DISPLAY"] == ":123"
    assert env["MOZ_ENABLE_WAYLAND"] == "0"
    assert env["GDK_BACKEND"] == "x11"
    for k in headless._WAYLAND_LEAK_VARS:
        assert k in env and env[k] is None, k
    assert dict(os.environ) == before, "start() wrote into the process"

    vd.stop()
    assert vd.launch_env() == {}
    assert dict(os.environ) == before, "stop() wrote into the process"


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
    ``__exit__`` path on a launcher that failed before Xvfb was spawned."""
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



#  _pick_display / start() - which number is chosen. These are selection and
#  bookkeeping tests: Xvfb is not started, and _spawn is stubbed.

_REAL_EXISTS = os.path.exists


def _only_these_locks(monkeypatch, locks=()):
    """Answer for X lockfiles only; every other path is the real filesystem."""
    def exists(path):
        if str(path).startswith("/tmp/.X") and str(path).endswith("-lock"):
            return path in locks
        return _REAL_EXISTS(path)
    monkeypatch.setattr(headless.os.path, "exists", exists)


def _lowest_first(monkeypatch):
    """The picker chooses at random among the free numbers; these tests pin
    the choice to the lowest, so they can name the number they expect."""
    monkeypatch.setattr(headless.secrets, "choice", lambda seq: seq[0])


def _proc(monkeypatch, unix=""):
    files = {"/proc/net/unix": unix}
    # raising=False: the seam is new, so these tests also run against a
    # version without it and fail there on the behaviour, not on the mock.
    monkeypatch.setattr(headless, "_read_proc", lambda path: files.get(path, ""), raising=False)


_UNIX_HEADER = "Num       RefCount Protocol Flags    Type St Inode Path\n"


def _unix_row(path):
    return f"0000000000000000: 00000002 00000000 00010000 0001 01 35038 {path}\n"


def _stub_start(monkeypatch, failing):
    monkeypatch.setattr(headless, "_binary_on_path", lambda name: True)
    _lowest_first(monkeypatch)
    spawned = []

    def spawn(self, display):
        spawned.append(display)
        if display in failing:
            raise headless._XvfbExited(f"Xvfb {display} exited before it was listening")
        return display

    monkeypatch.setattr(_LinuxVirtualDisplay, "_spawn", spawn)
    return spawned


@pytest.mark.unit
def test_every_display_that_fails_stays_excluded(monkeypatch):
    """A display taken by a server this process cannot see used to be picked
    on all ten attempts. Every failed number is skipped from then on, not just
    the last one: two hidden servers must not be alternated between."""
    _only_these_locks(monkeypatch)
    _proc(monkeypatch)
    spawned = _stub_start(monkeypatch, failing={":99", ":100", ":101"})
    vd = _LinuxVirtualDisplay()
    vd.start()
    assert spawned == [":99", ":100", ":101", ":102"]
    assert vd.launch_env()["DISPLAY"] == ":102"


@pytest.mark.unit
def test_ten_failures_are_ten_different_numbers(monkeypatch):
    _only_these_locks(monkeypatch)
    _proc(monkeypatch)
    spawned = _stub_start(monkeypatch, failing={f":{n}" for n in range(99, 400)})
    with pytest.raises(RuntimeError, match="after 10 attempts"):
        _LinuxVirtualDisplay().start()
    assert spawned == [f":{n}" for n in range(99, 109)]


@pytest.mark.unit
def test_sessions_starting_together_do_not_all_pick_the_same_number(monkeypatch):
    """⛔ The herd. Taking the lowest free number, N sessions starting at once
    all tried :99, every loser moved to :100 together, and so on: the k-th
    needed k attempts and, measured with 15 at once, five ran out of their
    ten. The choice is random among the free numbers, and only among them."""
    _only_these_locks(monkeypatch, locks={"/tmp/.X101-lock"})
    _proc(monkeypatch, unix=_UNIX_HEADER + _unix_row("@/tmp/.X11-unix/X102"))
    vd = _LinuxVirtualDisplay()
    picks = {vd._pick_display(exclude={":103"}) for _ in range(200)}
    assert len(picks) > 50, picks
    assert picks.isdisjoint({":101", ":102", ":103"})
    assert all(99 <= int(d[1:]) <= 399 for d in picks)


@pytest.mark.unit
def test_the_random_choice_is_over_exactly_the_free_numbers(monkeypatch):
    _only_these_locks(monkeypatch, locks={"/tmp/.X100-lock"})
    _proc(monkeypatch, unix=_UNIX_HEADER + _unix_row("@/tmp/.X11-unix/X101"))
    offered = []
    monkeypatch.setattr(headless.secrets, "choice",
                        lambda seq: offered.append(list(seq)) or seq[-1])
    assert _LinuxVirtualDisplay()._pick_display(exclude={":102"}) == ":399"
    assert offered == [[99] + list(range(103, 400))]


@pytest.mark.unit
def test_an_abstract_x_socket_in_this_namespace_is_taken(monkeypatch):
    """The case that started this: a container sharing the host network
    namespace holds :100. Its lockfile and socket FILE are in its own /tmp,
    but its abstract socket is listed in this namespace's /proc/net/unix."""
    _lowest_first(monkeypatch)
    _only_these_locks(monkeypatch, locks={"/tmp/.X99-lock"})
    _proc(monkeypatch, unix=_UNIX_HEADER + _unix_row("@/tmp/.X11-unix/X100"))
    assert _LinuxVirtualDisplay()._pick_display() == ":101"


@pytest.mark.unit
def test_a_path_x_socket_that_is_listening_is_taken(monkeypatch):
    _lowest_first(monkeypatch)
    _only_these_locks(monkeypatch)
    _proc(monkeypatch, unix=_UNIX_HEADER + _unix_row("/tmp/.X11-unix/X99"))
    assert _LinuxVirtualDisplay()._pick_display() == ":100"


@pytest.mark.unit
def test_empty_proc_tables_leave_the_lockfile_check(monkeypatch):
    """Nothing listening: the lockfile check, the only one there was, still
    applies."""
    _lowest_first(monkeypatch)
    _only_these_locks(monkeypatch, locks={"/tmp/.X99-lock"})
    _proc(monkeypatch)
    assert _LinuxVirtualDisplay()._pick_display() == ":100"


@pytest.mark.unit
def test_a_socket_name_with_an_x_looking_suffix_is_not_a_display(monkeypatch):
    """The path is everything after the seventh column and may contain spaces.
    Matching its last word would read these unrelated sockets as :99."""
    _lowest_first(monkeypatch)
    _only_these_locks(monkeypatch)
    _proc(monkeypatch, unix=_UNIX_HEADER
          + _unix_row("@other /tmp/.X11-unix/X99")
          + _unix_row("/tmp/.X11-unix/X99 "))
    assert _LinuxVirtualDisplay()._pick_display() == ":99"


@pytest.mark.unit
@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_an_unreadable_proc_table_is_not_an_error(monkeypatch, error):
    """No /proc, or one this user cannot read: the real reader answers empty
    and the lockfile check still applies."""
    _lowest_first(monkeypatch)
    _only_these_locks(monkeypatch, locks={"/tmp/.X99-lock"})
    real_open = open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/net/unix":
            raise error(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    assert headless._read_proc("/proc/net/unix") == ""
    assert _LinuxVirtualDisplay()._pick_display() == ":100"


# ──────────────────────────────────────────────────────────────────────
#  start() against a fake Xvfb - WHEN a display counts as ready. The fake is
#  a real child process found on PATH, so start() runs unmodified: spawn,
#  readiness, retry, reaping. It behaves like the real server in each mode,
#  with and without -displayfd, so the same tests run against a version that
#  waits for the lockfile and show it wrong.
# ──────────────────────────────────────────────────────────────────────

_FAKE_XVFB = r'''
import json, os, signal, sys, time
args = sys.argv[1:]
display = args[0]
n = display[1:]
fd = int(args[args.index("-displayfd") + 1]) if "-displayfd" in args else None
mode = json.loads(os.environ["FAKE_XVFB_PLAN"]).get(display, "ready")
with open(os.environ["FAKE_XVFB_LOG"], "a") as f:
    f.write(f"{os.getpid()} {display}\n")
lock = f"/tmp/.X{n}-lock"
if mode == "exit-late":
    # Another server holds the number: like the real loser, it lingers for
    # a moment and exits without ever listening.
    time.sleep(0.3)
    sys.exit(1)
if mode == "lock-then-exit":
    # Made its lockfile, then could not open its sockets.
    with open(lock, "w") as f:
        f.write("%10d\n" % os.getpid())
    time.sleep(0.3)
    os.unlink(lock)
    sys.exit(1)
if mode == "hang":
    time.sleep(120)
    sys.exit(0)
# ready, as the real server: with -displayfd it reports on the pipe and
# writes no lockfile; without, it writes the lockfile.
if fd is not None:
    os.write(fd, f"{n}\n".encode())
    os.close(fd)
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
else:
    with open(lock, "w") as f:
        f.write("%10d\n" % os.getpid())
    def bye(*a):
        os.unlink(lock)
        sys.exit(0)
    signal.signal(signal.SIGTERM, bye)
time.sleep(120)
'''

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="spawns a child with an inherited pipe")


@pytest.fixture
def fake_xvfb(tmp_path, monkeypatch):
    """Put a fake ``Xvfb`` first on PATH. Returns ``run(plan, displays)``:
    ``plan`` maps a display to a mode, ``displays`` is what _pick_display
    offers, in order. Returns the (pid, display) of every spawn. Every fake
    still alive at the end is killed, and every lockfile removed."""
    import json
    import signal

    import shlex

    # A sh launcher rather than a shebang naming the interpreter: a shebang
    # line has a length limit, and a virtualenv path can exceed it.
    script = tmp_path / "fake_xvfb.py"
    script.write_bytes(_FAKE_XVFB.encode())
    exe = tmp_path / "Xvfb"
    exe.write_bytes(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} '
        f'{shlex.quote(str(script))} "$@"\n'.encode())
    exe.chmod(0o755)
    log = tmp_path / "spawns.log"
    log.write_bytes(b"")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_XVFB_LOG", str(log))
    used = []

    def spawns():
        return [(int(p), d) for p, d in
                (line.split() for line in log.read_text().splitlines())]

    def run(plan, displays):
        monkeypatch.setenv("FAKE_XVFB_PLAN", json.dumps(plan))
        offered = iter(displays)
        used.extend(displays)
        monkeypatch.setattr(_LinuxVirtualDisplay, "_pick_display",
                            lambda self, exclude=frozenset(): next(offered))
        return spawns

    yield run
    for pid, _ in spawns():
        # Only a fake that still runs: a pid already reaped may belong to
        # someone else by now.
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                ours = str(script).encode() in f.read()
            if ours:
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    for d in used:
        try:
            os.unlink(f"/tmp/.X{d[1:]}-lock")
        except OSError:
            pass


def _gone(pid):
    """Not running and not a zombie: someone waited for it."""
    return not os.path.exists(f"/proc/{pid}")


def _free_high_pair():
    import random
    n = random.randrange(5000, 6000, 2)
    return f":{n}", f":{n + 1}"


@pytest.mark.linux_only
@linux_only
@pytest.mark.parametrize("whose_lock", ["another process's", "its own"])
def test_a_lockfile_is_not_a_listening_server(fake_xvfb, whose_lock):
    """⛔ The defect under PR #85. Ready meant "/tmp/.X{n}-lock exists", and
    a lockfile proves nothing about OUR server listening:

    - another process's: two sessions race for one number, the loser's Xvfb
      exits, and the loser read the WINNER's lockfile as its own - measured,
      6 sessions at once, 3 or 4 of 6 ended on another session's Xvfb;
    - its own: an Xvfb creates its lockfile BEFORE its sockets, so one that
      then cannot open them (the number held from another /tmp) was taken
      for live - measured, main reported :99 started and the server was
      dead half a second later.

    Against that version this fails: start() returns the first number and
    its server is dead. Ready is now the server's own report on -displayfd,
    and its exit before reporting moves start() to another number."""
    first, second = _free_high_pair()
    if whose_lock == "another process's":
        with open(f"/tmp/.X{first[1:]}-lock", "w") as f:
            f.write(f"{os.getpid():10d}\n")        # a live winner holds it
        plan = {first: "exit-late"}
    else:
        plan = {first: "lock-then-exit"}
    spawns = fake_xvfb(plan, [first, second])
    vd = _LinuxVirtualDisplay()
    vd.start()
    try:
        import time
        time.sleep(0.6)
        assert vd._proc is not None and vd._proc.poll() is None, (
            "start() returned on a display whose own Xvfb is dead")
        assert vd.launch_env()["DISPLAY"] == second
        (loser, _), (winner, _) = spawns()
        assert winner == vd._proc.pid
        assert _gone(loser), "the Xvfb that exited was never waited for"
    finally:
        vd.stop()


@pytest.mark.linux_only
@linux_only
def test_a_server_that_neither_reports_nor_exits_is_killed_reaped_and_not_retried(
        fake_xvfb, monkeypatch):
    """A hang is not a taken number: another number would hang the same way,
    so start() stops at the first, kills it and waits for it."""
    monkeypatch.setattr(headless, "_READY_TIMEOUT", 0.5, raising=False)
    first, second = _free_high_pair()
    spawns = fake_xvfb({first: "hang", second: "hang"}, [first, second])
    vd = _LinuxVirtualDisplay()
    with pytest.raises(RuntimeError, match="neither reported ready nor exited"):
        vd.start()
    assert [d for _, d in spawns()] == [first]
    assert _gone(spawns()[0][0])
    assert vd._proc is None and vd.launch_env() == {}


@pytest.mark.linux_only
@linux_only
def test_no_descriptor_outlives_start_and_stop(fake_xvfb):
    """Each attempt opens a pipe. Both ends are closed, on success, on a
    server that exited, and on stop()."""
    first, second = _free_high_pair()
    fake_xvfb({first: "exit-late"}, [first, second])
    before = sorted(os.listdir("/proc/self/fd"))
    vd = _LinuxVirtualDisplay()
    vd.start()
    vd.stop()
    assert sorted(os.listdir("/proc/self/fd")) == before


# ──────────────────────────────────────────────────────────────────────
#  The real Xvfb, where there is one (e2e: not in the default selection).
# ──────────────────────────────────────────────────────────────────────

def _listener_pid(display):
    """The pid that called listen() on the display's abstract socket, or
    None when nothing listens there."""
    import socket
    import struct
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(f"\0/tmp/.X11-unix/X{display[1:]}")
        return struct.unpack("3i", s.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[0]
    except OSError:
        return None
    finally:
        s.close()


real_xvfb = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not headless._binary_on_path("Xvfb"),
    reason="needs a real Xvfb on Linux")


@pytest.mark.e2e
@pytest.mark.linux_only
@real_xvfb
def test_sessions_racing_each_get_their_own_live_server():
    """The judge never asks the module: whoever LISTENS on the display a
    session got must be that session's own Xvfb. Measured before this, 6
    sessions at once: 3 or 4 of 6 on another session's server."""
    import threading
    vds = [_LinuxVirtualDisplay() for _ in range(6)]
    barrier = threading.Barrier(len(vds))
    errors = []

    def go(vd):
        barrier.wait()
        try:
            vd.start()
        except RuntimeError as e:       # reported below, with the others
            errors.append(e)

    threads = [threading.Thread(target=go, args=(vd,)) for vd in vds]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert not errors, errors
        displays = [vd.launch_env()["DISPLAY"] for vd in vds]
        assert len(set(displays)) == len(vds), displays
        for vd, d in zip(vds, displays):
            assert vd._proc.poll() is None, d
            assert _listener_pid(d) == vd._proc.pid, d
    finally:
        for vd in vds:
            vd.stop()


@pytest.mark.e2e
@pytest.mark.linux_only
@real_xvfb
def test_stop_right_after_start_leaves_nothing_behind():
    """Measured before this: stop() right after start() left the lockfile
    40 times out of 40, because ready came before the server's SIGTERM
    handler. Every leftover lockfile is a number no picker offers again."""
    for _ in range(10):
        vd = _LinuxVirtualDisplay()
        vd.start()
        d = vd.launch_env()["DISPLAY"]
        vd.stop()
        assert not os.path.exists(f"/tmp/.X{d[1:]}-lock"), d
        assert _listener_pid(d) is None, d


def _tcp_listeners(port):
    """Listening sockets on ``port`` in /proc/net/tcp and tcp6."""
    found = 0
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        with open(path) as f:
            for line in f.read().splitlines()[1:]:
                cols = line.split()
                if cols[3] == "0A" and int(cols[1].rsplit(":", 1)[1], 16) == port:
                    found += 1
    return found


@pytest.mark.e2e
@pytest.mark.linux_only
@real_xvfb
def test_the_display_is_not_on_the_network():
    """⛔ Until this change the Xvfb listened on TCP 6000+n on every interface
    with access control off: measured, an X client connected from the host's
    LAN address with no credential. The browser never used it - it holds
    one unix connection to the display and no TCP one."""
    vd = _LinuxVirtualDisplay()
    vd.start()
    try:
        d = vd.launch_env()["DISPLAY"]
        assert _listener_pid(d) == vd._proc.pid
        assert _tcp_listeners(6000 + int(d[1:])) == 0
    finally:
        vd.stop()


@pytest.mark.e2e
@pytest.mark.linux_only
@real_xvfb
def test_a_dual_stack_listener_on_the_x_port_does_not_stop_the_display(monkeypatch):
    """With TCP on, a listener on [::]:6000+n made this Xvfb exit with "Cannot
    establish any listening sockets" (one on IPv4 only did not). Without TCP
    the port is no concern of the display at all."""
    import random
    import socket
    for _ in range(50):
        n = random.randrange(3000, 4000)
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            s.bind(("::", 6000 + n))
            break
        except OSError:
            s.close()
    s.listen()
    try:
        monkeypatch.setattr(_LinuxVirtualDisplay, "_pick_display",
                            lambda self, exclude=frozenset(): f":{n}")
        vd = _LinuxVirtualDisplay()
        vd.start()
        try:
            assert vd.launch_env()["DISPLAY"] == f":{n}"
            assert _listener_pid(f":{n}") == vd._proc.pid
        finally:
            vd.stop()
    finally:
        s.close()
