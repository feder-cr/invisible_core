"""The FULL e2e run of a client of this core (every test that opens the
browser) against one binary.

ONE COPY FOR THE THREE CLIENTS. `invisible_playwright`, `invisible_selenium`
and `invisible_puppeteer` each carried this run as a byte-identical
`scripts/run_e2e.py` until 44.39.0, so a defect in it was three defects and a
fix was three fixes. Each keeps `scripts/run_e2e.py` as the entry its
workflows and documents call, and that entry is two lines that land here.

Every `@pytest.mark.e2e` test is excluded from the default `pytest` run
(`addopts = -m 'not slow and not e2e'`) because they need a real Firefox binary
and a display, and they skip themselves when no binary is available. That makes
them easy to forget. This is the run that takes them all, deliberately, against
a chosen binary: the mandatory pre-release e2e gate of a firefox-N.

Flake-resilience: under full-suite load a couple of interaction tests (dblclick,
hover/mouseenter) can flake even though they pass 3/3 in isolation, so failures
are rerun up to twice on the known transient signatures. A genuinely broken
binary fails all attempts. The webrtc e2e fake a TCP-only SOCKS locally (no
proxy/secrets), so the whole suite is offline.

The run opens FOUR browsers at once by default. `--dist loadfile` is not
negotiable and the reason is on the line that sets it.

Usage, through a client's entry:
    python scripts/run_e2e.py <firefox-binary>
    python scripts/run_e2e.py            # uses $INVPW_BINARY_PATH
    python scripts/run_e2e.py <binary> -n 8    # eight browsers at once
    python scripts/run_e2e.py <binary> -n 1    # the serial path, no xdist
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

__all__ = ["DEADLINE_S", "command", "main"]

_RERUN_SIGNATURES = "Timeout|context was destroyed|was detached|not visible|because of a navigation|TargetClosed"

#: Browsers opened at once. Four was asked for and four is what the default is;
#: it is not a measured optimum and there is no reason to believe one number
#: fits every machine, so `-n` moves it and `-n 1` puts the run back exactly on
#: the serial path this run had before parallelism existed.
_DEFAULT_PROCS = "4"

#: The per-test deadline. 420 s is 4.5x the slowest legitimate test measured
#: on CI (test_webgl_readpixels_no_masking_signature in invisible_playwright,
#: 94.4 s; the second slowest is 11.4 s and the whole suite was 318 s), and 1/6
#: of the job budget.
DEADLINE_S = 420


def _stack_dump_after(deadline_s: int) -> int:
    """When the stacks of a test that is still running are written out: a
    little before its deadline, so they are on the log before it is killed."""
    return deadline_s - max(1, deadline_s // 20)


def _take_procs(argv: list[str]) -> tuple[str, list[str]]:
    """Pull `-n/--procs` out of the arguments, leaving the rest for pytest.

    Hand-parsed rather than argparse because everything this run does not
    recognise is forwarded to pytest verbatim, and argparse would have to be
    taught every pytest flag in order to not eat one.

    The value is kept as a STRING and handed to xdist unread, so `auto` and any
    future spelling xdist accepts keep working without this function learning
    about them. Only `0` and `1` are interpreted here, and they mean the same
    thing: do not load xdist at all. That is not the same as `-n 1`, which
    still runs the tests inside a worker subprocess - a difference that matters
    the day this run is used to compare a parallel run against a serial one,
    because then the serial arm must be the ORIGINAL path and not a
    one-worker variant of the new one.
    """
    procs, rest, i = _DEFAULT_PROCS, [], 0
    while i < len(argv):
        if argv[i] in ("-n", "--procs"):
            if i + 1 >= len(argv):
                print("run_e2e.py: -n needs a value (1 = serial)", file=sys.stderr)
                raise SystemExit(2)
            procs, i = argv[i + 1], i + 2
            continue
        rest.append(argv[i])
        i += 1
    return procs, rest


def command(procs: str, extra: list[str], *,
            deadline_s: int = DEADLINE_S) -> list[str]:
    """The pytest command line of the run."""
    # ⛔ `--dist loadfile` IS NOT A TUNING CHOICE, IT IS A CORRECTNESS ONE, and
    # xdist's default (`--dist load`, which hands out one test at a time) breaks
    # the wrapper's suite in two measured ways.
    #
    # 1. MODULE-SCOPED BROWSERS. `test_fingerprint_consistency.py` is 65 of the
    #    193 e2e tests and opens ONE browser for all of them, from a
    #    module-scoped fixture. Scattered test-by-test across four workers, each
    #    worker that receives any of those tests builds its own copy of that
    #    fixture: four browsers where the file wants one, and the identity those
    #    65 tests exist to prove is no longer being read off a single session.
    #
    # 2. TESTS THAT DEPEND ON AN EARLIER TEST IN THEIR OWN FILE.
    #    `test_binary_executes_after_fetch` fails 2 out of 2 in isolation and
    #    passes 9 out of 9 when its module runs whole, because a prior test in
    #    that file populates the venv it uses. Measured 2026-08-29, and it very
    #    nearly got recorded as a deterministic failure of the product.
    #
    # `loadfile` keeps every file whole on one worker, so both properties
    # survive. The price is the ceiling: the longest single FILE is now the
    # floor of the whole run, and with 65 of 193 tests in one file this run
    # cannot go faster than that file no matter how many workers are asked for.
    #
    # ⛔ AND A WORKER THAT DIES ENDS THE RUN (`--max-worker-restart 0`), because
    # under `loadfile` a replaced worker WAITS FOREVER. When a worker dies,
    # xdist's scope scheduler puts back in the queue EVERY file that worker was
    # ever given, the finished ones too, and hands the replacement one unit:
    # the first, which is a file already run. The replacement is sent nothing
    # to run, reports nothing, and is never sent anything else. Measured
    # 2026-10-13: the full run on firefox-44 lost a worker at its deadline and
    # then sat for 24 minutes with the two files still queued, the replacement
    # idle in its command loop; reproduced with three files and no browser,
    # still waiting after two minutes, and with this flag finished in 15 s
    # naming the test that killed its worker. A dead worker is a red run
    # anyway; what this buys is that the run SAYS so.
    parallel = ([] if procs in ("0", "1") else
                ["-n", procs, "--dist", "loadfile", "--max-worker-restart", "0"])
    return [
        sys.executable, "-m", "pytest",
        "-m", "e2e",
        "-o", "addopts=",            # override the default 'not e2e' deselection
        "--reruns", "2", "--reruns-delay", "1",
        "--only-rerun", _RERUN_SIGNATURES,
        # A DEADLINE is not a flake, and retrying one multiplies it by three.
        # `Timeout` in the signatures above was written for Playwright's
        # TimeoutError, but it also matches `subprocess.TimeoutExpired` -
        # measured, 2 tests produced 4 reruns - and the release/upgrade e2e
        # spend their time in `pip install` calls whose timeouts run to 300s
        # each. Retried twice that is 900s for one test, and the two files sum
        # to 10050s of timeout budget against a 2400s job. That is how the job
        # was killed at 40 minutes twice with nothing to show for it.
        "--rerun-except", "TimeoutExpired",   # a subprocess deadline
        "--rerun-except", "Timeout >",        # pytest-timeout's own deadline
        # And a per-test deadline, so a hang FAILS WITH A NAME instead of eating
        # the job. METHOD=thread, not the signal default: measured 2026-08-13,
        # the suite wedged in `test_hover_triggers_mouseenter` and the 420s
        # signal deadline came and went with NOTHING at 19.3 minutes, 21
        # minutes, 40. SIGALRM is delivered to the main thread and Python runs
        # the handler at the next bytecode boundary; Playwright's sync API is
        # blocked in a greenlet waiting on the driver socket, so that boundary
        # never arrives. A watchdog THREAD does not need the hung thread to
        # cooperate: it ends the process.
        "--timeout", str(deadline_s),
        "--timeout-method", "thread",
        # ⛔ THE STACKS COME FROM HERE, NOT FROM THE DEADLINE ABOVE. That
        # watchdog writes them to the terminal of the process it kills, and an
        # xdist worker's terminal is its stdout, which execnet points at the
        # null device: under `-n` every hang died without a stack. Measured
        # 2026-10-13 on a full run with stderr in the log: a worker killed at
        # its deadline, no stack. pytest's own faulthandler writes to the
        # stderr the worker inherits, so this dump reaches the log of the run,
        # parallel or not. Measured the same day with a test asleep in a named
        # function under `-n`: this dump named the function, the deadline's
        # banner never appeared.
        "-o", "faulthandler_timeout=%d" % _stack_dump_after(deadline_s),
        "-p", "no:cacheprovider",
        # -v, not -q, and the reason is a hang we could not name twice.
        # Under -q pytest emits one character per test and the line only
        # reaches the log when it is full, so a run that dies mid-line says
        # nothing about where it died. Measured 2026-08-12 on two runs of the
        # SAME commit: both printed the identical `.s....[ 50%]` line at 78
        # seconds, one then finished in 4:47 and the other was killed by the
        # 40-minute timeout with no second line - so the hang was somewhere in
        # tests 73-141 and that is as close as the log could get. Same symptom
        # on 2026-08-04. PYTHONUNBUFFERED was already set and is not the
        # missing piece: the output does stream, the GRANULARITY was wrong.
        # One line per test costs 141 lines and names the last one that ran.
        "-v", "--tb=short",
    ] + parallel + extra


def main(argv: list[str], repo: Path, *,
         deadline_s: int = DEADLINE_S) -> int:
    """Run the e2e suite of the client checked out at ``repo``.

    ``argv`` is the entry's own arguments: the binary (or none, for
    $INVPW_BINARY_PATH), `-n`, and anything else, which goes to pytest.
    ``deadline_s`` is for this module's own tests, which cannot wait 420 s for
    a test to die.
    """
    procs, extra = _take_procs(argv)
    binary = extra[0] if extra else os.environ.get("INVPW_BINARY_PATH")
    extra = extra[1:] if extra else []
    if not binary:
        print("usage: run_e2e.py <firefox-binary>  (or set INVPW_BINARY_PATH)", file=sys.stderr)
        return 2
    if not Path(binary).exists():
        print(f"ERROR: binary not found: {binary}", file=sys.stderr)
        return 2

    # An escape hatch in the environment is not neutral here: one e2e ASSERTS
    # the behaviour it disables.
    #
    # `INVISIBLE_CORE_PIN=allow` tells the core to run a mismatched pair instead
    # of repairing it. It is the normal way to drive a locally built engine, so
    # it is often already exported in the shell that starts this run - and it is
    # INHERITED by the venv subprocess that the wrapper's
    # `test_the_mismatch_repairs_itself_at_import_without_a_restart` spawns.
    # That test deliberately breaks an environment and requires importing the
    # wrapper to fix it. With the variable set, the repair correctly declines,
    # the version does not move, and the test fails for a reason that has
    # nothing to do with the binary under test.
    #
    # Measured 2026-08-16: 1 failed / 143 passed in 14 minutes, and the same
    # file re-run with the variable unset gave 4 passed in 2 minutes. Refusing
    # up front costs a second; discovering it costs the whole run plus the time
    # spent believing the product was broken.
    #
    # It is stripped, not merely warned about: a warning at the top of a
    # 14-minute run scrolls away long before the failure appears.
    env = {k: v for k, v in os.environ.items() if k != "INVISIBLE_CORE_PIN"}
    if "INVISIBLE_CORE_PIN" in os.environ:
        print("[run_e2e] INVISIBLE_CORE_PIN is set in this shell and has been "
              "STRIPPED for the run: one e2e asserts the pin repair that the "
              "variable disables, and it would fail for that reason alone.",
              file=sys.stderr)
    # One setting drives the whole suite: conftest's firefox_binary fixture and
    # the webrtc e2e both resolve from these.
    env["INVPW_BINARY_PATH"] = binary
    env["STEALTHFOX_E2E_BINARY"] = binary

    cmd = command(procs, extra, deadline_s=deadline_s)
    print(f"[run_e2e] binary={binary}")
    print(f"[run_e2e] browsers at once: "
          f"{'1 (serial)' if procs in ('0', '1') else procs}")
    print(f"[run_e2e] {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=repo, env=env).returncode
