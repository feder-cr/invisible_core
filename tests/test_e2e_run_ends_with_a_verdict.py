"""The clients' e2e run ends on its own when a test hangs, red, naming the test
and the function it hung in.

Measured 2026-10-13 on the full e2e run of firefox-44 (four workers): a test
hung, pytest-timeout ended its worker at the deadline, and then

  * the run waited for 24 minutes with two files still queued, because under
    `--dist loadfile` xdist hands a replacement worker a file already run and
    nothing after it;
  * the log had no stack, because the deadline's watchdog writes it to the
    worker's stdout, which execnet points at the null device.

Both are asserted here on a suite of three files and no browser, through the
same `main` the clients' `scripts/run_e2e.py` call. Without
`--max-worker-restart 0` this run never ends (measured: still waiting after two
minutes); without `faulthandler_timeout` it ends without the stack.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time

import psutil
import pytest

# The run needs these three plugins, as the clients' runs do; a missing one is
# a broken dev environment, not a reason to skip.
import pytest_rerunfailures  # noqa: F401
import pytest_timeout  # noqa: F401
import xdist  # noqa: F401

pytestmark = pytest.mark.integration

#: How long the whole run may take before it counts as never ending. The run
#: itself takes about 15 s: two workers starting, a 6 s deadline, a summary.
_RUN_BUDGET_S = 120

_FILES = {
    "pytest.ini": """
        [pytest]
        markers =
            e2e: opens the browser
    """,
    "test_hangs.py": """
        import time

        import pytest

        pytestmark = pytest.mark.e2e


        def test_first_is_done():
            pass


        def test_hangs_in_a_named_function():
            def asleep_in_here():
                time.sleep(600)
            asleep_in_here()
    """,
    "test_after_one.py": """
        import pytest

        pytestmark = pytest.mark.e2e


        def test_after_one():
            pass
    """,
    "test_after_two.py": """
        import pytest

        pytestmark = pytest.mark.e2e


        def test_after_two():
            pass
    """,
}

_ENTRY = (
    "import sys\n"
    "from pathlib import Path\n"
    "from invisible_core.testing.e2e import main\n"
    "sys.exit(main(sys.argv[1:-1], repo=Path(sys.argv[-1]), deadline_s=6))\n")


def _run(tmp_path):
    for name, body in _FILES.items():
        (tmp_path / name).write_bytes(textwrap.dedent(body).lstrip().encode())
    binary = tmp_path / "firefox"
    binary.write_bytes(b"")
    proc = subprocess.Popen(
        [sys.executable, "-c", _ENTRY, str(binary), "-n", "2", str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    began = time.monotonic()
    try:
        out, _ = proc.communicate(timeout=_RUN_BUDGET_S)
    except subprocess.TimeoutExpired:
        for child in psutil.Process(proc.pid).children(recursive=True):
            child.kill()
        proc.kill()
        out, _ = proc.communicate()
        pytest.fail("the e2e run never ended after a worker died at its "
                    "deadline:\n" + out.decode(errors="replace")[-3000:])
    return proc.returncode, out.decode(errors="replace"), time.monotonic() - began


def test_a_test_that_hangs_ends_the_run_red_with_its_name_and_its_stack(
        tmp_path):
    rc, log, _took = _run(tmp_path)
    assert rc != 0, log[-3000:]
    assert "test_hangs_in_a_named_function" in log, log[-3000:]
    assert "crashed while running" in log, log[-3000:]
    assert "asleep_in_here" in log, (
        "the run ended but the stack of the hung test is not on its log:\n"
        + log[-3000:])
