"""The wait of the install job for the index (no network, no browser).

Ledger #250 of 63.66.0: the two jobs "install from index and launch" failed in
``test_user_install_e2e.py::test_the_version_the_index_serves_matches_this_checkout``, which saw 62.65.0 from the index
seconds after the upload, on both runners: the step that waited for the index had asked the JSON endpoint of the version,
which answered, while the install that followed was served an older page (the wrapper's ledger #476 of 0.50.1 failed
the same way; its fix is 299fc9f). The wait now asks pip itself and ends in an error that says what is missing. The script
of the step is run here as it stands in the workflow, in a checkout of version 9.9.9, with a ``pip`` that answers as a
lagging index does.

Known-bad input: the step as it was (the JSON endpoint, a warning and exit 0 after five minutes) - the first test sees pip
never called, the second sees exit 0 where a forgotten publish must fail.
"""
from __future__ import annotations

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


def _run_the_wait(tmp_path, *, succeeds_on_call, seconds=2.0, poll=0.05):
    _checkout(tmp_path)
    shim = tmp_path / "shim" / "pip"
    shim.mkdir(parents=True)
    (shim / "__init__.py").write_text("", encoding="utf-8")   # a REGULAR package: a namespace one loses to the pip of the environment
    counter = tmp_path / "calls"
    (shim / "__main__.py").write_text(textwrap.dedent("""
        import pathlib, sys
        p = pathlib.Path(%r)
        n = (int(p.read_text()) if p.exists() else 0) + 1
        p.write_text(str(n))
        if %d and n >= %d:
            sys.exit(0)
        sys.stderr.write("ERROR: No matching distribution found for invisible-core==9.9.9\\n")
        sys.exit(1)
    """) % (str(counter), succeeds_on_call, succeeds_on_call), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "shim"), INDEX_WAIT_SECONDS=str(seconds), INDEX_WAIT_POLL=str(poll))
    try:
        r = subprocess.run([sys.executable, "-c", _wait_script()], cwd=tmp_path, env=env, capture_output=True, text=True,
                           timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail("the wait did not end within 60 s with INDEX_WAIT_SECONDS=%s: it does not read its deadline" % seconds)
    calls = int(counter.read_text()) if counter.exists() else 0
    return r, calls


def test_the_wait_asks_pip_and_goes_on_when_pip_gets_the_version(tmp_path):
    r, calls = _run_the_wait(tmp_path, succeeds_on_call=3)
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls == 3, "it asked pip %d times, pip answered on the 3rd" % calls
    assert "pip downloads invisible-core 9.9.9" in r.stdout


def test_the_wait_ends_in_an_error_that_names_the_missing_version_when_the_index_never_has_it(tmp_path):
    r, calls = _run_the_wait(tmp_path, succeeds_on_call=0, seconds=1.0)
    assert r.returncode == 1, "a forgotten publish must fail the job here: " + r.stdout + r.stderr
    assert "::error::the index does not have invisible-core 9.9.9 yet" in r.stdout
    assert "minutes" in r.stdout and "No matching distribution" in r.stdout
    assert calls >= 2


def test_the_wait_step_still_passes_the_gate_of_the_install_job():
    from invisible_core import install_job
    assert install_job.problems(WORKFLOW.read_text(encoding="utf-8")) == []
