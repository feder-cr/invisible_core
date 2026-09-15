"""The pre-push policy, driven against known-bad inputs.

Every gate below spent time being a gate that had only ever printed PASS. The
pin check skipped silently for anyone whose workbench had moved and then printed
"all tests green - push proceeding". The name scanner returned a clean verdict
over three of the four surfaces a public repo publishes. The core's hook ran no
tests at all. None of that was visible from the outside, because a gate that
does not run and a gate that passed produce the same push.

So the policy takes its collaborators by injection - the interpreter, the runner
that spawns subprocesses, the environment, the refs on stdin - and every test
here drives it with one of them deliberately broken. Nothing spawns pytest,
nothing touches a network, and every assertion is about a decision the policy
made rather than about a string it happened to print.

The one exception is the closing summary, which IS the product: it is what a
human reads before deciding the push was checked. Its failure mode is claiming
more than ran, so it is asserted as a claim, not as prose.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path
from typing import Sequence

import pytest

from invisible_core import hooks

pytestmark = pytest.mark.unit

_PIN = "sync_core_pin.py"
_NAME = "check_forbidden_names.py"
_DISCLOSURE = "check_internal_disclosure.py"

#: The repositories that run this hook. TWO since 2026-08-18, when
#: `invisible_firefox` was deleted: the GitHub repo is gone and so is the
#: checkout beside this one. The package is still on PyPI, unyanked, but nothing
#: here can read a tree that no longer exists.
#:
#: Defined UP HERE because the parametrised policy test below is decorated with
#: it, and a decorator argument is evaluated at import time. It used to sit next
#: to the stub tests at the bottom with the list spelled out a second time in the
#: decorator; one list is what stops the two from drifting.
#:
#: Dropping the dead name was not cosmetic. `_stub()` and the policy test both
#: `pytest.skip` as soon as ONE listed repo is missing, so the stale entry did
#: not shrink these checks, it disabled them: on 2026-08-18 the byte-comparison
#: of the surviving stubs was skipping on the workbench itself.
_SIBLINGS = ["invisible_core", "invisible_playwright"]

#: Named rather than inlined because writing them inline needs escapes, and the
#: first attempt at these three tests went through a shell heredoc where every
#: backslash-n became a real newline and the module stopped parsing. Constants
#: cost nothing and cannot be corrupted that way.
_NO_PYTEST_NO_PIN = "pytest = false" + chr(10) + "pin = false"
_A_BRANCH_PUSH = "refs/heads/main aaa refs/heads/main bbb" + chr(10)
_A_RANGE_PUSH = "refs/heads/main NEW refs/heads/main OLD" + chr(10)


class FakeRun:
    """Records every command and answers with a canned exit code.

    Keyed on WHAT was invoked - `pytest`, `sync_core_pin.py`,
    `invisible_core.release` - rather than on a substring of the joined argv. A
    substring match looked fine and was not: pytest's tmp_path is named after
    the running test, so `.../test_the_scan_gets_the_pushed_range_.../scripts/x.py`
    contains both "pytest" and "test_", and three tests silently matched
    commands that had nothing to do with what they were asserting.
    """

    def __init__(self, codes: dict[str, int] | None = None):
        self.calls: list[list[str]] = []
        self.codes = codes or {}

    @staticmethod
    def label(cmd: list[str]) -> str:
        """The script basename or the -m module name. Never a directory."""
        for part in cmd[1:]:
            if part == "-m":
                continue
            if part.endswith(".py"):
                return Path(part).name
            if not part.startswith("-"):
                return part
        return ""

    def __call__(self, cmd, cwd):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        return self.codes.get(self.label(cmd), 0)

    def ran(self, label: str) -> bool:
        return any(self.label(c) == label for c in self.calls)

    def call_for(self, label: str) -> list[str]:
        matches = [c for c in self.calls if self.label(c) == label]
        assert matches, f"{label} was never invoked; ran {[self.label(c) for c in self.calls]}"
        return matches[0]


def make_repo(tmp_path: Path, *, block: str | None = "pytest = false\npin = false",
              workbench: bool = True, untested: "Sequence[str]" = ()) -> Path:
    """A checkout at the real depth, with the workbench two levels above it.

    The depth matters: the policy finds the maintainer scripts at
    `<root>/../../scripts`, which is where they sit relative to a real checkout,
    and a test that flattened the layout would never exercise the lookup.
    """
    root = tmp_path / "release" / "pkg"
    root.mkdir(parents=True)
    # A real repository, because the language gate runs IN-PROCESS and asks git
    # for the tracked files: a bare folder made it refuse every scenario with
    # "not inside a git repository". Nothing is added, so the tree is empty and
    # the gate is silent; the two tests that want it to speak stage a file.
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True,
                   env=hooks.outside_the_hook(dict(os.environ)))
    body = "[project]\nname = \"pkg\"\nversion = \"1.0\"\n"
    if block is not None:
        body += "\n[tool.invisible.hooks]\n" + textwrap.dedent(block) + "\n"
    (root / "pyproject.toml").write_text(body, encoding="utf-8")

    if workbench:
        scripts = tmp_path / "scripts"
        scripts.mkdir(exist_ok=True)
        for name in (_PIN, _NAME, _DISCLOSURE):
            (scripts / name).write_text("import sys; sys.exit(0)\n", encoding="utf-8")
            if name not in untested:
                (scripts / f"test_{name}").write_text("import sys; sys.exit(0)\n",
                                                      encoding="utf-8")
    return root


def run_policy(root: Path, *, refs: str = "", run=None, **env):
    run = run if run is not None else FakeRun()
    code = hooks.main(root=root, push_refs=refs, run=run, env=env, python="PY")
    return code, run


# ------------------------------------------------------------ the declaration

def test_a_repo_that_declares_no_policy_is_refused_rather_than_defaulted(tmp_path, capsys):
    """The failure a default would hide.

    Defaulting here means every gate's on/off state is decided by whoever wrote
    the default, for a repository that never said what it wanted. Get it wrong
    in the permissive direction and the push goes out unchecked while printing
    the same thing a checked push prints. That is the exact shape of every
    incident this module exists because of, so the absent block is a refusal.
    """
    root = make_repo(tmp_path, block=None)
    code, run = run_policy(root)
    assert code == 1
    assert run.calls == [], "a gate ran before the policy was even known"
    assert "no [tool.invisible.hooks] block" in capsys.readouterr().err


def test_a_pyproject_that_does_not_parse_is_refused(tmp_path, capsys):
    root = make_repo(tmp_path)
    (root / "pyproject.toml").write_text("[project\nbroken", encoding="utf-8")
    code, _ = run_policy(root)
    assert code == 1
    assert "does not parse" in capsys.readouterr().err


def test_a_misspelt_key_is_named_rather_than_ignored(tmp_path, capsys):
    """`pytst = false` reads, to its author, as "the suite is off here".

    Nothing consults that key, so the suite would run anyway - the harmless
    direction. The other direction of the same mistake is a gate that silently
    does not run, and neither direction is legible from the outside, which is
    the property this whole module exists to restore. So the typo is named.
    """
    root = make_repo(tmp_path, block="pytst = false\npin = false")
    with pytest.raises(hooks.HookConfigError, match="pytst"):
        hooks.hook_config(root)
    assert run_policy(root)[0] == 1
    assert "nothing reads" in capsys.readouterr().err


def test_a_bare_string_release_tag_is_accepted_as_one_prefix(tmp_path):
    """`release_tags = "v"` is what a human writes. Taken as a string it would
    iterate into ["v"] by accident of characters, which happens to work and
    would stop working the moment somebody wrote "rel"."""
    cfg = hooks.hook_config(make_repo(tmp_path, block='release_tags = "rel"'))
    assert cfg["release_tags"] == ["rel"]


@pytest.mark.parametrize("repo", _SIBLINGS)
def test_every_repo_that_uses_this_hook_declares_its_policy(repo):
    """The declaration is the only per-repo part left, so it is the only part
    that can go missing. Read from the workbench when it is there; skipped in an
    installed copy, which has no sibling repositories."""
    here = Path(__file__).resolve().parents[2] / repo / "pyproject.toml"
    if not here.is_file():
        pytest.skip("not the workbench - the sibling repos are not here")
    cfg = hooks.hook_config(here.parent)
    assert cfg["pytest"] is True, (
        f"{repo} does not run its tests before a push. That is how this repo "
        f"pushed red: hard rule 3 names this hook as what prevents it.")
    assert isinstance(cfg["release_tags"], list) and cfg["release_tags"]


# ------------------------------------------------------------------- the suite

def test_red_tests_stop_the_push_and_nothing_downstream_runs(tmp_path, capsys):
    root = make_repo(tmp_path, block="pytest = true\npin = true")
    code, run = run_policy(root, run=FakeRun({"pytest": 1}))
    assert code == 1
    assert not run.ran(_PIN), "the pin gate ran after the suite was already red"
    assert not run.ran(_NAME)
    assert "TESTS FAILED" in capsys.readouterr().err


def test_pytest_false_means_no_suite_is_spawned(tmp_path):
    code, run = run_policy(make_repo(tmp_path))
    assert code == 0
    assert not run.ran("pytest")


# --------------------------------------------------------------------- the pin

def test_an_unreachable_pin_checker_refuses_instead_of_skipping(tmp_path, capsys):
    """The exact bug: it used to be a bare `if [ -f ... ]` that skipped with no
    output and fell through to a line reading "all tests green - push
    proceeding". Moving the workbench, or pushing from a worktree at another
    depth, disarmed the gate and printed something indistinguishable from a
    pass."""
    root = make_repo(tmp_path, block="pytest = false\npin = true", workbench=False)
    code, run = run_policy(root)
    assert code == 1
    out = capsys.readouterr()
    assert "REFUSED" in out.err and "not reachable" in out.err
    assert not run.ran(_NAME), "the name scan ran past a refusal"


def test_skipping_the_pin_is_possible_and_the_summary_says_so(tmp_path, capsys):
    """There must be an escape hatch, or people reach for --no-verify - which
    turns off every gate in the hook, not this one."""
    root = make_repo(tmp_path, block="pytest = false\npin = true", workbench=False)
    code, run = run_policy(root, INVISIBLE_PIN_CHECK="skip")
    assert code == 0
    assert not run.ran(_PIN)
    out = capsys.readouterr().out
    assert "INVISIBLE_PIN_CHECK=skip" in out
    assert "SKIPPED: pin gate" in out, "the summary hid a gate that did not run"


def test_a_pin_that_does_not_match_stops_the_push(tmp_path, capsys):
    root = make_repo(tmp_path, block="pytest = false\npin = true")
    code, run = run_policy(root, run=FakeRun({_PIN: 1}))
    assert code == 1
    assert "does not match the core" in capsys.readouterr().err


def test_the_pin_gate_runs_its_own_cases_before_its_verdict_is_trusted(tmp_path, capsys):
    """A gate that has only ever printed PASS is not a gate. Its eighteen
    known-bad cases live next to it and nothing else in any repo that pins
    this package runs them, so without this they are a runbook note."""
    root = make_repo(tmp_path, block="pytest = false\npin = true")
    run = FakeRun({f"test_{_PIN}": 1})
    code, run = run_policy(root, run=run)
    assert code == 1
    assert not run.ran(_PIN), "the pin gate was consulted with its own cases red"
    assert "cases are RED" in capsys.readouterr().err


def test_the_pin_gates_own_cases_run_first(tmp_path):
    root = make_repo(tmp_path, block="pytest = false\npin = true")
    _, run = run_policy(root)
    order = [FakeRun.label(c) for c in run.calls if FakeRun.label(c).endswith(_PIN)]
    assert order == [f"test_{_PIN}", _PIN]


@pytest.mark.parametrize("gate,label", [(_PIN, "pin"), (_NAME, "name scanner")])
def test_a_gate_with_no_cases_at_all_is_refused(tmp_path, capsys, gate, label):
    """Only THIS gate loses its cases, so only this gate can produce the refusal.

    Stripping both let a test about the pin pass on the name scanner's refusal
    two blocks further down: removing the pin's requirement entirely changed
    nothing about whether this test was green.
    """
    root = make_repo(tmp_path, block="pytest = false\npin = true", untested=[gate])
    code, _ = run_policy(root)
    assert code == 1
    err = capsys.readouterr().err
    assert "unverified" in err and f"the {label} gate" in err


def test_pin_false_never_looks_for_a_checker(tmp_path):
    """The core IS the package the other two pin; there is nothing here to
    compare against, and looking would refuse for the absence of a tool that
    has no job in this repo."""
    code, run = run_policy(make_repo(tmp_path, block="pytest = false\npin = false",
                                     workbench=False))
    assert code == 0
    assert not run.ran(_PIN)


# ------------------------------------------------------------------- the names

def test_a_clone_with_no_word_list_is_told_so_and_is_not_blocked(tmp_path, capsys):
    """The failure the first cut of this gate shipped: it refused whenever the
    scanner was not at the workbench path, so every clone of the PUBLIC repo was
    stopped on every push with no way to comply - the word list deliberately
    does not exist outside the workbench. A gate red by default is one people
    switch off, and switching this one off disarms the rest of the hook.

    Not blocked, and not silent either: silence reads exactly like a scan that
    ran and passed."""
    root = make_repo(tmp_path, block="pytest = false\npin = false", workbench=False)
    code, _ = run_policy(root)
    assert code == 0
    out = capsys.readouterr().out
    assert "no forbidden-name word list here" in out
    assert "SKIPPED: name scan" in out


def test_a_scanner_that_was_configured_and_is_missing_is_a_refusal(tmp_path, capsys):
    """Setting the variable is somebody saying "run this". Not finding it then
    is a broken gate, not an absent one, and the two must not look alike."""
    root = make_repo(tmp_path, block="pytest = false\npin = false", workbench=False)
    code, _ = run_policy(root, INVISIBLE_NAME_CHECK=str(tmp_path / "nowhere.py"))
    assert code == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err and "points at nothing" in err


def test_a_red_scanner_stops_an_ordinary_branch_push(tmp_path):
    """Not release-tag-only, unlike the publish gate: a forbidden name is public
    as soon as the BRANCH lands, and a force-push does not take it back - GitHub
    keeps the object reachable by SHA."""
    root = make_repo(tmp_path, block="pytest = false\npin = false")
    code, _ = run_policy(root, refs="refs/heads/main aaa refs/heads/main bbb\n",
                         run=FakeRun({_NAME: 1}))
    assert code == 1


def test_a_red_disclosure_scanner_refuses_the_push(tmp_path):
    """The twin of the name-scanner case above, and it was missing.

    Written because a mutation survived: replacing the `return rc` after the
    internal-disclosure gate with `pass` left the whole hook suite green, so
    the hook could run the gate, be told REFUSED, and push anyway. Running a
    gate and ignoring its verdict is worse than not running it, because the
    summary line then claims it ran."""
    root = make_repo(tmp_path, block=_NO_PYTEST_NO_PIN)
    code, run = run_policy(root, refs=_A_BRANCH_PUSH,
                           run=FakeRun({_DISCLOSURE: 1}))
    assert code == 1, "a refused disclosure scan let the push through"
    assert run.ran(_DISCLOSURE)


def test_skipping_the_disclosure_scan_says_so(tmp_path, capsys):
    root = make_repo(tmp_path, block=_NO_PYTEST_NO_PIN)
    code, run = run_policy(root, INVISIBLE_DISCLOSURE_CHECK="skip")
    assert code == 0
    assert not run.ran(_DISCLOSURE)
    out = capsys.readouterr().out
    assert "INVISIBLE_DISCLOSURE_CHECK=skip" in out
    assert "SKIPPED: disclosure scan" in out


def test_the_disclosure_scan_is_diff_scoped_when_a_range_exists(tmp_path):
    """Whole-corpus mode would fail on a long-standing phrase, and a gate that
    refuses things nobody just wrote is a gate people switch off."""
    root = make_repo(tmp_path, block=_NO_PYTEST_NO_PIN)
    _, run = run_policy(root, refs=_A_RANGE_PUSH)
    call = run.call_for(_DISCLOSURE)
    assert "--range" in call and "OLD..NEW" in call


def test_skipping_the_scan_says_what_it_costs(tmp_path, capsys):
    root = make_repo(tmp_path, block="pytest = false\npin = false")
    code, run = run_policy(root, INVISIBLE_NAME_CHECK="skip")
    assert code == 0
    assert not run.ran(_NAME)
    out = capsys.readouterr().out
    assert "INVISIBLE_NAME_CHECK=skip" in out
    assert "force-push does not remove it" in out
    assert "SKIPPED: name scan" in out


def test_the_scan_gets_the_pushed_range_when_git_supplies_one(tmp_path):
    """Commit messages are one of the four surfaces, and the surface ten of our
    own names went out on. They are only scannable with a range, and a range
    only exists when the hook is invoked by git."""
    root = make_repo(tmp_path, block="pytest = false\npin = false")
    _, run = run_policy(root, refs="refs/heads/main NEW refs/heads/main OLD\n")
    call = run.call_for(_NAME)
    assert "--range" in call and "OLD..NEW" in call


def test_no_range_is_stated_out_loud_rather_than_passed_as_empty(tmp_path, capsys):
    """`--range ""` would scan nothing while looking like a message scan. With
    no range the policy scans files and says which surface it could not cover."""
    root = make_repo(tmp_path, block="pytest = false\npin = false")
    _, run = run_policy(root, refs="")
    call = run.call_for(_NAME)
    assert "--range" not in call
    assert "COMMIT MESSAGES were not" in capsys.readouterr().out


# ----------------------------------------------------------------- the ranges

@pytest.mark.parametrize("refs,expected", [
    ("refs/heads/main NEW refs/heads/main OLD\n", "OLD..NEW"),
    # The remote has never seen this ref. With no repo to ask, the function
    # does not invent a range it cannot verify: it answers "".
    # The two real cases - with and without new commits - are tested at the end.
    ("refs/heads/f NEW refs/heads/f 0000000\n", ""),
    # A branch deletion pushes no content, so there is nothing to scan. Taking
    # it would build `X..0000000`, which git reads as a range going backwards.
    ("refs/heads/f 0000000 refs/heads/f OLD\n", ""),
    ("", ""),
])
def test_push_range(refs, expected):
    assert hooks.push_range(refs) == expected


@pytest.mark.parametrize("refs,prefixes,expected", [
    ("x NEW refs/tags/v1.0 0000\n", ["v"], "refs/tags/v1.0"),
    ("x NEW refs/tags/release-18 0000\n", ["v"], ""),
    ("x NEW refs/tags/release-18 0000\n", ["v", "release-"], "refs/tags/release-18"),
    ("x NEW refs/heads/main OLD\n", ["v"], ""),
    # Deleting a tag publishes nothing, so it must not start a publish gate.
    ("x 0000000 refs/tags/v1.0 OLD\n", ["v"], ""),
])
def test_release_tag_in(refs, prefixes, expected):
    assert hooks.release_tag_in(refs, prefixes) == expected


# ----------------------------------------------------------- the publish gate

def test_an_ordinary_push_never_starts_the_publish_gate(tmp_path):
    """It builds the project twice. A hook that costs a minute on every push is
    a hook people delete, and deleting it takes every other gate with it."""
    root = make_repo(tmp_path, block="pytest = false\npin = false")
    _, run = run_policy(root, refs="refs/heads/main a refs/heads/main b\n")
    assert not run.ran("invisible_core.release")


def test_a_release_tag_runs_the_gate_against_this_repo_and_the_index(tmp_path):
    root = make_repo(tmp_path, block="pytest = false\npin = false")
    _, run = run_policy(root, refs="x NEW refs/tags/v1.0 0000\n")
    call = run.call_for("invisible_core.release")
    # --project-root is a TOP-LEVEL option. Putting it after the subcommand made
    # argparse exit 2, which the shell version read as a refusal - so it printed
    # REFUSED without the gate ever running, and the first verification of that
    # wiring was itself wrong.
    assert call.index("--project-root") < call.index("check")
    assert str(root) in call
    assert "--verify-index" in call, (
        "the gate was asked only what the local ledger claims. The ledger is a "
        "claim; the index is the fact, and they have disagreed.")


@pytest.mark.parametrize("rc,allowed", [
    (0, True),
    # ALREADY PUBLISHED AND BYTE-IDENTICAL. Not a refusal: it is what re-pushing
    # or back-filling a release tag legitimately produces, and the workflow
    # no-ops on it the same way. A local layer that blocks what CI waves through
    # is a layer people switch off.
    (5, True),
    (1, False),     # refused
    (2, False),     # gate broken - including an index that could not be reached
    (4, False),     # no usable ledger
])
def test_only_a_pass_and_an_explicit_no_op_let_a_release_tag_through(
        tmp_path, rc, allowed):
    root = make_repo(tmp_path, block="pytest = false\npin = false")
    code, _ = run_policy(root, refs="x NEW refs/tags/v1.0 0000\n",
                         run=FakeRun({"invisible_core.release": rc}))
    assert (code == 0) is allowed, (
        f"gate exit {rc} was treated as {'a pass' if code == 0 else 'a refusal'}")


def test_an_unreachable_index_is_not_read_as_not_published(tmp_path, capsys):
    """The direction that authorises an upload nobody checked.

    The gate answers 2 (GATE BROKEN) when it cannot reach the index, precisely
    so this layer is never left guessing. Guessing "not published" would let a
    version number be reused, and a PyPI filename is never re-uploaded.
    """
    root = make_repo(tmp_path, block="pytest = false\npin = false")
    code, _ = run_policy(root, refs="x NEW refs/tags/v1.0 0000\n",
                         run=FakeRun({"invisible_core.release": 2}))
    assert code == 1
    assert "gate exit 2" in capsys.readouterr().err


# --------------------------------------------------------- the closing summary

def test_the_summary_never_claims_a_gate_that_did_not_run(tmp_path, capsys):
    """The old closing line was `all tests green, pin matches, no forbidden
    names - push proceeding`, printed unconditionally in one of the three hooks
    and from a variable set in three places and read in none in another. It is
    the sentence a human reads before believing the push was checked.

    ⛔ EVERY GATE HAS TO BE NAMED IN THE BLOCK, and `english` joined it on
    2026-09-15. The scenario is "nothing is configured to run", so a new gate
    belongs in the list of things turned off - otherwise this stops testing the
    summary and starts testing how many gates happen to be on by default.
    """
    root = make_repo(tmp_path,
                     block="pytest = false\npin = false\nenglish = false",
                     workbench=False)
    code, _ = run_policy(root)
    assert code == 0
    line = [l for l in capsys.readouterr().out.splitlines() if "push proceeding" in l][0]
    # ⛔ A CLAIM LIVES BEFORE `(SKIPPED:`, AND ONLY THERE. Searching the whole
    # line conflates the two halves, which say opposite things: naming a gate in
    # the SKIPPED list is the honest disclosure this test exists to require, not
    # the false claim it exists to forbid. It read the whole line until
    # 2026-09-15 and survived only because no skip label happened to contain a
    # claim label - `language` does.
    claimed = line.split("(SKIPPED:")[0]
    for absent in ("tests", "pin", "names", "publish", "language"):
        assert f"- {absent}" not in claimed and f", {absent}" not in claimed, (
            f"the summary claims {absent!r} ran: {line}")
    assert "NOTHING was checked" in claimed


def test_the_summary_lists_exactly_what_ran(tmp_path, capsys):
    root = make_repo(tmp_path, block="pytest = true\npin = true")
    code, _ = run_policy(root, refs="x NEW refs/tags/v1.0 0000\n")
    assert code == 0
    line = [l for l in capsys.readouterr().out.splitlines() if "push proceeding" in l][0]
    assert "tests" in line and "pin" in line and "names" in line and "publish gate" in line
    assert "internals" in line, (
        "the internal-disclosure gate ran but the summary does not say so: "
        + line)
    assert "SKIPPED" not in line


# ------------------------------------------------------------------ the stubs
#
# `_SIBLINGS` is defined at the top of this module: the policy test above is
# parametrised on it, and a decorator runs at import time.


def _stub(repo: str) -> Path:
    p = Path(__file__).resolve().parents[2] / repo / ".githooks" / "pre-push"
    if not p.is_file():
        pytest.skip("not the workbench - the sibling repos are not here")
    return p


def test_every_repo_ships_the_same_stub():
    """The property the whole module buys, asserted as bytes.

    743 lines of shell across three files, 207 of 211 comparable lines identical
    between two of them, and nine of the eleven differences accidental. They
    diverged because a copy is the kind of thing you fix in whichever file
    happens to be open, and nothing anywhere compared them.

    Those counts are from the measurement that bought this test, when there were
    three stubs. `invisible_firefox` was deleted on 2026-08-18 and the property
    is unchanged for the two that remain: two copies of a shell script drift for
    exactly the same reason three did.
    """
    texts = {r: _stub(r).read_text(encoding="utf-8") for r in _SIBLINGS}
    distinct = set(texts.values())
    assert len(distinct) == 1, (
        "the pre-push stubs have diverged again:\n  " +
        "\n  ".join(f"{r}: {len(t.splitlines())} lines, "
                    f"{sum(1 for a, b in zip(t.splitlines(), texts['invisible_core'].splitlines()) if a != b)}"
                    f" lines differ from the core's" for r, t in texts.items()))


@pytest.mark.parametrize("repo", _SIBLINGS)
def test_the_stub_carries_no_policy(repo):
    """A stub that starts deciding things is three copies again, one commit at
    a time. It may find an interpreter and hand over stdin; the moment it names
    a gate, the divergence has somewhere to happen."""
    text = _stub(repo).read_text(encoding="utf-8")
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "invisible_core.hooks" in body, "the stub does not reach the policy"
    for token in ("pytest", "sync_core_pin", "check_forbidden_names",
                  "version_gate", "verify-index", "INVISIBLE_PIN_CHECK",
                  "INVISIBLE_NAME_CHECK", "refs/tags"):
        assert token not in body, (
            f"{repo}'s stub decides something about {token!r}. That decision "
            f"now exists in three places and will be fixed in one of them.")


@pytest.mark.parametrize("repo", _SIBLINGS)
def test_the_stub_is_tracked_as_executable(repo):
    """git refuses to run a non-executable hook: it prints that it ignored it
    and exits 0, so the push succeeds with every gate inert. All three were
    tracked 100644, and it survived because core.fileMode is false on Windows."""
    from invisible_core.testing import assert_hook_is_executable

    assert_hook_is_executable(_stub(repo).parents[1])


# ------------------------------------------- the helpers, on known-bad clones
#
# Three assertions run in every repo that pins this package through
# `invisible_core.testing` (three repos when this was written, two since
# invisible_firefox was deleted 2026-08-18), which means one silent mistake in
# any of them disarms every suite that shares it at once. A gate that has only
# ever printed PASS is not a gate, and that goes double for one shared this
# widely - so each is driven here against a clone deliberately in the state it
# exists to catch.

def _git_repo(tmp_path: Path, hook_text: str = "x\n") -> Path:
    import subprocess
    repo = tmp_path / "clone"
    (repo / ".githooks").mkdir(parents=True)
    (repo / ".githooks" / "pre-push").write_text(hook_text, encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "1.0"\n\n[tool.invisible.hooks]\n'
        'pytest = false\npin = false\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    return repo


def test_the_armed_helper_fails_on_a_clone_that_is_not_armed(tmp_path):
    """The state every fresh clone starts in, and the state two of the three
    repos had no way to notice they were in."""
    from invisible_core.testing import assert_hooks_are_armed

    repo = _git_repo(tmp_path)
    with pytest.raises(AssertionError, match="not armed"):
        assert_hooks_are_armed(repo)

    import subprocess
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", ".githooks"],
                   check=True, capture_output=True)
    assert_hooks_are_armed(repo)            # and it passes once armed


def test_the_executable_helper_fails_on_the_mode_git_ignores(tmp_path):
    """100644 is what all three were tracked as. git prints that it ignored the
    hook and exits 0, so the push succeeds with every gate inert."""
    import subprocess

    from invisible_core.testing import assert_hook_is_executable

    repo = _git_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "update-index", "--chmod=-x",
                    ".githooks/pre-push"], capture_output=True)
    with pytest.raises(AssertionError, match="100644"):
        assert_hook_is_executable(repo)

    subprocess.run(["git", "-C", str(repo), "update-index", "--chmod=+x",
                    ".githooks/pre-push"], check=True, capture_output=True)
    assert_hook_is_executable(repo)


@pytest.mark.parametrize("stub,why", [
    ("#!/bin/sh\necho hello\n", "does not hand over"),
    # A stub that reaches the policy AND decides something itself is how three
    # copies come back, one commit at a time.
    ("#!/bin/sh\npython -m pytest -q\nexec python -c \"from invisible_core.hooks "
     "import main; main()\"\n", "decides something"),
])
def test_the_wiring_helper_fails_on_a_stub_that_is_not_one(tmp_path, stub, why):
    from invisible_core.testing import assert_pre_push_policy_is_wired

    with pytest.raises(AssertionError, match=why):
        assert_pre_push_policy_is_wired(_git_repo(tmp_path, stub))


# ------------------------------------------------ the ref new on the remote

def _repo_with_commits(tmp_path, n):
    """A repo with n commits and no remote: every commit is "new"."""
    import subprocess
    r = tmp_path / "r"
    r.mkdir()

    def run(*a):
        subprocess.run(["git", "-C", str(r), *a], check=True,
                       capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    for i in range(n):
        (r / ("f%d.txt" % i)).write_text("x")
        run("add", "-A")
        run("commit", "-qm", "c%d" % i)
    sha = subprocess.run(["git", "-C", str(r), "rev-list", "-1", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    return r, sha


def test_push_range_on_a_new_ref_is_one_token_that_git_diff_accepts(tmp_path):
    """ONE token, always. This is the defect that refused a release.

    This case used to return the string "<sha> --not --remotes": three tokens in
    one. The name scanner survived it, the disclosure gate handed it to
    `git diff` as a SINGLE revision and got "fatal: bad revision", and the hook
    refused the push over an error of its own. Measured 2026-08-11 pushing the
    tag v18.14.0, which could not go out.
    """
    import subprocess
    repo, sha = _repo_with_commits(tmp_path, 3)
    refs = "refs/heads/f %s refs/heads/f %s" % (sha, "0" * 40)

    rng = hooks.push_range(refs, repo)

    assert rng, "with new commits there has to be a range"
    assert len(rng.split()) == 1, (
        "push_range must return ONE token: %r has %d, and whoever receives it "
        "cannot know whether to split it" % (rng, len(rng.split())))
    out = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", rng],
                         capture_output=True, text=True)
    assert out.returncode == 0, (
        "git diff refuses the range produced: %s" % out.stderr.strip())


def test_push_range_is_empty_when_the_commits_are_already_published(tmp_path):
    """A TAG on an already published commit: nothing new to read.

    This is the real case that blocked v18.14.0. The ref is new on the remote
    (remote sha all zeroes) but the COMMITS are not, so the right answer is "",
    and any range in its place refuses a legitimate push.
    """
    import subprocess
    repo, sha = _repo_with_commits(tmp_path, 2)
    subprocess.run(["git", "-C", str(repo), "update-ref",
                    "refs/remotes/origin/main", sha], check=True)
    refs = "refs/tags/v1.0.0 %s refs/tags/v1.0.0 %s" % (sha, "0" * 40)

    assert hooks.push_range(refs, repo) == "", (
        "the commits are already on a remote: there is nothing to scan, and "
        "saying so with an odd range makes the push be refused")


# ------------------------------------------------ what the hook hands a gate

_LOCATION = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
             "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
             "GIT_PREFIX", "GIT_NAMESPACE")


def test_a_gate_the_hook_launches_is_not_told_which_repository_the_hook_is_about(
        tmp_path, monkeypatch):
    """git exports `GIT_DIR` and its siblings to a hook, and from a WORKTREE
    the value is absolute. A test suite the hook launches builds throwaway
    repositories with `git init`; with those variables inherited, its `init`,
    `add` and `commit` land in the hook's repository instead of the throwaway
    one. Measured 2026-09-14 on a refused push of this package: five test
    commits on the release branch, `core.bare = true` in the shared config and
    `origin/main` moved - all from a suite that thought it was working in
    `tmp_path`.

    EXECUTED through the real runner with a real child process: the child
    reports which of those variables it can see, and the answer has to be
    none of them. The runner is the one place the hook spawns anything, so it
    is the one place this can be true for every gate at once.

    Known-bad: drop the `env=` from `_subprocess_run` and inherit the
    environment again.
    """
    import json
    import os
    import subprocess
    import sys

    for name in _LOCATION:
        monkeypatch.setenv(name, str(tmp_path / "hook-repo" / ".git" / "worktrees" / "x"))
    monkeypatch.setenv("INVISIBLE_UNRELATED", "kept")
    report = tmp_path / "seen.json"
    code = hooks._subprocess_run(
        [sys.executable, "-c",
         "import json, os, sys; json.dump({k: v for k, v in os.environ.items() "
         "if k.startswith('GIT_') or k == 'INVISIBLE_UNRELATED'}, "
         "open(sys.argv[1], 'w'))", str(report)],
        tmp_path)
    assert code == 0
    seen = json.loads(report.read_text(encoding="utf-8"))

    leaked = sorted(k for k in seen if k in _LOCATION)
    assert leaked == [], (
        "the gate the hook launches is told which repository the hook is about, "
        "so its throwaway repositories are ours: %s" % leaked)
    assert seen.get("INVISIBLE_UNRELATED") == "kept", (
        "the runner drops more than the repository location, so a gate loses "
        "the environment it was configured with")
    assert set(hooks.HOOK_LOCATION_VARIABLES) == set(_LOCATION)
    # And the shape of the incident, with git itself: a throwaway `init` and
    # `commit` run through the runner while an absolute GIT_DIR names a decoy
    # repository must leave the decoy exactly as it was.
    decoy = tmp_path / "hook-repo"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True, capture_output=True,
                   env={k: v for k, v in os.environ.items() if k not in _LOCATION})
    quiet = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    quiet = {k: v for k, v in quiet.items() if k not in _LOCATION}
    subprocess.run(["git", "-C", str(decoy), "commit", "-q", "--allow-empty", "-m", "decoy"],
                   check=True, capture_output=True, env=quiet)
    before = subprocess.run(["git", "-C", str(decoy), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True, env=quiet).stdout
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, "t" if "NAME" in name else "t@t")
    throwaway = tmp_path / "throwaway"
    assert hooks._subprocess_run(["git", "init", "-q", str(throwaway)], tmp_path) == 0
    assert hooks._subprocess_run(["git", "-C", str(throwaway), "commit", "-q",
                                  "--allow-empty", "-m", "c0"], tmp_path) == 0
    after = subprocess.run(["git", "-C", str(decoy), "rev-parse", "HEAD"],
                           check=True, capture_output=True, text=True, env=quiet).stdout
    assert after == before, "the throwaway commit landed in the hook's repository"
    assert (throwaway / ".git").is_dir(), "the throwaway repository was never made"


# ------------------------------------------------------------- the language

#: In-string newlines are `chr(10)`, like the constants at the top of this file
#: and for the reason written there: a backslash-n written through a shell
#: heredoc arrives as a REAL newline and the module stops parsing. It happened
#: again writing these two tests, on 2026-09-16.
_ENGLISH_CLEAN = "# This comment is in English, which is what the repo uses." + chr(10)
#: The Italian known-bad is IMPORTED from the gate's own corpus, never retyped
#: here: a page of Italian in this file would be flagged by the very gate it
#: tests, and the corpus lives in one place so there is exactly one exempt path.
from invisible_core.english import _ITA as _ITALIAN  # noqa: E402
_NO_GATES_AT_ALL = _NO_PYTEST_NO_PIN + chr(10) + "english = false"


def _stage(root, rel, text):
    """Write a file and stage it, so `git ls-files` sees it and so does the gate."""
    (root / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", rel], cwd=str(root), check=True, capture_output=True,
                   env=hooks.outside_the_hook(dict(os.environ)))


def test_italian_prose_in_a_tracked_file_refuses_the_push(tmp_path, capsys):
    """The known-bad, run IN-PROCESS against a real repository.

    ⛔ THE FIRST VERSION OF THIS GATE SPAWNED A CHILD `python -m` AND DIED
    WITHOUT SAYING SO. From a git worktree the child imported the interpreter's
    editable install, another checkout with no `english` module, and the hook
    read its exit code as "the files above are not in English" with nothing
    above. The gate is now a sibling call, so this test can only be green if the
    module ran here and refused for the reason it prints.
    """
    root = make_repo(tmp_path, block=_NO_PYTEST_NO_PIN, workbench=False)
    _stage(root, "clean.py", _ENGLISH_CLEAN)
    assert run_policy(root)[0] == 0, "a clean tree must pass"
    capsys.readouterr()          # the clean run's summary is not the one under test
    _stage(root, "guilty.py", _ITALIAN)
    code, _ = run_policy(root)
    out = capsys.readouterr().out
    assert code == 1
    assert "guilty.py" in out, out
    assert "not in English" in out, out
    assert "push proceeding" not in out


def test_english_false_turns_the_language_gate_off_and_says_so(tmp_path, capsys):
    """Off is a declaration, and it shows up as SKIPPED, never as a claim."""
    root = make_repo(tmp_path, block=_NO_GATES_AT_ALL, workbench=False)
    _stage(root, "guilty.py", _ITALIAN)
    code, _ = run_policy(root)
    line = [row for row in capsys.readouterr().out.splitlines()
            if "push proceeding" in row][0]
    assert code == 0
    assert "language" in line.split("(SKIPPED:")[1], line
    assert "language" not in line.split("(SKIPPED:")[0], line
