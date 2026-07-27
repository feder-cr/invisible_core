"""One marker vocabulary for the three repositories, and it stays one.

WHAT WENT WRONG. `integration` meant "several modules together, no browser" in
`invisible_core` and `invisible_playwright`, and "launches the real patched
Firefox binary named by the seal" in `invisible_firefox`. Nothing said so; the
two definitions simply sat in two pyprojects.

The consequences were not cosmetic:

  * the manager's default selection was `not integration and not e2e`, correct
    for ITS meaning and wrong for the shared one, so its every-push suite was
    shaped by a rule written for one test out of eighty-five;
  * CLAUDE.md's "integration is reserved for explicit release runs" was true in
    one repo and false in the other two, where the marker names the tests that
    cover the release wiring - exactly the ones a push most needs;
  * `invisible_core`'s addopts excluded `slow` while never declaring it, so the
    exclusion matched nothing and the word meant nothing;
  * a hook comment written on 2026-07-27, copied from the manager into the
    module that now speaks for all three, carried the wrong contract with it.

Nothing could have caught any of that: three pyprojects, no comparison. This is
the comparison. It reads the sibling repos when the workbench is present and
skips otherwise, the same shape as the pre-push stub check next door - an
installed copy has no siblings and no opinion about them.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPOS = ["invisible_core", "invisible_playwright", "invisible_firefox"]
_RELEASE = Path(__file__).resolve().parents[2]


def _pytest_config(repo: str) -> dict:
    path = _RELEASE / repo / "pyproject.toml"
    if not path.is_file():
        pytest.skip("not the workbench - the sibling repos are not here")
    with path.open("rb") as fh:
        return tomllib.load(fh)["tool"]["pytest"]["ini_options"]


def test_all_three_declare_the_same_markers():
    got = {r: _pytest_config(r)["markers"] for r in _REPOS}
    first = got[_REPOS[0]]
    for repo, markers in got.items():
        assert markers == first, (
            f"{repo}'s marker vocabulary has drifted from {_REPOS[0]}'s.\n"
            f"  only here:  {sorted(set(markers) - set(first))}\n"
            f"  only there: {sorted(set(first) - set(markers))}\n"
            f"A marker that means two things selects two different sets under "
            f"one name, and nothing downstream can tell which one it got.")


def test_all_three_run_the_same_selection_by_default():
    """The default selection IS the pre-push gate: `invisible_core.hooks` runs
    a bare `pytest -q` in each repo, so whatever addopts says is what "never
    push red" means there."""
    got = {r: _pytest_config(r)["addopts"] for r in _REPOS}
    first = got[_REPOS[0]]
    assert all(v == first for v in got.values()), got


def test_every_excluded_marker_is_actually_declared():
    """`-m 'not slow'` against an undeclared marker excludes nothing and reads
    like a rule. That was live in invisible_core: `slow` was excluded, never
    declared, and therefore meaningless."""
    for repo in _REPOS:
        cfg = _pytest_config(repo)
        declared = {m.split(":")[0].strip() for m in cfg["markers"]}
        excluded = {w for w in cfg["addopts"].replace("'", " ").split()
                    if w in ("slow", "e2e", "integration", "unit", "linux_only")}
        missing = excluded - declared
        assert not missing, (
            f"{repo} excludes {sorted(missing)} by default without declaring "
            f"it, so the exclusion matches nothing")


def test_strict_markers_is_on_so_a_typo_cannot_be_silent():
    """Without it, `@pytest.mark.uint` is a warning nobody reads: the test still
    runs, and every `-m unit` selection quietly misses it. The manager had a
    real instance - a marker used by `test_process_layer.py` and never
    registered."""
    for repo in _REPOS:
        assert "--strict-markers" in _pytest_config(repo)["addopts"], repo


# ---------------------------------------------- the suite-collapse tripwire

def test_every_repo_refuses_a_run_in_which_nothing_ran():
    """A selection that collapses is a green tick, and pytest cannot see it.

    `-m` with a typo'd marker, a `norecursedirs` that swallowed the tests, an
    addopts edit - all of them exit 0 having run almost nothing. Each repo's CI
    therefore reads the count back out of the report and refuses below a floor.

    THIS repo did not have one. Found 2026-07-28 while fixing the wrapper's,
    which fired correctly when 322 tests moved out and the floor stayed at 600.
    The core - the package both consumers pin - was the only one of the three
    without the guard, which is the same asymmetry that had left it as the only
    one whose pre-push hook ran no tests at all.

    Asserted on the CI workflow that runs the DEFAULT selection, not on every
    workflow: an e2e or install job legitimately runs a handful.
    """
    import re

    wanted = {
        "invisible_core": ".github/workflows/ci.yml",
        "invisible_playwright": ".github/workflows/tests.yml",
        "invisible_firefox": ".github/workflows/ci.yml",
    }
    missing = []
    for repo, rel in wanted.items():
        path = _RELEASE / repo / rel
        if not path.is_file():
            pytest.skip("not the workbench - the sibling repos are not here")
        text = path.read_text(encoding="utf-8")
        m = re.search(r"if passed < (\d+)", text)
        if not m:
            missing.append(f"{repo}: {rel} never compares the count")
            continue
        floor = int(m.group(1))
        if floor < 1:
            missing.append(f"{repo}: floor is {floor}, which refuses nothing")
    assert not missing, (
        "a repository's CI cannot tell a collapsed selection from a pass:\n  "
        + "\n  ".join(missing))
