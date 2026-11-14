"""The wrapper's tests run on the core about to be published, and a release of the
core cannot go through without them.

The wrapper pins this package and is published after it, so the wrapper's CI
installs the PINNED core from the index: until `wrapper-on-this-core` existed, no
test of the wrapper that stands on the core's Juggler client (a Lifecycle, an
InjectedScript, a Connection, the protocol) ran on the code that was about to be
released.

What is held here, on the files themselves and on the function that computes the
list of tests:

  1. `ci.yml` has the job, on a release pull request (head branch `release/*`),
     on a push to main and on a pull request that touches the job itself (the
     small job `wrapper-job-needed` decides, and is run here on a real diff), on
     Linux in two parts and on
     Windows in four, each leg told how many parts there are, with the pin left alone (`INVISIBLE_CORE_PIN=allow`,
     `INVISIBLE_CORE_AUTOFIX=off`) and the core installed over it.
  2. The single verdict (`gate`) needs it, under the very condition the job has:
     required, and green, where the job applies; skipped where it does not.
  3. The list of tests is computed, never typed, and it is not empty: the grep
     finds the files of a sample wrapper tree that import the Juggler client or
     build one of its objects by hand, and only those. A list that got shorter
     than main's is red; an empty one is red.
  4. Every exclusion has a witness that its reason still holds, and a witness that
     no longer holds is red naming the entry.
  5. A run is red on a failure, on a SKIPPED test, on a part that ran no file it
     was given, and on a run that ran nothing; a failed attempt that was run again
     is not a skip.

Every check is also run on a known-bad input first: a check that has only ever
said "fine" is not known to be able to say anything else.
"""
from __future__ import annotations

import importlib.util
import os
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
JOB_FILES = (".github/workflows/ci.yml", "scripts/wrapper_on_this_core.py",
             "scripts/wrapper_test_seconds.json", "tests/test_the_wrapper_job_runs_on_the_core.py")


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
    if _flat(job.get("if")) != "needs.wrapper-job-needed.outputs.run=='true'" \
            or "wrapper-job-needed" not in (job.get("needs") or []):
        out.append("the job does not run where `wrapper-job-needed` says (its output `run`)")
    selector = jobs.get("wrapper-job-needed")
    decide = next((x for x in ((selector or {}).get("steps") or []) if x.get("id") == "decide"), None)
    if not selector or not decide:
        out.append("ci.yml has no job `wrapper-job-needed` that decides")
    else:
        if "run" not in (selector.get("outputs") or {}):
            out.append("`wrapper-job-needed` has no output `run`")
        if not any((x.get("with") or {}).get("fetch-depth") == 0 for x in selector["steps"]):
            out.append("`wrapper-job-needed` does not fetch the history it diffs")
        text = str(decide.get("run", ""))
        for needle, what in (('"push"', "a push to main"), ("release/*", "a release/* head branch"),
                             ("git diff --name-only", "a diff of the pull request")) + tuple(
                                 (f, f"a change of {f}") for f in JOB_FILES):
            if needle not in text:
                out.append(f"the job does not run on {what}")
        if "label" in text or "labels" in str(decide.get("env", "")):
            out.append("the job still looks at a label")
    on = spec.get("on") or {}
    if "pull_request" not in on or "main" not in ((on.get("push") or {}).get("branches") or []):
        out.append("ci.yml no longer runs on pull requests and on pushes to main")
    include = ((job.get("strategy") or {}).get("matrix") or {}).get("include") or []
    legs = sorted((m.get("os"), m.get("part"), m.get("parts")) for m in include)
    if legs != [("linux", 1, 2), ("linux", 2, 2), ("windows", 1, 4), ("windows", 2, 4),
                ("windows", 3, 4), ("windows", 4, 4)]:
        out.append(f"the legs are {legs}, not Linux in two parts and Windows in four")
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
    if run_step.get("timeout-minutes") != 12:
        out.append("the test step is not held to 12 minutes")
    if '--parts "${{ matrix.parts }}"' not in str(run_step.get("run", "")):
        out.append("a leg is not told how many parts there are (--parts from the matrix)")
    if "xvfb-run" not in text:
        out.append("Linux does not run under a virtual display")
    return out


