"""`invisible_core.testing.world`: a test reaches outside this process only when
it is marked e2e, and an e2e test that never does is reported.

Each case below runs a real pytest session in a temporary directory, with the
plugin loaded the way a repository loads it (its root conftest), and reads the
verdict back. A case that cannot fail is not a check, so every one of them is a
known-bad input that has to be caught, beside the known-good one it must leave
alone.

The engine is never started: every executable these cases name is a file that
does not exist, so a spawn that got past the guard fails on the missing file
instead of opening a browser on the machine that runs the suite ([B345]: on
2026-10-17 a guard that watched `subprocess.Popen` let about 400 browsers
start, because on Windows the core spawns through `CreateProcessW` and ctypes).
The spawns themselves are NOT replaced: the event is raised inside them, so a
replaced spawn would declare nothing and prove nothing.
"""
from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "invisible_core"

CONFTEST = 'pytest_plugins = ["invisible_core.testing.world"]\n'
INI = "[pytest]\nmarkers =\n    e2e: needs the world\naddopts = -p no:cacheprovider\n"



def _session(tmp_path: pathlib.Path, tests: str, *args: str,
             autoload: bool = False) -> subprocess.CompletedProcess:
    (tmp_path / "pytest.ini").write_bytes(INI.encode())
    (tmp_path / "conftest.py").write_bytes(CONFTEST.encode())
    (tmp_path / "test_inner.py").write_bytes(textwrap.dedent(tests).encode())
    env = dict(os.environ)
    if not autoload:
        # Only the plugin under test: an installed plugin must not decide a case.
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-rA", *args],
                          cwd=tmp_path, env=env, capture_output=True, text=True,
                          timeout=300)


def _out(r: subprocess.CompletedProcess) -> str:
    return r.stdout + r.stderr


def _reported(out: str) -> list:
    """The node ids in the plugin's own section, and nothing after it."""
    section = out.split("marked e2e, touched nothing", 1)[1]
    lines = section.splitlines()[1:]
    return [line.strip() for line in lines[:next(
        i for i, line in enumerate(lines) if "marked e2e passed without" in line)]]


# --- the default selection: refused ------------------------------------------

def test_a_default_test_cannot_start_the_engine(tmp_path):
    r = _session(tmp_path, '''
        from invisible_core.juggler import connection

        def test_launches(tmp_path):
            connection.launch(str(tmp_path / "missing" / "firefox.exe"), str(tmp_path))
    ''')
    assert r.returncode == 1, _out(r)
    assert "WorldRefused" in _out(r) and "start the engine" in _out(r), _out(r)
    for spawned in ("FileNotFoundError", "CreateProcessW failed"):
        assert spawned not in _out(r), "the launch got past the guard:\n" + _out(r)


def test_a_default_test_cannot_spawn_a_program_named_like_the_engine(tmp_path):
    r = _session(tmp_path, '''
        import subprocess

        def test_spawns(tmp_path):
            subprocess.Popen([str(tmp_path / "firefox.exe"), "-version"])
    ''')
    assert r.returncode == 1, _out(r)
    assert "WorldRefused" in _out(r), _out(r)
    assert "FileNotFoundError" not in _out(r), "the spawn was attempted:\n" + _out(r)


def test_a_default_test_cannot_start_a_display_server(tmp_path):
    """Xvfb is a process: Python's own `subprocess.Popen` event names it."""
    r = _session(tmp_path, '''
        import subprocess

        def test_display(tmp_path):
            subprocess.Popen([str(tmp_path / "Xvfb"), ":99"])
    ''')
    assert r.returncode == 1, _out(r)
    assert "WorldRefused" in _out(r) and "start a display" in _out(r), _out(r)
    assert "FileNotFoundError" not in _out(r), "the spawn was attempted:\n" + _out(r)


@pytest.mark.skipif(sys.platform != "win32", reason="_winapi is Windows")
def test_a_default_test_cannot_reach_create_process_around_subprocess(tmp_path):
    """`_winapi.CreateProcess` called directly raises only its own event, not
    `subprocess.Popen`'s ([B345], 1c's review of PR #160)."""
    r = _session(tmp_path, '''
        import _winapi

        def test_spawns(tmp_path):
            exe = str(tmp_path / "missing" / "firefox.exe")
            _winapi.CreateProcess(exe, '"%s" -version' % exe, None, None,
                                  False, 0, None, None, None)
    ''')
    assert r.returncode == 1, _out(r)
    assert "WorldRefused" in _out(r) and "start the engine" in _out(r), _out(r)
    assert "FileNotFoundError" not in _out(r), "the spawn was attempted:\n" + _out(r)


