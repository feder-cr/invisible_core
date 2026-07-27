"""Make a browser tree die when the process that launched it does.

SHARED BY BOTH PRODUCTS, which is why it lives here. Each of them launches the
patched Firefox and each of them had the same problem; the automation wrapper
solved it in 2026-07-26 and the profile manager did not, so on 2026-07-27 the
manager still leaked its ENTIRE tree every single time it was killed - measured
at eight surviving processes, three runs out of three, 100%. Two copies of this
would have been two chances to fix it once.

THE BUG IT EXISTS FOR
---------------------
Twelve runs of one test left eleven browsers alive. The first diagnosis blamed
"any path where teardown does not run cleanly", lumping three situations
together; measured separately, only one of them leaks. An exception out of the
``with`` block does NOT leak - teardown runs, the driver cleans up, zero
survivors over an interleaved A/B. The leak is the KILLED-RUNNER path, where
teardown never executes at all: launch, kill, count - eight survivors, then
twelve. For the manager the equivalent is the user closing the app, the OS
ending it, or a crash: all the same case, and none of them reaches anything the
app could have written.

Nothing written inside a teardown method can reach that, so the guarantee has to
come from the operating system. On Windows a job object with KILL_ON_JOB_CLOSE
does exactly it: the launching process holds the only handle, and when it ends by
any means the kernel terminates the job's members. Processes created inside a job
join it automatically, so content processes spawned later need no tracking.

THE DESIGN
----------
Four pieces with one responsibility each, so the Win32 layer is not tangled with
the policy and neither is tangled with any launcher:

    SessionToken   identity. Mints itself, stamps an environment, and answers
                   whether a given process is ours.
    find_processes the search.
    terminate      immediate cleanup, returning a COUNT.
    LifetimeGuard  the strategy. ``JobObjectGuard`` on Windows, ``NullGuard``
                   everywhere else and whenever the mechanism is unavailable.

``os.name`` is tested in exactly one place - ``guard_for()`` - so no caller
branches on the platform. NullGuard is a Null Object in the strict sense: it
satisfies the interface and REPORTS that it guarantees nothing (``bound == 0``,
``guaranteed is False``) rather than quietly doing nothing and looking
successful.

WHY A TOKEN AND NOT A SEARCH
----------------------------
The manager used to find its browser by matching the ``-profile`` directory,
which works because it owns that path - but the wrapper cannot, because
Playwright creates the profile and never says where. Everything else available -
"a firefox that appeared after we started", "a firefox running our binary" - is
a heuristic, and it invites the one failure a reaper must never have: on a fleet,
several sessions start on the same binary within the same second, and a wrong
guess kills a healthy browser belonging to someone else.

So each session mints a random token and puts it in the browser's environment.
Children inherit it, so every process in the tree carries it, and a process
either has this session's token or it does not. Verified on Windows, where the
launcher stub made this necessary: ``psutil.Process.environ()`` reads a
same-user process's block and children show the inherited value.

The rule throughout: act on POSITIVE identification only. A process whose
environment cannot be read is left alone. Leaking a browser is a bug; killing
someone else's is an incident, and only one of the two is recoverable.
"""
from __future__ import annotations

import os
import secrets
import time
from abc import ABC, abstractmethod
from typing import Any, Iterable, List, Mapping, Optional

# Named in the environment of every process in the tree, read back through
# psutil. The browser itself never looks at it.
#
# The INVPW_ prefix is LEGACY and deliberate. This started in the automation
# wrapper, and the published wrapper reads exactly this name; renaming it would
# make an already-shipped client and a newer core disagree about which processes
# belong to a session - which is the one disagreement a reaper must not have.
TOKEN_VAR = "INVPW_SESSION_TOKEN"

try:  # psutil is a declared dependency; guarded so that an import problem in a
    # user's environment degrades to a Null guard rather than breaking launch.
    import psutil
except Exception:  # pragma: no cover - exercised by the tests' monkeypatching
    psutil = None  # type: ignore[assignment]


# ── identity ──────────────────────────────────────────────────────────────

class SessionToken:
    """What makes a process ours, and the only thing that does.

    A value object: two tokens are equal when their values are, and an EMPTY
    token is falsy and matches nothing. That last part is load-bearing rather
    than defensive tidiness - without it, a session that failed before minting
    one would sweep every process whose variable happens to be empty.
    """

    __slots__ = ("value",)

    def __init__(self, value: str = "") -> None:
        self.value = value

    @classmethod
    def mint(cls) -> "SessionToken":
        return cls(secrets.token_hex(16))

    def __bool__(self) -> bool:
        return bool(self.value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SessionToken) and other.value == self.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"SessionToken({self.value[:8]}...)" if self else "SessionToken(empty)"

    def stamp(self, env: Mapping[str, str]) -> dict:
        """A copy of ``env`` the browser will pass on to its children."""
        out = dict(env)
        out[TOKEN_VAR] = self.value
        return out

    def matches(self, proc: Any) -> bool:
        """True only when the environment can be READ and holds this token.

        Every failure answers False. A process we cannot inspect is a process
        we do not touch.
        """
        if not self:
            return False
        try:
            return proc.environ().get(TOKEN_VAR) == self.value
        except Exception:
            return False