def gate_problems(spec: dict) -> list[str]:
    jobs = spec.get("jobs") or {}
    job, gate = jobs.get(JOB) or {}, jobs.get("gate") or {}
    out = []
    if JOB not in (gate.get("needs") or []):
        out.append(f"`gate` does not need `{JOB}`")
    if "wrapper-job-needed" not in (gate.get("needs") or []):
        out.append("`gate` does not need `wrapper-job-needed`")
    steps = gate.get("steps") or []
    env = {k: v for s in steps for k, v in (s.get("env") or {}).items()}
    if _bare(env.get("WRAPPER_JOB_REQUIRED", "")) != _bare(job.get("if", "")):
        out.append("`gate` decides whether the job was required by another condition than the job's own `if`")
    run = "\n".join(str(s.get("run", "")) for s in steps)
    required = re.search(r'if \[ "\$WRAPPER_JOB_REQUIRED" = "true" \]; then\s+'
                         r'test "\$\{\{ needs\.wrapper-on-this-core\.result \}\}" = "success"\s+'
                         r'else\s+test "\$\{\{ needs\.wrapper-on-this-core\.result \}\}" = "skipped"\s+fi', run)
    if 'test "${{ needs.wrapper-job-needed.result }}" = "success"' not in run:
        out.append("`gate` does not demand that the decision was made (a failed decision reads as a skip)")
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
    spec["jobs"]["wrapper-job-needed"]["steps"][-1]["run"] = "echo run=true >> $GITHUB_OUTPUT"
    job["strategy"]["matrix"]["include"] = job["strategy"]["matrix"]["include"][:2]
    job["env"]["INVISIBLE_CORE_AUTOFIX"] = "on"
    found = job_problems(spec)
    assert any("wrapper-job-needed" in p for p in found)
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


SECONDS = Path(__file__).resolve().parents[1] / "scripts" / "wrapper_test_seconds.json"
BIG = "tests/test_nested_frame_actions.py"


def _table(mod, system):
    return mod.load_seconds(system, SECONDS)


def _fake_collect(table):
    """The nodes a file has, as the table knows them, plus one the table has never seen."""
    def collect(rel):
        row = table[rel]
        return [rel + "::" + n for n in row["nodes"]] + [rel + "::test_a_node_the_table_never_saw"]
    return collect


def _files_of_the_table(mod, system):
    return sorted(f for f in _table(mod, system) if f not in mod.excluded_nodes(system)[0])


def _plan(mod, system, parts):
    table = _table(mod, system)
    return mod.plan_parts(_files_of_the_table(mod, system), system, parts, _fake_collect(table), table)


@pytest.mark.parametrize("system,parts", [("linux", 2), ("windows", 4)])
def test_every_node_runs_in_exactly_one_part_as_a_whole_file_or_a_chunk(system, parts):
    mod = _load_script()
    table = _table(mod, system)
    files = _files_of_the_table(mod, system)
    collect = _fake_collect(table)
    plan = mod.plan_parts(files, system, parts, collect, table)
    assert len(plan) == parts
    whole = [f for p in plan for f in p["files"]]
    chunked = [n for p in plan for n in p["nodes"]]
    assert len(whole) == len(set(whole)) and len(chunked) == len(set(chunked))
    long_files = {f for f in files if table[f]["parallel"] > mod.SPLIT_OVER_SECONDS}
    assert long_files
    assert not set(whole) & long_files, "a long file ran whole"
    assert sorted(set(whole) | long_files) == files, "a file ran in no part"
    _, nodes_out = mod.excluded_nodes(system)
    for f in long_files:
        expected = sorted(n for n in collect(f) if not any(n.startswith(x) for x in nodes_out))
        assert sorted(n for n in chunked if n.split("::", 1)[0] == f) == expected
    assert {n.split("::", 1)[0] for n in chunked} == long_files


