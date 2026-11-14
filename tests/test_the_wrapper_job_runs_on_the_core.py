"""The wrapper's tests run on the core about to be published, and a release of the
core cannot go through without them.

The wrapper pins this package and is published after it, so the wrapper's CI
installs the PINNED core from the index: until `wrapper-on-this-core` existed, no
test of the wrapper that stands on the core's Juggler client (a Lifecycle, an
InjectedScript, a Connection, the protocol) ran on the code that was about to be
released.

What is held here, on the files themselves and on the function that computes the
list of tests:

  1. `ci.yml` has the job, on a release pull request (head branch `release/*` or
     the label `release`) and on a push to main, on Linux and Windows in two parts
     each, with the pin left alone (`INVISIBLE_CORE_PIN=allow`,
     `INVISIBLE_CORE_AUTOFIX=off`) and the core installed over it.
  2. The single verdict (`gate`) needs it, under the very condition the job has:
     required, and green, where the job applies; skipped where it does not.
  3. The list of tests is computed, never typed, and it is not empty: the grep
     finds the files of a sample wrapper tree that import the Juggler client or
     build one of its objects by hand, and only those. A list that got shorter
     than main's is red; an empty one is red.
  4. A run is red on a failure, on a SKIPPED test, on a part that ran no file it
     was given, and on a run that ran nothing; a failed attempt that was run again
     is not a skip.

Every check is also run on a known-bad input first: a check that has only ever
said "fine" is not known to be able to say anything else.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / "scripts" / "wrapper_on_this_core.py"
JOB = "wrapper-on-this-core"


def _load_script():
    # By path, as a module of its own: `scripts/` is not a package.
    assert SCRIPT.exists(), f"{SCRIPT} does not exist: nothing computes the list of wrapper tests"
    spec = importlib.util.spec_from_file_location("wrapper_on_this_core_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _flat(expr) -> str:
    return re.sub(r"\s+", "", str(expr))


def _bare(expr) -> str:
    """An expression without its `${{ }}` and whitespace."""
    return re.sub(r"^\$\{\{|\}\}$", "", _flat(expr))


def _spec() -> dict:
    spec = yaml.safe_load(CI.read_text(encoding="utf-8"))
    if True in spec and "on" not in spec:
        spec["on"] = spec.pop(True)
    return spec


# ------------------------------------------------------------------ the workflow

def job_problems(spec: dict) -> list[str]:
    out = []
    jobs = spec.get("jobs") or {}
    job = jobs.get(JOB)
    if not isinstance(job, dict):
        return [f"ci.yml has no job `{JOB}`"]
    condition = _flat(job.get("if"))
    for needle, what in (("github.event_name=='push'", "a push to main"),
                         ("startsWith(github.head_ref,'release/')", "a release/* head branch"),
                         ("contains(github.event.pull_request.labels.*.name,'release')",
                          "the label `release`")):
        if needle not in condition:
            out.append(f"the job does not run on {what}")
    on = spec.get("on") or {}
    if "pull_request" not in on or "main" not in ((on.get("push") or {}).get("branches") or []):
        out.append("ci.yml no longer runs on pull requests and on pushes to main")
    include = ((job.get("strategy") or {}).get("matrix") or {}).get("include") or []
    legs = sorted((m.get("os"), m.get("part")) for m in include)
    if legs != [("linux", 1), ("linux", 2), ("windows", 1), ("windows", 2)]:
        out.append(f"the legs are {legs}, not Linux and Windows in two parts each")
    env = job.get("env") or {}
    if str(env.get("INVISIBLE_CORE_PIN")) != "allow" or str(env.get("INVISIBLE_CORE_AUTOFIX")) != "off":
        out.append("the pin is not left alone (INVISIBLE_CORE_PIN=allow, INVISIBLE_CORE_AUTOFIX=off)")
    steps = job.get("steps") or []
    text = "\n".join(str(s.get("run", "")) for s in steps)
    if not any(s.get("with", {}).get("repository") == "oss-invisible/playwright"
               and s.get("with", {}).get("ref") == "main" for s in steps):
        out.append("the wrapper's main is not checked out")
    for needle, what in (("pip install --force-reinstall --no-deps .", "this core is not installed over the pin"),
                         ("wrapper_on_this_core.py compare", "the list is not compared with main's"),
                         ("wrapper_on_this_core.py\" engine", "the engine of the seal is not fetched and checked"),
                         ("wrapper_on_this_core.py run", "the tests are not run")):
        if needle not in text:
            out.append(what)
    run_step = next((s for s in steps if "wrapper_on_this_core.py run" in str(s.get("run", ""))), {})
    if run_step.get("timeout-minutes") != 8:
        out.append("the test step is not held to 8 minutes")
    if "xvfb-run" not in text:
        out.append("Linux does not run under a virtual display")
    return out


def gate_problems(spec: dict) -> list[str]:
    jobs = spec.get("jobs") or {}
    job, gate = jobs.get(JOB) or {}, jobs.get("gate") or {}
    out = []
    if JOB not in (gate.get("needs") or []):
        out.append(f"`gate` does not need `{JOB}`")
    steps = gate.get("steps") or []
    env = {k: v for s in steps for k, v in (s.get("env") or {}).items()}
    if _bare(env.get("WRAPPER_JOB_REQUIRED", "")) != _bare(job.get("if", "")):
        out.append("`gate` decides whether the job was required by another condition than the job's own `if`")
    run = "\n".join(str(s.get("run", "")) for s in steps)
    required = re.search(r'if \[ "\$WRAPPER_JOB_REQUIRED" = "true" \]; then\s+'
                         r'test "\$\{\{ needs\.wrapper-on-this-core\.result \}\}" = "success"\s+'
                         r'else\s+test "\$\{\{ needs\.wrapper-on-this-core\.result \}\}" = "skipped"\s+fi', run)
    if not required:
        out.append("`gate` does not demand success where the job is required and a skip elsewhere")
    return out


OLD_CI = """
name: ci
on:
  push:
    branches: [main]
  pull_request:
