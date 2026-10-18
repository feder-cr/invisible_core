"""A test reaches outside this process only when it says so.

The markers every repository sharing this core declares (the list
`tests/test_marker_vocabulary.py` keeps identical) define `e2e` as a test that
"needs something outside this process - the network, the real patched binary,
or a display", and the default selection excludes it. Nothing checked either
half of that sentence:

* a test marked e2e that needs none of the three is invisible to the default
  selection, and so to the pre-push: ten in the wrapper's `tests/test_e2e.py`,
  two of which a core requirement broke where only CI could see it ([B345],
  2026-10-17);
* a test NOT marked e2e that starts a browser runs in every pre-push, on the
  machine of whoever pushes.

This plugin turns the marker into a checked statement. A repository loads it
from its root conftest (`pytest_plugins = ["invisible_core.testing.world"]`, never an
entry point: a user's own test suite is none of its business). It installs one
audit hook (PEP 578), and lets each test reach the world only if it is marked
e2e:

* the engine: the core's own `invisible_core.engine.launch` event, raised by
  the two spawns `juggler.connection.launch` uses, where the process is
  created - on Windows that is `CreateProcessW` through ctypes, which no
  `subprocess` event sees - and any spawn of a program named like the engine;
* a display server: a spawn of Xvfb, which Python's own `subprocess` event
  shows. A Windows hidden desktop is not one: `CreateDesktopW` starts no
  process and reaches no network, it is an object of the logon session
  released at stop, so the tests that make one stay in the default selection;
* the network: a connection, a datagram or a name lookup that is not loopback.
  A local test server is inside this process's world, so loopback stays open.

A stand-in is neither the engine nor a display: a script (`#!`) that the
session wrote into its own temporary directory and that only borrows the name,
like the fake `Xvfb` that `tests/test_headless.py` puts first on PATH to drive
the display's start without a display. A real engine or Xvfb is a compiled
program, and one extracted into a temporary directory is still refused. A
stand-in is judged by where it is and by its first line, not by what it runs:
the guard ends at its exec, as it does for any child process.

A test that is not marked e2e and tries one of them gets `WorldRefused` from the
hook, so the call never happens, and the test is reported failed even if it
caught the exception: the attempt is what is wrong, not the traceback.

The configuration of whoever runs the suite is outside too. Every variable the
library reads (`invisible_core.environment.READ`) is taken out of the
environment while the suite collects and while a test not marked e2e runs, and
put back after: such a test runs in nobody's configuration, so an engine folder
or a seal file set in a developer's shell cannot turn it red (13 tests were,
with `INVISIBLE_PLAYWRIGHT_ENGINE_DIR` set). A test marked e2e keeps them: a
run against a local engine needs its `INVISIBLE_SEAL_FILE`. What the core reads
at import is read before any hook runs, so a seal file in the shell still
decides the session's engine identity: one interpreter has one.

`--world-report` judges the other half. An e2e test that passed without
touching the world, by itself or through a fixture that did, is mis-marked, and
the session fails naming it. A fixture counts because e2e modules open one
browser for many tests: only the first records the launch, and the others use
the browser it left. A test that started a child process is not judged, because
the hook cannot see what the child does.

What it does not see: a child process (the hook lives in this interpreter), and
a spawn that goes around both `launch` and Python's own events, a ctypes call
elsewhere, which `tests/test_world.py` pins as absent from the core.
"""
from __future__ import annotations

import ipaddress
import os
import shutil
import sys
from typing import Dict, List, Optional, Tuple

import pytest

from .. import environment
from ..juggler.connection import LAUNCH_EVENT as ENGINE_EVENT

#: Spawned under these names, a program is the engine or a display.
_ENGINE_PROGRAMS = frozenset({"firefox", "firefox.exe", "firefox-bin"})
_DISPLAY_PROGRAMS = frozenset({"xvfb", "xvfb-run"})
_SPAWN_EVENTS = frozenset({"subprocess.Popen", "os.posix_spawn", "os.spawn",
                           "os.exec", "os.system", "os.startfile"})
#: Raised by `_winapi.CreateProcess`, which `subprocess.Popen` calls on Windows
#: after its own event: a second event for the same child, so it names the
#: program but is never counted as one. Called directly, it is the only event.
_WIN32_SPAWN = "_winapi.CreateProcess"
_ADDRESS_EVENTS = frozenset({"socket.connect", "socket.sendto"})
_NAME_EVENTS = frozenset({"socket.getaddrinfo", "socket.gethostbyname",
                          "socket.gethostbyaddr"})
