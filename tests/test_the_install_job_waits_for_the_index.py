"""The wait of the install job for the index (no network, no browser).

Ledger #250 of 63.66.0: the two jobs "install from index and launch" failed in
``test_user_install_e2e.py::test_the_version_the_index_serves_matches_this_checkout``, which saw 62.65.0 from the index
seconds after the upload, on both runners: the step that waited for the index had asked the JSON endpoint of the version,
which answered, while the install that followed was served an older page (the wrapper's ledger #476 of 0.50.1 failed
the same way; its fix is 299fc9f). The wait now asks the tool that installs: uv (the README's way in, the user path of the
job), after uv is installed, and goes on only after it answered yes several times in a row, because one answer proves that
one request saw the version, not that the next is served by the same node of the index. The script of the step is run
here as it stands in the workflow, in a checkout of version 9.9.9, with a ``uv`` that answers as a lagging index does.

Known-bad input: the step as it was (it asked pip; one answer was enough) - the tests below see pip's answer taken for uv's,
a single yes enough, and a call that hangs wait for it.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "user-install.yml"


def _wait_script() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("- name: wait for the index to serve this version")
    m = re.search(r"python - <<'PY'\n(.*?)\n\s*PY\n", text[start:], re.S)
    assert m, "the wait step has no python script"
    return textwrap.dedent(m.group(1)) + "\n"


def _checkout(tmp_path):
    """A checkout named invisible-core whose derived version is 9.9.9 (the step loads src/invisible_core/_version.py)."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "invisible-core"\ndynamic = ["version"]\n', encoding="utf-8")
    pkg = tmp_path / "src" / "invisible_core"
    pkg.mkdir(parents=True)
    (pkg / "_version.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")


def _run_the_wait(tmp_path, plan, *, seconds=3.0, poll=0.05, call_seconds=None, confirmations=None):
    """Run the wait with a uv that answers call number i as plan[i] says (the last letter repeats): y = resolves it, n = no such version,
    h = does not answer (sleeps 30 s). Returns (completed process, the calls uv got, with their stdin)."""
    _checkout(tmp_path)
    log = tmp_path / "uv-calls.jsonl"
    shim = tmp_path / "uv_shim.py"
    shim.write_text(textwrap.dedent("""
        import json, pathlib, sys, time
        log = pathlib.Path(%r)
        n = len(log.read_text().splitlines()) if log.exists() else 0
        with log.open("a") as f:
            f.write(json.dumps({"args": sys.argv[1:], "stdin": sys.stdin.read()}) + chr(10))
        plan = %r
        what = plan[min(n, len(plan) - 1)]
        if what == "h":
            time.sleep(30)
        if what == "y":
            sys.exit(0)
        sys.stderr.write("  x No solution found when resolving dependencies: invisible-core==9.9.9 was not found in the package registry" + chr(10))
        sys.exit(1)
    """) % (str(log), plan), encoding="utf-8")
    env = dict(os.environ, INDEX_WAIT_UV=json.dumps([sys.executable, str(shim)]), INDEX_WAIT_SECONDS=str(seconds), INDEX_WAIT_POLL=str(poll))
    if call_seconds is not None:
        env["INDEX_WAIT_CALL_SECONDS"] = str(call_seconds)
    if confirmations is not None:
        env["INDEX_WAIT_CONFIRMATIONS"] = str(confirmations)
    try:
        r = subprocess.run([sys.executable, "-c", _wait_script()], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail("the wait did not end within 60 s with INDEX_WAIT_SECONDS=%s: it does not read its deadline" % seconds)
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return r, calls


def test_the_wait_asks_uv_for_the_exact_version_and_changes_nothing(tmp_path):
    r, calls = _run_the_wait(tmp_path, "y")
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls, "uv was never asked (the step asks something else)"
    assert calls[0]["args"][:3] == ["pip", "compile", "-"] and "--no-cache" in calls[0]["args"] and "--no-deps" in calls[0]["args"]
    assert calls[0]["stdin"].strip() == "invisible-core==9.9.9"


def test_one_yes_is_not_enough_three_in_a_row_are(tmp_path):
    r, calls = _run_the_wait(tmp_path, "y")
    assert r.returncode == 0 and len(calls) == 3, "it went on after %d yes: %s" % (len(calls), r.stdout + r.stderr)
    assert "uv resolves invisible-core 9.9.9 from the index, 3 times in a row" in r.stdout


def test_a_no_between_the_yes_starts_the_count_again(tmp_path):
    r, calls = _run_the_wait(tmp_path, "yynyyy")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(calls) == 6, "a stale answer between two good ones must restart the count: %d calls" % len(calls)


def test_the_wait_ends_in_an_error_that_names_the_missing_version_when_the_index_never_has_it(tmp_path):
    r, calls = _run_the_wait(tmp_path, "n", seconds=1.0)
    assert r.returncode == 1, "a forgotten publish must fail the job here: " + r.stdout + r.stderr
    assert "::error::the index does not have invisible-core 9.9.9 yet" in r.stdout
    assert "minutes" in r.stdout and "No solution found" in r.stdout and "0 of 3 confirmations" in r.stdout
    assert len(calls) >= 2


def test_a_version_that_arrives_too_late_for_the_deadline_is_an_error_not_a_pass(tmp_path):
    r, calls = _run_the_wait(tmp_path, "yy" + "n", seconds=1.0)
    assert r.returncode == 1, "two yes then no until the deadline is not three in a row: " + r.stdout + r.stderr


def test_a_call_that_does_not_answer_is_a_no_and_does_not_stop_the_wait(tmp_path):
    r, calls = _run_the_wait(tmp_path, "hyyy", seconds=20.0, call_seconds=0.5)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(calls) == 4, "the hung call counts as one no, then three yes: %d calls" % len(calls)


def test_a_call_that_never_answers_ends_in_the_error_with_what_happened(tmp_path):
    r, calls = _run_the_wait(tmp_path, "h", seconds=1.5, call_seconds=0.3)
    assert r.returncode == 1 and "uv did not answer within" in r.stdout, r.stdout + r.stderr


def test_the_wait_comes_after_uv_is_installed_and_before_the_user_path():
    """The names of the steps are read as text, in file order (the workflow has one job): no YAML library is needed."""
    names = [n.strip() for n in re.findall(r"^\s*-\s*name:\s*(.+)$", WORKFLOW.read_text(encoding="utf-8"), re.M)]
    wait = names.index("wait for the index to serve this version")
    assert max(i for i, n in enumerate(names) if n.startswith("uv, from its own installer")) < wait < names.index("The user path, end to end")


def test_the_wait_step_still_passes_the_gate_of_the_install_job():
    from invisible_core import install_job
    assert install_job.problems(WORKFLOW.read_text(encoding="utf-8")) == []