# ── the search: the only place psutil is used ─────────────────────────────

def find_processes(token: SessionToken) -> List[Any]:
    """Every live process carrying ``token``, deepest child first.

    Deepest-first so that terminating in order reaches the content processes
    before the parent that would otherwise orphan or restart them.
    """
    if psutil is None or not token:
        return []
    found = [p for p in psutil.process_iter(["pid", "ppid"]) if token.matches(p)]
    pids = {p.pid for p in found}
    # The token already delimits the tree exactly, so "deeper" is just "my
    # parent is also in the set" - no tree walk needed.
    found.sort(key=lambda p: p.info.get("ppid") in pids, reverse=True)
    return found


def terminate(procs: Iterable[Any], *, timeout: float = 5.0) -> int:
    """Graceful terminate, then kill the survivors. Returns how many were sent.

    A count rather than nothing, so a caller can tell "nothing was leaked" from
    "the reaper did not run" - two outcomes that look identical from outside.
    """
    procs = list(procs)
    if not procs:
        return 0
    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            pass
    alive = procs
    try:
        _, alive = psutil.wait_procs(procs, timeout=timeout)
    except Exception:
        pass
    for proc in alive:
        try:
            proc.kill()
        except Exception:
            pass
    if alive:
        try:
            psutil.wait_procs(alive, timeout=1.0)
        except Exception:
            pass
    return len(procs)