_WATCHED = (frozenset({ENGINE_EVENT, _WIN32_SPAWN}) | _SPAWN_EVENTS
            | _ADDRESS_EVENTS | _NAME_EVENTS)

#: The user property a call report carries when an e2e test touched nothing.
UNTOUCHED = ("world", "untouched")


class WorldRefused(RuntimeError):
    """A test that is not marked e2e reached outside this process."""


def _first_word(command) -> str:
    text = os.fsdecode(command).strip()
    if text[:1] in ('"', "'"):
        end = text.find(text[0], 1)
        return text[1:end if end > 0 else None]
    return text.split()[0] if text else ""


def _command(event: str, args: tuple) -> Tuple[str, object]:
    """What a spawn event starts, as given, and the environment it gets."""
    env = None
    if event == "subprocess.Popen":  # executable, args, cwd, env
        executable, argv, env = args[0], args[1], args[3]
        if executable:
            first = os.fsdecode(executable)
        elif isinstance(argv, (str, bytes)):
            first = _first_word(argv)
        else:
            argv = list(argv or [])
            first = os.fsdecode(argv[0]) if argv else ""
    elif event == _WIN32_SPAWN:  # application_name, command_line, cwd
        first = os.fsdecode(args[0]) if args[0] else _first_word(args[1] or "")
    elif event == "os.system":
        first = _first_word(args[0])
    elif event == "os.spawn":  # mode, path, args, env
        first, env = os.fsdecode(args[1]), args[3]
    elif event in ("os.posix_spawn", "os.exec"):  # path, args, env
        first, env = os.fsdecode(args[0]), args[2]
    else:  # os.startfile: path, operation
        first = os.fsdecode(args[0])
    return first, env


def _program(first: str) -> str:
    """The basename, lowercased."""
    return os.path.basename(first.replace("\\", "/")).lower()


def _search_path(env) -> Optional[str]:
    """The PATH a spawn resolves a bare name on: its own env, or ours."""
    if env is None:
        return os.environ.get("PATH")
    try:
        for key, value in dict(env).items():
            if os.fsdecode(key).upper() == "PATH":
                return os.fsdecode(value)
    except (TypeError, ValueError):
        pass
    return None


def _loopback(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).strip("[]")
    if host == "" or host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


class _World:
    """What the running test may do and what it did, in one object.

    The audit hook cannot be removed once installed, so it is installed once
    and reads this object; the pytest hooks below move it from test to test.
    """

    def __init__(self) -> None:
        self.test: Optional[str] = None
        self.allowed = False
        self.touched: List[str] = []
        self.children = 0
        #: id(fixturedef) -> what its setup touched. The definitions are kept
        #: alive in `_defs`, so an id cannot be reused by another object.
        self.fixtures: Dict[int, List[str]] = {}
        self._defs: list = []
        #: The session's temporary directory, real path: where a test's own
        #: files live, stand-ins included.
        self.scratch: Optional[str] = None
        #: What `enter` took out of the environment, for `leave` to put back.
        self.withheld: Optional[Dict[str, str]] = None

    def stand_in(self, first: str, env=None) -> bool:
        """A script the session wrote into its own temporary directory."""
        if not self.scratch or not first:
            return False
        found = (first if os.path.dirname(first)
                 else shutil.which(first, path=_search_path(env)))
        if not found:
            return False
        path = os.path.realpath(found)
        try:
            if os.path.commonpath([path, self.scratch]) != self.scratch:
                return False
            with open(path, "rb") as f:
                return f.read(2) == b"#!"
        except (OSError, ValueError):
            return False

    def enter(self, item) -> None:
        self.test = item.nodeid
        self.allowed = item.get_closest_marker("e2e") is not None
        self.touched = []
        self.children = 0
        self.withheld = None if self.allowed else _withhold()

    def leave(self) -> None:
        self.test = None
        self.allowed = False
        if self.withheld is not None:
            _give_back(self.withheld)
            self.withheld = None

    def touch(self, what: str) -> None:
        self.touched.append(what)
        if not self.allowed:
            where = ("%s is not marked e2e" % self.test if self.test
                     else "nothing outside a test may")
            raise WorldRefused(
                "%s, and tried to %s. A test that needs the network, the real "
                "engine or a display says so with @pytest.mark.e2e; one that "
                "does not must not reach them (invisible_core.testing.world)." % (where, what))

    def hook(self, event: str, args: tuple) -> None:
        if event not in _WATCHED:
            return
        if event == ENGINE_EVENT:
            if not self.stand_in(os.fsdecode(args[0])):
                self.touch("start the engine (%s)" % (args[0],))
        elif event in _SPAWN_EVENTS or event == _WIN32_SPAWN:
            first, env = _command(event, args)
            program = _program(first)
            named = program in _ENGINE_PROGRAMS or program in _DISPLAY_PROGRAMS
            if named and not self.stand_in(first, env):
                self.touch(("start the engine (%s)" if program in _ENGINE_PROGRAMS
                            else "start a display (%s)") % program)
            elif event != _WIN32_SPAWN:
                self.children += 1
        elif event in _ADDRESS_EVENTS:
            address = args[1]
            if isinstance(address, tuple) and address and not _loopback(address[0]):
                self.touch("reach %s over the network" % (address[0],))
        else:  # a name lookup
            if not _loopback(args[0]):
                self.touch("look up %s on the network" % (args[0],))

    def used_a_worldly_fixture(self, item) -> bool:
        info = getattr(item, "_fixtureinfo", None)
        defs = getattr(info, "name2fixturedefs", {}) or {}
        for name in getattr(item, "fixturenames", ()):
            chain = defs.get(name)
            if chain and self.fixtures.get(id(chain[-1])):
                return True
        return False