@pytest.mark.parametrize("system,parts", [("linux", 2), ("windows", 4), ("windows", 6)])
def test_no_part_is_empty_and_the_parts_are_balanced(system, parts):
    mod = _load_script()
    plan = _plan(mod, system, parts)
    for part in plan:
        assert part["files"] or part["nodes"], plan
        assert mod.files_of(part)
    seconds = [p["seconds"] for p in plan]
    # longest first onto the least loaded part: no part is far from the mean
    assert max(seconds) - min(seconds) <= 0.15 * (sum(seconds) / parts) + 15, seconds


def test_the_plan_is_the_same_on_every_call_and_whatever_the_order_of_the_files():
    mod = _load_script()
    for system, parts in (("linux", 2), ("windows", 4)):
        table = _table(mod, system)
        files = _files_of_the_table(mod, system)
        first = mod.plan_parts(files, system, parts, _fake_collect(table), table)
        assert first == mod.plan_parts(files, system, parts, _fake_collect(table), table)
        assert first == mod.plan_parts(list(reversed(files)), system, parts,
                                       lambda f: list(reversed(_fake_collect(table)(f))), table)


def test_the_file_over_90_seconds_is_split_and_the_short_ones_are_not():
    mod = _load_script()
    table = _table(mod, "windows")
    assert table[BIG]["parallel"] > 90
    plan = _plan(mod, "windows", 4)
    holders = [i for i, p in enumerate(plan) if any(n.startswith(BIG + "::") for n in p["nodes"])]
    assert len(holders) >= 2, "the longest file stayed in one part"
    for p in plan:
        assert BIG not in p["files"]
    asked = []
    files = _files_of_the_table(mod, "windows")
    mod.plan_parts(files, "windows", 4, lambda f: asked.append(f) or _fake_collect(table)(f), table)
    assert sorted(asked) == sorted(f for f in files if table[f]["parallel"] > 90), \
        "the nodes of a file that is not long were asked for"


def test_an_unknown_file_and_an_unknown_node_are_handled():
    mod = _load_script()
    table = {"tests/test_known.py": {"parallel": 40.0, "serial": 4.0},
             BIG: {"parallel": 400.0, "serial": 0.0, "nodes": {"test_a": 100.0, "test_b": 300.0}}}
    files = ["tests/test_known.py", "tests/test_new.py", BIG]
    nodes = [BIG + "::test_a", BIG + "::test_b", BIG + "::test_c_unseen"]
    plan = mod.plan_parts(files, "linux", 2, lambda f: nodes, table)
    assert sorted(f for p in plan for f in p["files"]) == ["tests/test_known.py", "tests/test_new.py"]
    assert sorted(n for p in plan for n in p["nodes"]) == nodes
    # the new file is worth the default (30 s of parallel time); the unseen node is worth the
    # average of the two the table has (200 s of parallel time)
    assert mod.DEFAULT_SECONDS == 30.0
    total = sum(p["seconds"] for p in plan)
    assert total == pytest.approx(40 / 4 + 4 + 30 / 4 + (100 + 300 + 200) / 4, abs=0.2)


def test_what_cannot_run_on_a_system_is_left_out_by_name_with_its_reason():
    mod = _load_script()
    theme = "tests/test_a_missing_system_theme_pref_refuses_the_launch_e2e.py"
    fission = "tests/test_fission_strategy1_bench.py"
    posix = "tests/test_juggler_posix_pipe.py"
    for system in ("linux", "windows"):
        assert theme in mod.excluded_nodes(system)[0]
    assert posix in mod.excluded_nodes("windows")[0] and posix not in mod.excluded_nodes("linux")[0]
    assert fission + "::test_same_site_other_port_stays_in_process" in mod.excluded_nodes("windows")[1]
    assert not any(n.startswith(fission) for n in mod.excluded_nodes("linux")[1])
    assert all(reason.strip() for _, _, reason in mod.EXCLUDED)
    # a whole-file exclusion keeps the file out of the plan; a node exclusion keeps the node out of its chunk
    table = {fission: {"parallel": 200.0, "serial": 0.0, "nodes": {"test_x": 100.0, "test_y": 100.0}},
             theme: {"parallel": 1.0, "serial": 0.0}}
    nodes = [fission + "::test_x", fission + "::test_y", fission + "::test_same_site_other_port_stays_in_process"]
    plan = mod.plan_parts([fission, theme, "tests/test_other.py"], "windows", 2, lambda f: nodes, table)
    assert theme not in [f for p in plan for f in p["files"]]
    assert sorted(n for p in plan for n in p["nodes"]) == nodes[:2]


