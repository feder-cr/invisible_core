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
  `juggler.connection.launch` before it spawns - on Windows that spawn is
  `CreateProcessW` through ctypes, which no `subprocess` event sees - and any
  spawn of a program named like the engine;
* a display: `invisible_core.display.start`, and any spawn of Xvfb;
* the network: a connection, a datagram or a name lookup that is not loopback.
  A local test server is inside this process's world, so loopback stays open.

A test that is not marked e2e and tries one of them gets `WorldRefused` from the
hook, so the call never happens, and the test is reported failed even if it
caught the exception: the attempt is what is wrong, not the traceback.

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
import sys
from typing import Dict, List, Optional

import pytest

from .._headless import DISPLAY_EVENT
from ..juggler.connection import LAUNCH_EVENT as ENGINE_EVENT

#: Spawned under these names, a program is the engine or a display.
_ENGINE_PROGRAMS = frozenset({"firefox", "firefox.exe", "firefox-bin"})
_DISPLAY_PROGRAMS = frozenset({"xvfb", "xvfb-run"})
_SPAWN_EVENTS = frozenset({"subprocess.Popen", "os.posix_spawn", "os.spawn",
                           "os.exec", "os.system", "os.startfile"})
_ADDRESS_EVENTS = frozenset({"socket.connect", "socket.sendto"})
_NAME_EVENTS = frozenset({"socket.getaddrinfo", "socket.gethostbyname",
                          "socket.gethostbyaddr"})
_WATCHED = (frozenset({ENGINE_EVENT, DISPLAY_EVENT}) | _SPAWN_EVENTS
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


def _program(event: str, args: tuple) -> str:
    """The basename, lowercased, of what a spawn event starts."""
    if event == "subprocess.Popen":
        executable, argv = args[0], args[1]
        if executable:
            first = os.fsdecode(executable)
        elif isinstance(argv, (str, bytes)):
            first = _first_word(argv)
        else:
            argv = list(argv or [])
            first = os.fsdecode(argv[0]) if argv else ""
    elif event == "os.system":
        first = _first_word(args[0])
    elif event == "os.spawn":
        first = os.fsdecode(args[1])
    else:  # os.posix_spawn, os.exec, os.startfile: the path comes first
        first = os.fsdecode(args[0])
    return os.path.basename(first.replace("\\", "/")).lower()


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

    def enter(self, item) -> None:
        self.test = item.nodeid
        self.allowed = item.get_closest_marker("e2e") is not None
        self.touched = []
        self.children = 0

    def leave(self) -> None:
        self.test = None
        self.allowed = False

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
            self.touch("start the engine (%s)" % (args[0],))
        elif event == DISPLAY_EVENT:
            self.touch("start a display (%s)" % (args[0],))
        elif event in _SPAWN_EVENTS:
            program = _program(event, args)
            if program in _ENGINE_PROGRAMS:
                self.touch("start the engine (%s)" % program)
            elif program in _DISPLAY_PROGRAMS:
                self.touch("start a display (%s)" % program)
            else:
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