def _withhold() -> Dict[str, str]:
    """Take the library's variables out of the environment; return their values."""
    return {name: os.environ.pop(name) for name in environment.READ
            if name in os.environ}


def _give_back(withheld: Dict[str, str]) -> None:
    """The environment as it was: the values taken, and none of these that a
    test set without putting back."""
    for name in environment.READ:
        if name in withheld:
            os.environ[name] = withheld[name]
        else:
            os.environ.pop(name, None)


_WORLD = _World()
_INSTALLED = False
#: Node ids of the e2e tests that passed touching nothing, gathered where the
#: reports arrive: the controller under xdist, the only process otherwise.
_MISMARKED: List[str] = []


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--world-report", action="store_true", default=False,
        help="fail the session when a test marked e2e passed without reaching "
             "the network, the engine or a display (invisible_core.testing.world)")


def pytest_configure(config) -> None:
    global _INSTALLED
    if not _INSTALLED:
        sys.addaudithook(_WORLD.hook)
        _INSTALLED = True


def pytest_sessionstart(session) -> None:
    factory = getattr(session.config, "_tmp_path_factory", None)
    if factory is not None:
        _WORLD.scratch = os.path.realpath(factory.getbasetemp())


@pytest.hookimpl(hookwrapper=True)
def pytest_collection(session):
    withheld = _withhold()
    try:
        yield
    finally:
        _give_back(withheld)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _WORLD.enter(item)
    try:
        yield
    finally:
        _WORLD.leave()


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    before = len(_WORLD.touched)
    try:
        yield
    finally:
        new = _WORLD.touched[before:]
        if new:
            _WORLD.fixtures[id(fixturedef)] = list(new)
            _WORLD._defs.append(fixturedef)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not _WORLD.allowed and _WORLD.touched and report.passed:
        # The hook raised; a test that caught it must not pass anyway.
        report.outcome = "failed"
        report.longrepr = "invisible_core.testing.world refused: " + "; ".join(_WORLD.touched)
    if (report.when == "call" and report.passed and _WORLD.allowed
            and not _WORLD.touched and not _WORLD.children
            and not _WORLD.used_a_worldly_fixture(item)):
        report.user_properties.append(UNTOUCHED)


def pytest_runtest_logreport(report) -> None:
    if report.when == "call" and report.passed and UNTOUCHED in report.user_properties:
        _MISMARKED.append(report.nodeid)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    if not config.getoption("--world-report") or hasattr(config, "workerinput"):
        return
    if _MISMARKED:
        terminalreporter.section("invisible_core.testing.world: marked e2e, touched nothing")
        for nodeid in _MISMARKED:
            terminalreporter.line(nodeid)
        terminalreporter.line(
            "%d test(s) marked e2e passed without the network, the engine or a "
            "display: they belong in the default selection." % len(_MISMARKED))


def pytest_sessionfinish(session, exitstatus) -> None:
    config = session.config
    if (config.getoption("--world-report") and not hasattr(config, "workerinput")
            and _MISMARKED and session.exitstatus == pytest.ExitCode.OK):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
