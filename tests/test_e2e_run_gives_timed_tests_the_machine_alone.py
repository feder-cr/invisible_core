"""The clients' e2e run gives the tests marked `wall_clock` the machine alone:
after the last parallel test has ended, one at a time, outside xdist.

Measured 2026-10-15/16 on the wrapper's device-enumeration test, which bounds
how long the engine's first enumeration takes: red at 98% of a run with four
browsers, green again on a rerun of the same SHA, three times over. Its bound
was right; the parent process's main thread was late under four browsers by an
amount no margin covers. So the fix is where the test runs, and this file
holds the run to it on a suite of three files and no browser, through the same
`main` the clients' `scripts/run_e2e.py` call.

Known bad, each tried against this file: one pass with `-m e2e` (the run
before this) puts the timed test on a worker beside the others, and the
ordering assertion goes red; dropping the second pass's code from the verdict
lets a red timed test through as green.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from invisible_core.testing.e2e import WALL_CLOCK, passes, verdict

# The run needs these plugins, as the clients' runs do.
import pytest_rerunfailures  # noqa: F401
import pytest_timeout  # noqa: F401
import xdist  # noqa: F401


# -- the plan: which passes, and what the run answers ------------------------

@pytest.mark.unit
@pytest.mark.parametrize("procs", ["4", "8", "auto"])
def test_several_browsers_run_the_timed_tests_last_and_alone(procs):
    assert passes(procs) == [(procs, "e2e and not wall_clock"),
                             ("0", "e2e and wall_clock")]


@pytest.mark.unit
@pytest.mark.parametrize("procs", ["0", "1"])
def test_one_browser_is_one_pass_the_serial_path_it_always_was(procs):
    assert passes(procs) == [(procs, "e2e")]


@pytest.mark.unit
@pytest.mark.parametrize("codes, expected", [
    ([0, 0], 0),
    ([0, 5], 0),     # a client with no timed test: the second pass is empty
    ([5, 0], 0),     # a -k that picked only timed tests
    ([1, 0], 1),
    ([0, 1], 1),     # a red timed test is a red run
    ([1, 1], 1),
    ([5, 5], 5),     # nothing ran at all: not green
    ([2], 2),        # interrupted: the run stopped there
    ([0], 0),
])
def test_the_run_answers_for_every_pass_that_ran(codes, expected):
    assert verdict(codes) == expected


@pytest.mark.unit
def test_the_marker_is_declared_where_the_run_selects_by_it():
    """`-m` with a word nobody declared selects nothing and says nothing: the
    timed tests would silently not run at all."""
    import tomllib
    from pathlib import Path

    with (Path(__file__).resolve().parents[1] / "pyproject.toml").open("rb") as fh:
        cfg = tomllib.load(fh)["tool"]["pytest"]["ini_options"]
    assert WALL_CLOCK in {m.split(":")[0].strip() for m in cfg["markers"]}


# -- the run itself, on a suite with no browser ------------------------------

_RECORD = """
    import json, os, time
    from pathlib import Path


    def record(name, body=lambda: time.sleep(1.5)):
        began = time.time()
        body()
        Path(__file__).with_name(name + ".json").write_text(json.dumps({
            "began": began, "ended": time.time(),
            "worker": os.environ.get("PYTEST_XDIST_WORKER")}))
"""

_INI = """
    [pytest]
    markers =
        e2e: opens the browser
        wall_clock: its verdict reads a wall-clock duration
"""

_PARALLEL = """
    import pytest
    from _record import record

    pytestmark = pytest.mark.e2e


    def test_{name}():
        record("{name}")
"""

_TIMED = """
    import pytest
    from _record import record

    pytestmark = [pytest.mark.e2e, pytest.mark.wall_clock]


    def test_timed():
        record("timed", lambda: None)
        assert {verdict}
"""

_ENTRY = (
    "import sys\n"
    "from pathlib import Path\n"
    "from invisible_core.testing.e2e import main\n"
    "sys.exit(main(sys.argv[1:-1], repo=Path(sys.argv[-1]), deadline_s=60))\n")


def _run(tmp_path, *, timed: str | None):
    files = {"pytest.ini": _INI, "_record.py": _RECORD,
             "test_one.py": _PARALLEL.replace("{name}", "one"),
             "test_two.py": _PARALLEL.replace("{name}", "two")}
    if timed is not None:
        files["test_timed.py"] = _TIMED.replace("{verdict}", timed)
    for name, body in files.items():
        (tmp_path / name).write_bytes(textwrap.dedent(body).lstrip().encode())
    binary = tmp_path / "firefox"
    binary.write_bytes(b"")
    # ⛔ NOT THIS SUITE'S OWN xdist VARIABLES. Under `pytest -n 4` this test
    # runs inside a worker, and `PYTEST_XDIST_WORKER` inherited by the run would
    # make its serial pass look like a worker: measured, red on the parallel
    # suite and green alone. A client starts the run from a plain shell.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_XDIST")}
    proc = subprocess.run(
        [sys.executable, "-c", _ENTRY, str(binary), "-n", "2", str(tmp_path)],
        capture_output=True, timeout=180, env=env)
    log = (proc.stdout + proc.stderr).decode(errors="replace")
    seen = {p.stem: json.loads(p.read_text())
            for p in tmp_path.glob("*.json")}
    return proc.returncode, log, seen


@pytest.mark.integration
def test_the_timed_test_starts_after_the_last_parallel_one_has_ended(tmp_path):
    rc, log, seen = _run(tmp_path, timed="True")
    assert rc == 0, log[-3000:]
    assert set(seen) == {"one", "two", "timed"}, log[-3000:]
    parallel = [seen["one"], seen["two"]]
    assert all(t["worker"] for t in parallel), (
        "the precondition: the other tests ran on xdist workers", seen)
    assert seen["timed"]["worker"] is None, (
        "the timed test ran on a worker, beside the others", seen)
    assert seen["timed"]["began"] >= max(t["ended"] for t in parallel), seen


@pytest.mark.integration
def test_a_red_timed_test_is_a_red_run(tmp_path):
    rc, log, seen = _run(tmp_path, timed="False")
    assert "timed" in seen, log[-3000:]
    assert rc == 1, log[-3000:]


@pytest.mark.integration
def test_a_client_with_no_timed_test_is_not_red_for_it(tmp_path):
    rc, log, _seen = _run(tmp_path, timed=None)
    assert rc == 0, log[-3000:]
