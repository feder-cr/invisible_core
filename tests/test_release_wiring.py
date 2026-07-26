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

@requires_checkout
def test_the_pre_push_hook_runs_the_gate_and_refuses_on_any_non_zero():
    text = HOOK.read_text(encoding="utf-8")
    assert "version_gate.py" in text, "the hook does not invoke the gate"
    assert "refs/tags/v*" in text, "the hook does not recognise a release tag"
    assert re.search(r'\$rc"?\s*-ne\s*0', text), \
        "the hook does not refuse on a non-zero gate exit"
    # Exit 2 (gate broken) and exit 4 (no ledger) are not passes either, and a
    # hook that only tested for 1 would let both through.
    assert "-eq 1" not in text and "= 1 ]" not in text, \
        "the hook singles out one exit code; only 0 is a pass"


@requires_checkout
@pytest.mark.parametrize("stdin,expect_gate", [
    ("refs/heads/main aaaa refs/heads/main bbbb\n", False),
    ("refs/tags/v1.0 aaaa refs/tags/v1.0 0000\n", True),
    ("refs/tags/release-18 aaaa refs/tags/release-18 0000\n", True),
])
def test_the_hook_gates_release_tags_and_leaves_ordinary_pushes_alone(
        tmp_path, stdin, expect_gate):
    """Runs the real hook against a fake repo, with a fake gate that always
    refuses. An ordinary branch push must stay fast and silent; a release tag
    must be stopped."""
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")

    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "version_gate.py").write_text(
        "import sys; print('FAKE GATE RAN'); sys.exit(1)\n", encoding="utf-8")
    (repo / ".githooks").mkdir()
    hook = repo / ".githooks" / "pre-push"
    hook.write_text(HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True,
                   capture_output=True, text=True)

    r = subprocess.run([sh, str(hook), "origin", "https://example.invalid/x.git"],
                       cwd=str(repo), input=stdin, capture_output=True, text=True,
                       env={**_env(), "INVISIBLE_GATE_PYTHON": sys.executable})
    text = (r.stdout or "") + (r.stderr or "")

    if expect_gate:
        assert "FAKE GATE RAN" in text, text
        assert r.returncode != 0, "a release tag went out past a red gate"
        assert "REFUSED" in text
    else:
        # "Alone" is about the PUBLISH gate, which builds the project twice and
        # is the reason ordinary pushes were left untouched in the first place -
        # a hook that costs a minute on every push is a hook people delete. The
        # forbidden-name scan is a different animal: sub-second, and a name is
        # public the moment the branch lands, so it runs here too.
        assert "FAKE GATE RAN" not in text, "the hook gates ordinary pushes too"
        assert r.returncode == 0, text


# ------------------------------------------------- the forbidden-name scan

