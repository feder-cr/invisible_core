"""The publish gate is WIRED, and these tests are what keeps it wired.

The gate itself was written first and connected to nothing: `grep -rn
version_gate` over the whole workbench found the script, its own test file and a
comment in pyproject.toml, and every path that could actually reach an upload
went around it. There was no .github/ and no pre-push hook in the repo at all.
A gate somebody has to remember is a runbook note wearing a gate's clothes.

Three layers now stand between a working tree and the index, and each one is
asserted here so it cannot quietly rot:

  1. `version_gate.py publish` - the only supported upload path. It runs `check`
     in-process and never reaches twine on a refusal (proved in
     test_release_gate.py, which drives it against real known-bad trees).
  2. .github/workflows/publish.yml - the index credential lives in a GitHub
     Environment that only the upload job names, and that job declares
     `needs: gate`. Without a green gate the job holding the credential never
     starts. There is no step ordering to get wrong.
  3. .githooks/pre-push - refuses to push a release tag whose gate is red, so
     the tag that starts (2) never leaves the machine.

These are structural assertions over files, deliberately: a test that spun up a
real GitHub Actions run or pushed a real tag would not run anywhere, and a
wiring check that does not run is the thing being fixed.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import invisible_core

# From THIS FILE, not from where the module is installed: the latter is the
# repo under an editable install and .../Lib under a regular one, so the same
# expression means two different things depending on how it was installed.
REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "scripts" / "version_gate.py"
HOOK = REPO_ROOT / ".githooks" / "pre-push"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"
INSTALLER = REPO_ROOT / "scripts" / "install_hooks.py"

pytestmark = pytest.mark.integration

# These files are outside both build targets, so they are absent from an
# installed copy and from the sdist. Skipping is correct there; failing would
# make `pytest` red for a user who merely installed the package.
_in_checkout = GATE.exists()
requires_checkout = pytest.mark.skipif(
    not _in_checkout, reason="not a source checkout - the release wiring does not ship")


@requires_checkout
def test_every_layer_of_the_wiring_exists():
    missing = [str(p) for p in (GATE, HOOK, WORKFLOW, INSTALLER) if not p.exists()]
    assert missing == [], (
        "the publish gate is wired to nothing again:\n  " + "\n  ".join(missing))


@requires_checkout
def test_the_hook_is_tracked_as_executable():
    """The claim and its measurement live in the helper, which all three repos
    call - this hook was tracked 100644 in every one of them, so a per-repo copy
    of the assertion would have been a third copy of one finding."""
    from invisible_core.testing import assert_hook_is_executable

    assert_hook_is_executable(REPO_ROOT)


# ------------------------------------------------------------------ layer 2

@requires_checkout
def test_the_upload_job_cannot_start_without_the_gate_job():
    """The load-bearing property of the workflow: the job holding the credential
    cannot start unless the gate job succeeded.

    Parsed as YAML, not matched as a string. The first version required the
    literal `needs: gate` and went red the moment a second dependency was added
    (`needs: [already-published, gate]`) even though the property it exists to
    protect was untouched. A test that fails on a formatting change it does not
    care about is a test people edit without reading.
    """
    import yaml

    spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = spec.get("jobs") or {}
    assert "upload" in jobs, "the publish workflow has no `upload` job"
    upload = jobs["upload"]

    needs = upload.get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or [])
    assert "gate" in needs, (
        f"the upload job declares needs={needs!r}, which does not include the "
        f"gate, so it can run past a red one")

    assert str(upload.get("environment", "")).startswith("pypi"), \
        "the upload job does not name the environment the credential lives in"

    steps = " ".join(str(s.get("run", "")) for s in upload.get("steps", []))
    assert "version_gate.py publish" in steps, \
        "the upload job does not go through the gate's own publish path"


@requires_checkout
def test_the_index_probe_gates_both_other_jobs():
    """The guard added on 2026-07-26 is only a guard if BOTH jobs honour it.

    It exists so a tag naming a version already on the index is a no-op rather
    than a failed upload. If `upload` kept running when the probe says the
    version is present, the guard would be decoration and the upload would fail
    on a filename the index has already served.
    """
    import yaml

    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    assert "already-published" in jobs, "the index probe job is gone"
    for name in ("gate", "upload"):
        job = jobs[name]
        needs = job.get("needs")
        needs = [needs] if isinstance(needs, str) else list(needs or [])
        assert "already-published" in needs, (
            f"`{name}` does not depend on the index probe, so it runs even when "
            f"the version is already published")
        cond = str(job.get("if", ""))
        assert "already-published" in cond and "present" in cond, (
            f"`{name}` has no condition on the probe's answer: {cond!r}")


@requires_checkout
def test_no_job_uploads_without_going_through_the_gate():
    """Any `twine upload` written directly into the workflow would be an upload
    path the gate does not sit in front of."""
    text = WORKFLOW.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "twine upload" not in stripped, (
            f"the workflow uploads directly, around the gate: {stripped!r}\n"
            "  use `version_gate.py publish`, which runs the gate in-process first.")
        assert "pypi-publish" not in stripped, (
            f"a publish action bypasses the gate entirely: {stripped!r}")


@requires_checkout
def test_the_gate_job_runs_the_gate_and_the_checkout_brings_the_ledger():
    text = WORKFLOW.read_text(encoding="utf-8")
    gate_job = text.split("\n  gate:", 1)[1].split("\n  upload:", 1)[0]
    assert "version_gate.py check" in gate_job
    # A shallow checkout that dropped PUBLISHED.json is one of the ways the gate
    # used to disarm itself silently. It now exits 4, but not having the file at
    # all in CI would just mean every release run fails.
    assert "fetch-depth: 0" in gate_job


# ------------------------------------------------------------------ layer 3
#
# The hook is a STUB now. The policy it hands over to is `invisible_core.hooks`,
# driven against its own known-bad inputs in test_hook_policy.py - forty-odd
# cases, none of which spawn a shell. What is left to check here is the handover
# itself, which is real code and has its own ways of failing silently: finding
# an interpreter, reaching the policy at all, and passing stdin through. git
# feeds the refs being pushed on stdin, and a stub that swallowed them would
# leave every range-dependent gate reporting "nothing to scan" - correctly, and
# with no coverage. That happened, in the shell version, for a day.
#
# So these run the REAL stub, through a real shell, against fake repositories.


def _fake_repo(tmp_path, *, block="pytest = false\npin = false") -> Path:
    """A checkout carrying the real stub and a policy declaration."""
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    (repo / ".githooks" / "pre-push").write_text(
        HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    body = '[project]\nname = "pkg"\nversion = "1.0"\n'
    if block is not None:
        body += "\n[tool.invisible.hooks]\n" + block + "\n"
    (repo / "pyproject.toml").write_text(body, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True,
                   capture_output=True, text=True)
    return repo


def _push(repo: Path, sh: str, stdin: str, **env) -> tuple[int, str]:
    r = subprocess.run(
        [sh, str(repo / ".githooks" / "pre-push"), "origin",
         "https://example.invalid/x.git"],
        cwd=str(repo), input=stdin, capture_output=True, text=True,
        env={**_env(), "INVISIBLE_GATE_PYTHON": sys.executable, **env})
    return r.returncode, (r.stdout or "") + (r.stderr or "")


@requires_checkout
def test_the_stub_reaches_the_shared_policy_and_says_what_ran(tmp_path):
    """End to end: the real stub, a real shell, the real policy module."""
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")
    code, text = _push(_fake_repo(tmp_path), sh,
                       "refs/heads/main a refs/heads/main b\n")
    assert code == 0, text
    assert "push proceeding" in text, text


@requires_checkout
def test_the_stub_passes_the_pushed_refs_through(tmp_path):
    """The failure that hid for a day in the shell version.

    A `while read` in the first gate drained stdin, so the second one found no
    range and reported "no commit messages scanned" on every push - a true
    statement, printed by a gate covering nothing. Nothing downstream can tell
    "there was no range" from "the range never arrived", so it is asserted here,
    at the only place that can: a release tag is visible ONLY through stdin.
    """
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")
    repo = _fake_repo(tmp_path)
    code, text = _push(repo, sh, "refs/tags/v1.0 aaaa refs/tags/v1.0 0000\n")
    assert "release tag" in text, (
        "the refs did not survive the handover, so no range-dependent gate "
        "could have run:\n" + text)
    # And the gate it starts is the publish gate, which refuses here: this
    # throwaway repo is not a publishable project.
    assert code != 0, text


@requires_checkout
def test_an_ordinary_push_never_starts_the_publish_gate(tmp_path):
    """It builds the project twice. A hook that costs a minute on every push is
    a hook people delete, and deleting it takes every other gate with it."""
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")
    code, text = _push(_fake_repo(tmp_path), sh,
                       "refs/heads/main a refs/heads/main b\n")
    assert code == 0, text
    assert "release tag" not in text
    assert "publish gate" not in text


@requires_checkout
def test_the_stub_refuses_when_it_cannot_reach_the_policy(tmp_path):
    """"The gate is not here" is the condition under which skipping is most
    tempting and least defensible: from the outside it is indistinguishable
    from a gate that was deleted on purpose.

    Driven for real, against a checkout whose own `src/` shadows the installed
    core with one that has no policy in it. That is the shape this actually
    takes: the stub puts `src` FIRST on purpose, so a checkout gates itself with
    the tree being pushed, and a half-written tree therefore wins over whatever
    is installed. Emptying PYTHONPATH instead proved nothing here - the core is
    installed, so it stayed importable and the test skipped itself.
    """
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")
    repo = _fake_repo(tmp_path)
    (repo / "src" / "invisible_core").mkdir(parents=True)
    (repo / "src" / "invisible_core" / "__init__.py").write_text("", encoding="utf-8")
    code, text = _push(repo, sh, "refs/heads/main a refs/heads/main b\n")
    assert code != 0, "a push went out past a hook that could not check it"
    assert "REFUSED" in text and "cannot import" in text, text


@requires_checkout
def test_the_stub_refuses_when_there_is_no_interpreter_at_all(tmp_path):
    """No python means no gate can run, in either direction. The shell version
    resolved an interpreter into a variable and then called bare `python`
    anyway, in one of the three copies."""
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")
    repo = _fake_repo(tmp_path)
    empty = str(tmp_path / "empty-bin")
    (tmp_path / "empty-bin").mkdir()
    r = subprocess.run(
        [sh, str(repo / ".githooks" / "pre-push"), "origin", "https://x.invalid/y"],
        cwd=str(repo), input="refs/tags/v1.0 a refs/tags/v1.0 0\n",
        capture_output=True, text=True,
        env={"PATH": empty, "SYSTEMROOT": _env().get("SYSTEMROOT", "")})
    text = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, "a release tag went out with no interpreter to gate it"
    assert "REFUSED" in text and "no python" in text.lower()


@requires_checkout
def test_the_stub_works_from_any_directory(tmp_path):
    """git runs a hook from the top level, but a worktree, a submodule or a
    human running it by hand does not. The stub cd's to its own parent, and the
    policy reads the repo from there."""
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")
    repo = _fake_repo(tmp_path)
    (repo / "deep" / "deeper").mkdir(parents=True)
    r = subprocess.run(
        [sh, str(repo / ".githooks" / "pre-push"), "origin", "https://x.invalid/y"],
        cwd=str(repo / "deep" / "deeper"),
        input="refs/heads/main a refs/heads/main b\n",
        capture_output=True, text=True,
        env={**_env(), "INVISIBLE_GATE_PYTHON": sys.executable})
    text = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, text
    assert "push proceeding" in text


# ---------------------------------------------- the hooks are actually armed

@requires_checkout
def test_the_hooks_are_armed_in_this_clone():
    """The reminder that does not depend on anybody remembering.

    A checked-in hook directory does nothing until core.hooksPath points at it,
    and git will not do that for you. Running the test suite is the one thing a
    developer does constantly, so the suite is where this belongs.

    The claim and its measurement live once, in the helper, because all three
    repos make it - until 2026-07-27 only this one did, and the other two
    shipped a hook that nothing armed and nothing checked was armed.
    """
    from invisible_core.testing import assert_hooks_are_armed

    assert_hooks_are_armed(REPO_ROOT)


def _find_sh():
    for name in ("sh", "bash"):
        from shutil import which
        p = which(name)
        if p:
            return p
    for p in (r"C:\Program Files\Git\bin\sh.exe", r"C:\Program Files\Git\usr\bin\sh.exe"):
        if Path(p).exists():
            return p
    return None


def _env():
    import os
    return {k: v for k, v in os.environ.items() if k != "INVISIBLE_GATE_PYTHON"}


@requires_checkout
def test_the_hook_and_the_workflow_agree_on_what_a_release_tag_means(tmp_path):
    """Both layers ask the INDEX, so neither can block what the other allows.

    They did not always. The first release tag ever pushed here was refused by
    this hook and would have been waved through by the workflow: the hook ran
    the gate against the WORKING TREE, which has legitimately moved on since the
    tagged version was published, so it read as "the content changed under an
    unmoved version" - for a tag that was simply marking something already
    shipped. A local layer that blocks what CI would accept is a layer people
    switch off, and switching this one off disables the rest of it too.

    THE HOOK'S HALF MOVED. It used to curl the index itself and exit 0 on a
    200, which answers a weaker question than the gate does - present on the
    index, versus present AND byte-identical - and answers it from a second
    source of truth. It now runs the gate with `--verify-index` and reads the
    answer: exit 5 is the no-op, exit 2 is an index it could not reach, and
    only the first of those lets the tag through. Those two branches are
    asserted directly, against a policy driven with each code in turn, in
    test_hook_policy.py. Asserting them here as well would be a second, weaker
    copy - which is what the deleted half of this test had become.

    What is left here is the workflow, which still asks the index itself, and
    the claim that the two layers cannot disagree.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "pypi.org/pypi/invisible-core/" in workflow, (
        "the workflow does not ask the index whether the version is already "
        "published, so the two layers can disagree about a tag again")
    assert '200' in workflow, (
        "the workflow does not branch on the index answering 200 (published)")

    # An unreachable index must not read as "not published": that is the
    # direction that authorises an upload nobody checked.
    assert "neither" in workflow and "guessing" in workflow, (
        "the workflow treats an unreachable index as a definite answer")

    # The hook's side of the same claim, read from the policy rather than from
    # its prose: an already-published tag is a no-op, a broken gate is not.
    from invisible_core import hooks

    assert hooks.GATE_NOTHING_TO_DO == 5, (
        "the no-op code moved; the hook and the gate now disagree about what a "
        "re-pushed release tag means")