def test_the_nodes_of_a_file_are_listed_by_pytest_in_the_leg(tmp_path):
    mod = _load_script()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_two.py").write_text(
        "\n".join(["import pytest", "", "def test_a():", "    pass", "",
                   "@pytest.mark.parametrize('x', [1, 2])", "def test_b(x):", "    pass", ""]),
        encoding="utf-8")
    collect = mod.collect_nodes(tmp_path, dict(os.environ, PYTEST_ADDOPTS=""))
    assert collect("tests/test_two.py") == ["tests/test_two.py::test_a", "tests/test_two.py::test_b[1]",
                                            "tests/test_two.py::test_b[2]"]
    with pytest.raises(RuntimeError):
        collect("tests/test_missing.py")


# ------------------------------------------------- when the job is needed (run for real)

def _decide_script() -> str:
    step = next(x for x in _spec()["jobs"]["wrapper-job-needed"]["steps"] if x.get("id") == "decide")
    return step["run"]


def _decide(tmp_path, event, head_ref, changed):
    """Runs the `decide` step in a throwaway repo where the head commit changes ``changed``."""
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash on this machine")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t" "@example.invalid"]

    def run(*cmd):
        return subprocess.run(list(cmd), cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

    run(*git, "init", "-q")
    (repo / "keep.txt").write_text("a", encoding="utf-8")
    run(*git, "add", "-A")
    run(*git, "commit", "-q", "-m", "base")
    base = run("git", "rev-parse", "HEAD")
    for rel in changed:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("b", encoding="utf-8")
    (repo / "keep.txt").write_text("b", encoding="utf-8")
    run(*git, "add", "-A")
    run(*git, "commit", "-q", "-m", "head")
    head = run("git", "rev-parse", "HEAD")
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    script = tmp_path / "decide.sh"
    script.write_text(_decide_script(), encoding="utf-8", newline="\n")
    env = dict(__import__("os").environ, EVENT=event, HEAD_REF=head_ref, BASE_SHA=base, HEAD_SHA=head,
               RUNNER_TEMP=str(tmp_path), GITHUB_OUTPUT=str(out))
    done = subprocess.run([bash, str(script)], cwd=repo, env=env, capture_output=True, text=True)
    return done, out.read_text(encoding="utf-8").strip()


def test_the_job_is_needed_on_a_push_a_release_branch_and_a_change_of_the_job_only(tmp_path):
    for n, (event, head_ref, changed, expected) in enumerate([
            ("push", "", [], "run=true"),
            ("pull_request", "release/65.72.0", [], "run=true"),
            ("pull_request", "fix/x", ["src/other.py", "docs/a.md"], "run=false"),
            ("pull_request", "fix/x", ["tests/test_other.py", "scripts/other.py"], "run=false")]
            + [("pull_request", "fix/x", [f], "run=true") for f in JOB_FILES]):
        done, out = _decide(tmp_path / str(n), event, head_ref, changed)
        assert done.returncode == 0, done.stderr
        assert out == expected, (event, head_ref, changed, out)


def test_a_diff_that_cannot_be_read_is_red_not_not_needed(tmp_path):
    (tmp_path / "x").mkdir()
    import subprocess
    done, out = _decide(tmp_path / "x", "pull_request", "fix/x", [])
    assert done.returncode == 0 and out == "run=false"
    # the same step, with a base that does not exist
    script = tmp_path / "x" / "decide.sh"
    env = dict(__import__("os").environ, EVENT="pull_request", HEAD_REF="fix/x", BASE_SHA="0" * 40,
               HEAD_SHA="1" * 40, RUNNER_TEMP=str(tmp_path / "x"), GITHUB_OUTPUT=str(tmp_path / "x" / "out2.txt"))
    bad = subprocess.run([__import__("shutil").which("bash"), str(script)], cwd=tmp_path / "x" / "repo",
                         env=env, capture_output=True, text=True)
    assert bad.returncode != 0
    assert not (tmp_path / "x" / "out2.txt").exists() or "run=false" not in (tmp_path / "x" / "out2.txt").read_text()


# ------------------------------------------------------------------ the witnesses

# The text of the wrapper's test files as they are today, cut to what each witness reads.
TODAY = {
    "tests/test_juggler_posix_pipe.py": (
        "import sys\nimport pytest\n\npytestmark = pytest.mark.skipif(\n    sys.platform == \"win32\",\n"
        "    reason=\"the POSIX leg does not execute on Windows\")\n\ndef test_the_posix_pipe_carries_messages_both_ways():\n    pass\n"),
    "tests/test_a_missing_system_theme_pref_refuses_the_launch_e2e.py": (
        "import pytest\n\n" "@pytest.mark.e2e\n" "@pytest.mark.skipif(ON_WINDOWS, reason=\"x\")\n"
        "def test_a_missing_pref_refuses_the_launch(firefox_binary, engine_at_least, pref):\n"
        "    engine_at_least(\"firefox-65\")\n    pass\n\n" "@pytest.mark.e2e\n"
        "def test_every_pref_declared_the_launch_succeeds(firefox_binary, engine_at_least):\n"
        "    engine_at_least(\"firefox-65\")\n    pass\n"),
    "tests/_processes.py": (
        "import sys\nimport pytest\n\ndef site_processes_below(root_pid, site, engine_dir=None):\n"
        "    import psutil\n\n    if not sys.platform.startswith(\"linux\"):\n"
        "        pytest.skip(\"only Linux names a content process\")\n    return set()\n"),
    "tests/test_fission_strategy1_bench.py": (
        "import pytest\nfrom _processes import site_processes_below\n\n"
        "def processes_of(site, engine_dir):\n    \"\"\"The pids.\"\"\"\n"
        "    return site_processes_below(os.getpid(), site, engine_dir)\n\n\n"
        "@pytest.mark.e2e\ndef test_cross_site_iframe_gets_its_own_process(page, engine_dir):\n"
        "    here = processes_of(SITE_A, engine_dir)\n\n\n"
        "@pytest.mark.e2e\ndef test_same_site_other_port_stays_in_process(page, engine_dir):\n"
        "    before = processes_of(SITE_A, engine_dir)\n\n\n"
        "@pytest.mark.e2e\ndef test_frame_list_has_real_urls(page):\n    pass\n"),
    "tests/test_juggler_transport.py": (
        "import pytest\n\ndef test_the_workbench_inventory_and_the_package_cannot_DRIFT():\n"
        "    tool = pathlib.Path(__file__).resolve().parents[3] / \"scripts\" \\\n        / \"inventario_voce6.py\"\n"
        "    if not tool.exists():\n        pytest.skip(\"the workbench is not next to this checkout\")\n\n"
        "def test_other():\n    pass\n"),
    "tests/test_juggler_injected.py": (
        "import pytest\n\ndef test_OUR_CLIENT_DOES_NOT_CROSS_into_the_page_realm():\n"
        "    spec = importlib.util.spec_from_file_location(\n        \"oc\", str(pathlib.Path(__file__).resolve().parents[3]\n"
        "                  / \"tests\" / \"gates\" / \"observable_crossings.py\"))\n"
        "    if spec is None or not pathlib.Path(spec.origin).is_file():\n"
        "        pytest.skip(\"the trap page lives in the workbench, not here\")\n\n"
        "def test_other():\n    pass\n"),
}

# The same text with the reason taken out of it: what each witness must notice.
REASON_REMOVED = {
    "tests/test_juggler_posix_pipe.py": lambda t: t.replace("sys.platform == \"win32\"", "False"),
    "tests/test_a_missing_system_theme_pref_refuses_the_launch_e2e.py":
        lambda t: t.replace("engine_at_least(\"firefox-65\")", "engine_at_least(\"firefox-60\")", 1),
    "tests/_processes.py": lambda t: t.replace("if not sys.platform.startswith(\"linux\"):", "if False:"),
    "tests/test_fission_strategy1_bench.py": lambda t: t.replace("return site_processes_below(", "return set(("),
    "tests/test_juggler_transport.py": lambda t: t.replace("pytest.skip(", "print("),
    "tests/test_juggler_injected.py": lambda t: t.replace("pytest.skip(", "print("),
}


def _wrapper_tree(tmp_path, edits=None):
    root = tmp_path / "work" / "wrapper"
    for rel, text in TODAY.items():
        if edits and rel in edits:
            text = edits[rel](text)
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _entries(mod, system):
    return [t for only, t, _ in mod.EXCLUDED if only in (None, system)]


@pytest.mark.parametrize("system", ["linux", "windows"])
def test_every_exclusion_has_a_witness_and_each_holds_on_todays_text(tmp_path, system):
    mod = _load_script()
    assert set(mod.WITNESSES) == {t for _, t, _ in mod.EXCLUDED}, "an exclusion with no witness, or a witness of none"
    assert mod.witness_problems(_wrapper_tree(tmp_path), system, "firefox-64-candidate") == []


def test_each_witness_is_red_on_a_text_with_its_reason_removed_and_names_the_entry(tmp_path):
    mod = _load_script()
    for n, (only, target, _) in enumerate(mod.EXCLUDED):
        rel = target.split("::")[0]
        for system in ("linux", "windows"):
            if only not in (None, system):
                continue
            root = _wrapper_tree(tmp_path / f"{n}{system}", {rel: REASON_REMOVED[rel]})
            found = mod.witness_problems(root, system, "firefox-64")
            mine = [f for f in found if f.startswith(target + ":")]
            assert len(mine) == 1, (target, system, found)
            assert "the reason is gone: run it, remove the exclusion" in mine[0]


def test_the_engine_witness_is_red_once_the_seal_names_the_engine_the_file_waits_for(tmp_path):
    mod = _load_script()
    theme = "tests/test_a_missing_system_theme_pref_refuses_the_launch_e2e.py"
    root = _wrapper_tree(tmp_path)
    for tag, red in (("firefox-64", False), ("firefox-64-candidate", False), ("firefox-65-candidate", True),
                     ("firefox-66", True), ("local", True), (None, True)):
        found = [f for f in mod.witness_problems(root, "linux", tag) if f.startswith(theme)]
        assert bool(found) is red, (tag, found)


def test_the_workbench_witness_is_red_where_the_workbench_files_are_present(tmp_path):
    mod = _load_script()
    root = _wrapper_tree(tmp_path)
    (root.parent.parent / "scripts").mkdir()
    (root.parent.parent / "scripts" / "inventario_voce6.py").write_text("x", encoding="utf-8")
    found = mod.witness_problems(root, "linux", "firefox-64")
    assert len(found) == 1 and "test_the_workbench_inventory" in found[0] and "exists in this clone" in found[0]


def test_an_exclusion_whose_file_is_gone_or_has_no_witness_is_red(tmp_path):
    mod = _load_script()
    root = _wrapper_tree(tmp_path)
    (root / "tests" / "test_juggler_posix_pipe.py").unlink()
    found = mod.witness_problems(root, "windows", "firefox-64")
    assert len(found) == 1 and "the reason is gone" in found[0] and "posix_pipe" in found[0]
    saved = dict(mod.WITNESSES)
    try:
        mod.WITNESSES.pop("tests/test_juggler_posix_pipe.py")
        assert any("no witness" in f for f in mod.witness_problems(_wrapper_tree(tmp_path / "b"), "windows", "firefox-64"))
    finally:
        mod.WITNESSES.update(saved)