jobs:
  tests:
    runs-on: ubuntu-latest
    steps: [{run: "true"}]
  gate:
    needs: [tests]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: test "${{ needs.tests.result }}" = "success"
"""


def test_the_workflow_before_this_job_is_caught():
    old = yaml.safe_load(OLD_CI)
    assert job_problems(old) == [f"ci.yml has no job `{JOB}`"]
    assert f"`gate` does not need `{JOB}`" in gate_problems(old)


def test_a_job_that_runs_on_less_is_caught():
    spec = _spec()
    job = spec["jobs"][JOB]
    job["if"] = "github.event_name == 'push'"
    job["strategy"]["matrix"]["include"] = job["strategy"]["matrix"]["include"][:2]
    job["env"]["INVISIBLE_CORE_AUTOFIX"] = "on"
    found = job_problems(spec)
    assert any("release/*" in p for p in found)
    assert any("label" in p for p in found)
    assert any("legs" in p for p in found)
    assert any("pin" in p for p in found)


def test_a_gate_that_forgets_the_job_or_decides_otherwise_is_caught():
    spec = _spec()
    spec["jobs"]["gate"]["needs"] = [n for n in spec["jobs"]["gate"]["needs"] if n != JOB]
    assert gate_problems(spec)
    spec = _spec()
    for s in spec["jobs"]["gate"]["steps"]:
        s.get("env", {}).pop("WRAPPER_JOB_REQUIRED", None)
    assert gate_problems(spec)


def test_the_core_ci_runs_the_wrapper_tests_on_this_core():
    assert job_problems(_spec()) == []


def test_the_single_verdict_requires_it_where_it_applies_and_only_there():
    assert gate_problems(_spec()) == []


# --------------------------------------------------------------- the list of tests

def _sample_tree(root: Path) -> Path:
    tests = root / "tests"
    (tests / "playwright-upstream").mkdir(parents=True)
    files = {
        "test_a_frame_dispatch.py": "from invisible_core.juggler.connection import Connection\n",
        "test_import_module.py": "import invisible_core.juggler\n",
        "test_from_package.py": "from invisible_core import config, juggler\n",
        "test_builds_by_hand.py": "def test_x():\n    lc = Lifecycle(None)\n",
        "test_injected_by_hand.py": "def test_x():\n    InjectedScript(source='')\n",
        "test_connection_by_hand.py": "def test_x():\n    Connection(pipe)\n",
        "test_unrelated.py": "from invisible_core.config import Config\n",
        "test_only_a_comment.py": "# see invisible_core.juggler and Lifecycle for why\n",
        "helper.py": "from invisible_core.juggler import x\n",
        "playwright-upstream/test_vendored.py": "from invisible_core.juggler import y\n",
    }
    for name, body in files.items():
        (tests / name).write_text(body, encoding="utf-8")
    return root


SAMPLE_LIST = ["tests/test_a_frame_dispatch.py", "tests/test_builds_by_hand.py",
               "tests/test_connection_by_hand.py", "tests/test_from_package.py",
               "tests/test_import_module.py", "tests/test_injected_by_hand.py"]


def test_the_grep_finds_the_files_that_stand_on_the_juggler_client_and_only_those(tmp_path):
    mod = _load_script()
    computed = mod.computed_list(_sample_tree(tmp_path))
    assert computed, "the computed list is empty on a tree that has such files"
    assert computed == SAMPLE_LIST


def test_the_list_is_empty_on_a_tree_with_no_such_file(tmp_path):
    mod = _load_script()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_unrelated.py").write_text("import os\n", encoding="utf-8")
    assert mod.computed_list(tmp_path) == []


def test_a_shorter_list_than_mains_is_caught(tmp_path):
    mod = _load_script()
    assert mod.missing_from(SAMPLE_LIST[:-1], SAMPLE_LIST) == [SAMPLE_LIST[-1]]
    assert mod.missing_from(SAMPLE_LIST, SAMPLE_LIST[:-1]) == []


def _compare(mod, wrapper, main_script=None, no_script=False):
    argv = ["compare", "--wrapper", str(wrapper)]
    argv += ["--main-has-no-script"] if no_script else ["--main-script", str(main_script)]
    return mod.main(argv)


def test_compare_is_red_on_an_empty_list_a_shorter_list_and_an_unreadable_main(tmp_path):
    mod = _load_script()
    wrapper = _sample_tree(tmp_path / "wrapper")
    # main's copy of the script that finds one file MORE than this checkout's
    longer = tmp_path / "main_longer.py"
    longer.write_text("def computed_list(wrapper):\n"
                      "    return %r + ['tests/test_gone.py']\n" % SAMPLE_LIST, encoding="utf-8")
    same = tmp_path / "main_same.py"
    same.write_text("def computed_list(wrapper):\n    return %r\n" % SAMPLE_LIST, encoding="utf-8")
    broken = tmp_path / "main_broken.py"
    broken.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    assert _compare(mod, wrapper, longer) == 1
    assert _compare(mod, wrapper, broken) == 1
    assert _compare(mod, wrapper, tmp_path / "does_not_exist.py") == 1
    assert _compare(mod, wrapper, same) == 0
    assert _compare(mod, wrapper, no_script=True) == 0
    empty = tmp_path / "empty"
    (empty / "tests").mkdir(parents=True)
    assert _compare(mod, empty, same) == 1
    assert _compare(mod, empty, no_script=True) == 1


# ----------------------------------------------------------------------- the judge

def _junit(path: Path, cases: list[str]) -> Path:
    path.write_text('<?xml version="1.0"?><testsuites><testsuite name="pytest">'
                    + "".join(cases) + "</testsuite></testsuites>", encoding="utf-8")
    return path


PASS = '<testcase classname="tests.test_a" name="test_x" time="0.1"/>'
PASS_B = '<testcase classname="tests.test_b.TestK" name="test_y" time="0.1"/>'
FAIL = '<testcase classname="tests.test_a" name="test_f"><failure message="no"/></testcase>'
SKIP = '<testcase classname="tests.test_a" name="test_s"><skipped type="pytest.skip" message="no screencast"/></testcase>'
RERUN = '<testcase classname="tests.test_a" name="test_r"><skipped type="pytest.rerun" message="again"/></testcase>'
FILES = ["tests/test_a.py", "tests/test_b.py"]


def test_the_judge_is_red_on_a_failure_a_skip_a_missing_file_and_an_empty_run(tmp_path):
    mod = _load_script()
    ok = _junit(tmp_path / "ok.xml", [PASS, PASS_B])
    assert mod.judge_junit([ok], FILES, [0]) == []
    assert any("FAILED" in p for p in mod.judge_junit([_junit(tmp_path / "f.xml", [PASS, PASS_B, FAIL])], FILES, [1]))
    assert any("SKIPPED" in p for p in mod.judge_junit([_junit(tmp_path / "s.xml", [PASS, PASS_B, SKIP])], FILES, [0]))
    assert any("tests/test_b.py" in p for p in mod.judge_junit([_junit(tmp_path / "m.xml", [PASS])], FILES, [0]))
    assert any("no test ran" in p for p in mod.judge_junit([_junit(tmp_path / "e.xml", [])], [], [5]))
    assert mod.judge_junit([tmp_path / "never_written.xml"], [], [5, 5])
    assert any("code" in p for p in mod.judge_junit([ok], FILES, [0, 2]))


def test_an_attempt_that_was_run_again_is_not_a_skip(tmp_path):
    mod = _load_script()
    assert mod.judge_junit([_junit(tmp_path / "r.xml", [PASS, PASS_B, RERUN])], FILES, [0]) == []


def test_the_parts_cover_the_list_once_and_windows_leaves_out_what_it_cannot_run():
    mod = _load_script()
    files = [f"tests/test_{c}.py" for c in "abcdefg"] + ["tests/test_juggler_posix_pipe.py"]
    for system in ("linux", "windows"):
        parts = [mod.files_of_part(files, system, n, 2) for n in (1, 2)]
        assert not set(parts[0]) & set(parts[1])
        assert sorted(parts[0] + parts[1]) == sorted(
            f for f in files if not (system == "windows" and f == "tests/test_juggler_posix_pipe.py"))
    whole, nodes = mod.excluded_nodes("windows")
    assert "tests/test_juggler_posix_pipe.py" in whole
    assert any(n.endswith("test_a_missing_pref_refuses_the_launch") for n in nodes)
    assert "tests/test_juggler_posix_pipe.py" not in mod.excluded_nodes("linux")[0]