#: A script that borrows the display's name, as tests/test_headless.py's
#: `fake_xvfb` does: put first on PATH, spawned by its bare name.
_STAND_IN = '''
    import os
    import subprocess

    def _spawn(where, content):
        exe = where / "Xvfb"
        exe.write_bytes(content)
        exe.chmod(0o755)
        os.environ["PATH"] = str(where) + os.pathsep + os.environ["PATH"]
        try:
            # By its bare name, as fake_xvfb does; Windows resolves none
            # without an extension, so there the path.
            subprocess.Popen([str(exe) if os.name == "nt" else "Xvfb", ":99"]).wait()
        except OSError:
            pass    # Windows does not run a script: the guard is what is judged
'''


def test_a_stand_in_the_test_wrote_is_not_a_display(tmp_path):
    r = _session(tmp_path, _STAND_IN + '''
    def test_display(tmp_path):
        _spawn(tmp_path, b"#!/bin/sh\\nexit 0\\n")
    ''')
    assert r.returncode == 0, _out(r)


@pytest.mark.parametrize("why", ["outside the session's temporary directory",
                                 "not a script"])
def test_only_a_script_in_the_sessions_own_directory_is_a_stand_in(tmp_path, why):
    """A compiled Xvfb extracted into the temporary directory is the real one,
    and a script elsewhere on the machine is not the test's own."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    where, content = ((repr(str(outside)), b"#!/bin/sh\nexit 0\n")
                      if why.startswith("outside") else ("tmp_path", b"\x7fELF"))
    r = _session(tmp_path, _STAND_IN + '''
    import pathlib

    def test_display(tmp_path):
        _spawn(pathlib.Path(%s), %r)
    ''' % (where, content))
    assert r.returncode == 1, _out(r)
    assert "WorldRefused" in _out(r) and "start a display" in _out(r), _out(r)


@pytest.mark.skipif(sys.platform != "win32", reason="a Win32 desktop")
def test_a_hidden_desktop_is_not_the_world(tmp_path):
    """`CreateDesktopW` starts no process and reaches no network: an object of
    the logon session, released at stop. The tests that make one stay in the
    default selection."""
    r = _session(tmp_path, '''
        from invisible_core import _headless

        def test_desktop():
            desktop = _headless._WindowsVirtualDesktop()
            desktop.start()
            desktop.stop()
    ''')
    assert r.returncode == 0, _out(r)


def test_a_default_test_cannot_reach_the_network_but_loopback_is_open(tmp_path):
    r = _session(tmp_path, '''
        import socket

        def test_off_loopback():
            # TEST-NET-3: never routed, so only the guard can answer quickly.
            socket.create_connection(("203.0.113.1", 9), timeout=5)

        def test_a_name_lookup():
            socket.getaddrinfo("example.invalid", 80)

        def test_loopback():
            server = socket.socket()
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            client = socket.create_connection(server.getsockname(), timeout=5)
            client.close()
            server.close()
    ''')
    out = _out(r)
    assert "PASSED test_inner.py::test_loopback" in out, out
    assert "FAILED test_inner.py::test_off_loopback" in out, out
    assert "FAILED test_inner.py::test_a_name_lookup" in out, out
    assert out.count("WorldRefused") >= 2 and "timed out" not in out, out


def test_a_default_test_that_swallows_the_refusal_still_fails(tmp_path):
    r = _session(tmp_path, '''
        import socket

        def test_swallows():
            try:
                socket.create_connection(("203.0.113.1", 9), timeout=5)
            except Exception:
                pass
    ''')
    assert r.returncode == 1, _out(r)
    assert "invisible_core.testing.world refused" in _out(r), _out(r)


def test_nothing_outside_a_test_reaches_the_world(tmp_path):
    r = _session(tmp_path, '''
        import socket
        socket.getaddrinfo("example.invalid", 80)

        def test_never_collected():
            pass
    ''')
    assert r.returncode != 0, _out(r)
    assert "WorldRefused" in _out(r) and "outside a test" in _out(r), _out(r)


# --- e2e: allowed, and judged with --world-report ----------------------------

E2E = '''
    import subprocess, sys
    import pytest
    from invisible_core.juggler import connection

    @pytest.fixture(scope="module")
    def browser(tmp_path_factory):
        try:
            where = tmp_path_factory.mktemp("p")
            connection.launch(str(where / "missing" / "firefox.exe"), str(where))
        except OSError:
            pass    # allowed: it reached the spawn, which found no such file
        return "launched"

    @pytest.mark.e2e
    def test_touches_nothing():
        assert 1 + 1 == 2

    @pytest.mark.e2e
    def test_first_on_the_browser(browser):
        assert browser

    @pytest.mark.e2e
    def test_second_on_the_same_browser(browser):
        assert browser    # the launch happened in the first test's setup

    @pytest.mark.e2e
    def test_starts_a_child():
        subprocess.run([sys.executable, "-c", "pass"], check=True)
'''


def test_an_e2e_test_that_touches_nothing_is_reported(tmp_path):
    r = _session(tmp_path, E2E, "--world-report")
    out = _out(r)
    assert r.returncode == 1, out
    assert _reported(out) == ["test_inner.py::test_touches_nothing"], out
    assert "1 test(s) marked e2e passed without" in out, out


def test_the_report_is_asked_for(tmp_path):
    r = _session(tmp_path, E2E)
    assert r.returncode == 0, _out(r)
    assert "touched nothing" not in _out(r), _out(r)


def test_the_report_gathers_what_the_workers_saw(tmp_path):
    pytest.importorskip("xdist")
    r = _session(tmp_path, E2E, "--world-report", "-p", "xdist", "-n", "2")
    out = _out(r)
    assert r.returncode == 1, out
    assert _reported(out) == ["test_inner.py::test_touches_nothing"], out


# --- the core: the engine and the desktop start only where they say so ------

def _functions(path: pathlib.Path):
    """Module-level functions and methods, once each, by qualified name. A
    nested function is part of the one it is nested in."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield "%s.%s" % (node.name, item.name), item