def _fake_repo_with_hook(tmp_path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    (repo / ".githooks" / "pre-push").write_text(
        HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True,
                   capture_output=True, text=True)
    return repo


def _push_ordinary(repo: Path, sh: str, **env) -> tuple[int, str]:
    r = subprocess.run(
        [sh, str(repo / ".githooks" / "pre-push"), "origin",
         "https://example.invalid/x.git"],
        cwd=str(repo), input="refs/heads/main a refs/heads/main b\n",
        capture_output=True, text=True,
        env={**_env(), "INVISIBLE_GATE_PYTHON": sys.executable, **env})
    return r.returncode, (r.stdout or "") + (r.stderr or "")


@requires_checkout
def test_a_clone_with_no_word_list_is_told_so_and_is_not_blocked(tmp_path):
    """The failure the first cut of this gate shipped.

    It refused whenever the scanner was not at the workbench path, so every
    clone of the PUBLIC repo was stopped on every push with no way to comply -
    the word list deliberately does not exist outside the workbench. A gate
    that is red by default is one people switch off, and switching this one off
    disarms every other gate in the same hook.

    Not blocked, and not silent either: silence is indistinguishable from a
    scan that ran and passed.
    """
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")
    code, text = _push_ordinary(_fake_repo_with_hook(tmp_path), sh)
    assert code == 0, text
    assert "no forbidden-name word list here" in text, text


@requires_checkout
def test_a_scanner_that_was_configured_and_is_missing_is_a_refusal(tmp_path):
    """Setting the variable is someone saying "run this". Not finding it then
    is a broken gate, not an absent one, and the two must not look alike."""
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")
    code, text = _push_ordinary(_fake_repo_with_hook(tmp_path), sh,
                                INVISIBLE_NAME_CHECK="/nowhere/at/all.py")
    assert code != 0, "a configured-but-missing scanner was waved through"
    assert "REFUSED" in text and "points at nothing" in text


@requires_checkout
def test_a_scanner_that_refuses_stops_an_ordinary_push(tmp_path):
    """The scan is not release-tag-only, unlike the publish gate: a forbidden
    name is public as soon as the BRANCH lands, and a force-push does not take
    it back - GitHub keeps the object."""
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")
    repo = _fake_repo_with_hook(tmp_path)
    scanner = tmp_path / "scanner.py"
    scanner.write_text("import sys; print('SCAN RAN'); sys.exit(1)\n",
                       encoding="utf-8")
    code, text = _push_ordinary(repo, sh, INVISIBLE_NAME_CHECK=str(scanner))
    assert "SCAN RAN" in text, text
    assert code != 0, "an ordinary push went out past a red name scan"


@requires_checkout
def test_switching_the_scan_off_is_possible_and_says_so_loudly(tmp_path):
    """There has to be an escape hatch or people reach for --no-verify, which
    turns off everything. It must leave a mark that a reader cannot miss."""
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")
    code, text = _push_ordinary(_fake_repo_with_hook(tmp_path), sh,
                                INVISIBLE_NAME_CHECK="skip")
    assert code == 0, text
    assert "WARNING: INVISIBLE_NAME_CHECK=skip" in text
    assert "force-push does not remove it" in text


@requires_checkout
def test_the_hook_refuses_rather_than_skipping_when_it_cannot_run_the_gate(tmp_path):
    """The failure that matters most: the gate could not run at all.

    "The gate is not here" is the one condition under which skipping is most
    tempting and least defensible - it is indistinguishable, from the outside,
    from a gate that was deleted on purpose. Driven for real: a checkout with no
    scripts/version_gate.py, pushing a release tag.
    """
    sh = _find_sh()
    if sh is None:
        pytest.skip("no POSIX shell available to run the hook")

    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    hook = repo / ".githooks" / "pre-push"
    hook.write_text(HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True,
                   capture_output=True, text=True)

    r = subprocess.run([sh, str(hook), "origin", "https://example.invalid/x.git"],
                       cwd=str(repo), input="refs/tags/v1.0 a refs/tags/v1.0 0\n",
                       capture_output=True, text=True,
                       env={**_env(), "INVISIBLE_GATE_PYTHON": sys.executable})
    text = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, "a release tag went out with no gate present at all"
    assert "REFUSED" in text and "missing" in text

    # And the no-interpreter branch refuses in the same direction.
    src = HOOK.read_text(encoding="utf-8")
    assert any("no python" in line.lower() for line in re.findall(r"REFUSED[^\n]*", src)), \
        "the hook does not refuse when there is no interpreter to run the gate"


# ---------------------------------------------- the hooks are actually armed

@requires_checkout
def test_the_hooks_are_installed_in_this_checkout():
    """The reminder that does not depend on anybody remembering.

    A checked-in hook directory does nothing until core.hooksPath points at it,
    and git will not do that for you. Running the test suite is the one thing a
    developer does constantly, so the test suite is where this belongs.

    Skipped outside a git checkout: an sdist or a CI job that unpacked a
    tarball has no hooks to install and no push to make.
    """
    if subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--git-dir"],
                      capture_output=True).returncode != 0:
        pytest.skip("not a git checkout")

    r = subprocess.run([sys.executable, str(INSTALLER), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, (
        (r.stdout or "") + (r.stderr or "") +
        "\n\nThe pre-push publish gate is not armed in this clone, so a release\n"
        "tag could be pushed without the gate ever running. One command:\n"
        "    python scripts/install_hooks.py")


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

    Structural, because the alternative is a test that talks to PyPI on every
    run: both files must consult the index for the version being tagged, before
    anything else decides.
    """
    hook = HOOK.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    for name, text in (("the hook", hook), ("the workflow", workflow)):
        assert "pypi.org/pypi/invisible-core/" in text, (
            f"{name} does not ask the index whether the version is already "
            f"published, so the two layers can disagree about a tag again")
        assert '200)' in text or "'200'" in text or '"200"' in text, (
            f"{name} does not branch on the index answering 200 (published)")

    # And an unreachable index must not read as "not published" in either: that
    # is the direction that authorises an upload nobody checked.
    assert "cannot tell whether it is published" in hook, (
        "the hook treats an unreachable index as a definite answer")
    assert "neither" in workflow and "guessing" in workflow, (
        "the workflow treats an unreachable index as a definite answer")