def alive(proc: Any) -> bool:
    """Cross-OS liveness for one process. False when gone OR a zombie.

    A zombie answers ``is_running()`` True on POSIX, and reading that as alive
    is how a UI shows a profile RUNNING forever after its browser has died.
    Came from the manager, which is where that was learned.
    """
    if proc is None or psutil is None:
        return False
    try:
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def wait_until_gone(token: SessionToken, timeout: float = 10.0) -> bool:
    """True once nothing carries ``token`` any more, False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not find_processes(token):
            return True
        time.sleep(0.2)
    return not find_processes(token)


# ── the strategy ──────────────────────────────────────────────────────────

class LifetimeGuard(ABC):
    """Tie a session's process tree to this process's lifetime."""

    #: False on any implementation that cannot actually promise it.
    guaranteed: bool = False

    @abstractmethod
    def bind(self, token: SessionToken, *, wait: float = 10.0) -> int:
        """Adopt the tree carrying ``token``. Returns how many were adopted."""

    def reap(self, token: SessionToken, *, timeout: float = 5.0) -> int:
        """Terminate the tree now, for teardowns that DO get to run.

        Kept even where ``bind`` guarantees the kill, because a browser that
        refuses ``close()`` should not wait for this process to exit.
        """
        return terminate(find_processes(token), timeout=timeout)


class NullGuard(LifetimeGuard):
    """No OS-level guarantee here, and it says so instead of implying one.

    The honest answer on platforms without the mechanism and on any failure to
    obtain it. ``reap`` still works: it is inherited, because immediate cleanup
    is not the part that needs a kernel.
    """

    guaranteed = False

    def bind(self, token: SessionToken, *, wait: float = 10.0,
             settle: float = 1.0) -> int:
        return 0

    def spawn_into(self, argv, *, env=None, **popen_kwargs):
        """An ordinary spawn, and it says so by being a NullGuard.

        Same signature as the real one so no caller branches on the platform -
        that is the whole point of the Null implementation.
        """
        import subprocess

        return subprocess.Popen(argv, env=env, **popen_kwargs)


class JobObjectGuard(LifetimeGuard):
    """Windows. The kernel empties the job when our last handle closes.

    The handle is held for the life of the guard and never closed explicitly:
    closing it is precisely the event that kills the job, so the process
    exiting - by any route, including being killed - is what triggers it.
    """

    guaranteed = True

    _KILL_ON_JOB_CLOSE = 0x2000
    _EXTENDED_LIMIT_INFORMATION = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    def __init__(self, handle: Any, kernel32: Any) -> None:
        self._handle = handle
        self._kernel32 = kernel32
        self._adopted: set = set()

    @classmethod
    def create(cls) -> "LifetimeGuard":
        """A guard, or a NullGuard if the mechanism is not available."""
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return NullGuard()
            info = _ExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = cls._KILL_ON_JOB_CLOSE
            ok = kernel32.SetInformationJobObject(
                handle, cls._EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info),
            )
            if not ok:
                kernel32.CloseHandle(handle)
                return NullGuard()
            return cls(handle, kernel32)
        except Exception:
            return NullGuard()

    def bind(self, token: SessionToken, *, wait: float = 10.0,
             settle: float = 1.0) -> int:
        """Wait for the tree to appear, then adopt ALL of it.

        The wait exists because the driver spawns the browser asynchronously.
        The token makes it exact rather than a guess about how long that takes.

        WHY IT KEEPS SCANNING AFTER THE FIRST SUCCESS. Until 2026-07-27 this
        loop did `if bound: break`, so it adopted whatever existed at the first
        instant anything existed, and returned. Processes appearing a moment
        later escaped unless Windows added them automatically - which it only
        does for CHILDREN of a job member, and on Windows `firefox.exe` is a
        launcher stub that exits, re-parenting the real browser away from
        anything that was adopted.

        Measured by killing the runner mid-session and counting survivors:
        four runs gave sync 0/0/0/0 and async 2/0/0/2. Intermittent, ~50% on
        the async path, 0% on the sync one - not because the two APIs differ
        here, but because their timing does, which is exactly the shape that
        makes a race look like a working feature.

        So it now scans until a full pass adds nothing for `settle` seconds,
        bounded by `wait`. The cost is about a second on launch; the thing it
        buys is that "the kernel is holding this tree" stops being a coin flip.
        """
        if not token:
            return 0
        deadline = time.monotonic() + wait
        bound = 0
        quiet_since: Optional[float] = None
        while time.monotonic() < deadline:
            added = 0
            for proc in find_processes(token):
                if proc.pid in self._adopted:
                    continue
                # ONLY a success is recorded. It used to add the pid either way,
                # so `_adopted` meant "tried" while reading as "held": a process
                # whose assignment failed was never retried, and the returned
                # count was a number of attempts. Measured on the profile
                # manager, that reported 8 adopted out of 8 while 8 survived the
                # kill - the guard was reporting a guarantee it did not have.
                if self._adopt(proc.pid):
                    self._adopted.add(proc.pid)
                    added += 1
            bound += added
            now = time.monotonic()
            if added:
                quiet_since = None
            elif bound:
                if quiet_since is None:
                    quiet_since = now
                elif now - quiet_since >= settle:
                    break
            time.sleep(0.25)
        return bound

    def spawn_into(self, argv, *, env=None, **popen_kwargs):
        """Start a process ALREADY INSIDE the job, so its whole tree is born in it.

        This exists because adopting afterwards does not work for this browser,
        and the measurement is unambiguous. On the profile manager's launch path,
        `AssignProcessToJobObject` returns **ERROR_ACCESS_DENIED (5)** for six of
        the eight processes and ERROR_NOT_ENOUGH_QUOTA (1816) for a seventh:
        Firefox puts its own content processes into its own sandbox jobs, and a
        process already in a job that does not permit nesting cannot be added to
        another one. Only the top process could be adopted, and the tree survived
        the kill 8 out of 8, three runs of three.

        Creating the process suspended, assigning it, and only then resuming it
        inverts that: everything it spawns is created inside our job from the
        start, which is the case Windows does support. `psutil` does the resume
        because `subprocess.Popen` does not expose the thread handle needed for
        `ResumeThread`.

        Falls back to an ordinary spawn if anything here fails - a launch that
        works without a guarantee beats a guarantee that refuses to launch.
        """
        import subprocess

        _CREATE_SUSPENDED = 0x00000004
        flags = popen_kwargs.pop("creationflags", 0) | _CREATE_SUSPENDED
        try:
            proc = subprocess.Popen(argv, env=env, creationflags=flags,
                                    **popen_kwargs)
        except Exception:
            return subprocess.Popen(argv, env=env, **popen_kwargs)
        try:
            if not self._adopt(proc.pid):
                raise OSError("the process could not be assigned to the job")
            self._adopted.add(proc.pid)
        except Exception:
            # Resume it anyway: a suspended browser that nobody resumes is a
            # worse outcome than an unguarded one.
            self._resume(proc.pid)
            return proc
        self._resume(proc.pid)
        return proc

    @staticmethod
    def _resume(pid: int) -> None:
        try:
            import psutil as _ps

            _ps.Process(pid).resume()
        except Exception:
            pass

    def _adopt(self, pid: int) -> bool:
        handle = self._kernel32.OpenProcess(
            self._PROCESS_SET_QUOTA | self._PROCESS_TERMINATE, False, pid
        )
        if not handle:
            return False
        try:
            return bool(
                self._kernel32.AssignProcessToJobObject(self._handle, handle)
            )
        finally:
            self._kernel32.CloseHandle(handle)


def _define_job_structures():
    """The Win32 layout, built once and only when ctypes is importable."""
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in
                    ("ReadOperationCount", "WriteOperationCount",
                     "OtherOperationCount", "ReadTransferCount",
                     "WriteTransferCount", "OtherTransferCount")]

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    return ExtendedLimit


try:
    _ExtendedLimitInformation = _define_job_structures()
except Exception:  # pragma: no cover - non-Windows, or no ctypes
    _ExtendedLimitInformation = None  # type: ignore[assignment]


def guard_for(platform: str | None = None) -> LifetimeGuard:
    """The only place the platform is tested.

    Every caller gets a LifetimeGuard and never asks which one it is: the Null
    implementation satisfies the same interface, so no launcher code branches
    on Windows.
    """
    name = os.name if platform is None else platform
    if name != "nt" or psutil is None or _ExtendedLimitInformation is None:
        return NullGuard()
    return JobObjectGuard.create()