def _mentions(node, word: str) -> bool:
    """`word` as a name or an attribute; a trailing `*` matches a prefix."""
    if word.endswith("*"):
        same = lambda name: name.startswith(word[:-1])  # noqa: E731
    else:
        same = lambda name: name == word  # noqa: E731
    return any((isinstance(n, ast.Name) and same(n.id))
               or (isinstance(n, ast.Attribute) and same(n.attr))
               for n in ast.walk(node))


def _audits(node, event: str) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "audit" and n.args
               and isinstance(n.args[0], ast.Name) and n.args[0].id == event
               for n in ast.walk(node))


def _core_functions() -> dict:
    """Every function of the core outside `testing/`, as `module::name`: two
    modules may each have a `launch`."""
    out = {}
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC)
        if "testing" in rel.parts:
            continue
        for name, fn in _functions(path):
            out["%s::%s" % (rel.as_posix(), name)] = fn
    return out


#: Each way the core starts the engine, and the one function allowed to reach
#: it. `CreateProcess*` is every Win32 spelling: `W`, `A`, `AsUserW`,
#: `WithLogonW`, and `_winapi.CreateProcess` itself.
OWNERS = {
    "_spawn_windows": "juggler/connection.py::launch",
    "_spawn_posix": "juggler/connection.py::launch",
    "CreateProcess*": "juggler/connection.py::_spawn_windows",
}


def _audit_line(fn) -> int:
    return min(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == "audit"
               and n.args and isinstance(n.args[0], ast.Name)
               and n.args[0].id == "LAUNCH_EVENT")


def _first_call_line(fn, name: str) -> int:
    return min(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
               and ((isinstance(n.func, ast.Attribute) and n.func.attr == name)
                    or (isinstance(n.func, ast.Name) and n.func.id == name)))


def test_the_core_starts_the_engine_only_where_it_says_so():
    """A spawn outside `launch`'s two would be invisible to the plugin:
    `CreateProcessW` through ctypes raises no event of its own. So each of the
    two raises the core's event, before the call that creates the process, and
    no other function names any `CreateProcess`."""
    functions = _core_functions()
    for word, owner in OWNERS.items():
        reached_by = {name for name, fn in functions.items()
                      if name.split("::")[1] != word and _mentions(fn, word)}
        assert reached_by == {owner}, (word, sorted(reached_by))
    windows = functions["juggler/connection.py::_spawn_windows"]
    posix = functions["juggler/connection.py::_spawn_posix"]
    assert _audit_line(windows) < _first_call_line(windows, "CreateProcessW")
    assert _audit_line(posix) < _first_call_line(posix, "Popen")
