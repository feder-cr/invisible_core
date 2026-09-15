"""The language gate, and the gate on the gate.

This package is public and English-only, and until 2026-09-15 nothing here
checked: the check existed as a SCRIPT copied into the two consumer
repositories and never into this one. So `invisible_core` - the package both
consumers pin - carried Italian prose in 29 files, two of them in messages a
user reads, while both consumers were green.

The tests below are the ones that matter for a gate, in order of how much they
buy:

  * the known-bad corpus fires, and the must-not-fire cases stay silent. A gate
    that has only ever printed PASS is not a gate;
  * the corpus cannot be quietly emptied;
  * this repository is SILENT under its own gate. That is the condition the
    whole translation existed to reach, and it is the one that would break
    first;
  * an exclusion that names nothing is refused, which is the class that let the
    two copies drift apart without either going red.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from invisible_core import english

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]

# The subprocesses below import THIS tree because `conftest.py` puts its `src`
# on PYTHONPATH at import, for every test that spawns a child. A helper doing
# the same thing lived here first, one file at a time; it was moved there when
# `test_seal_version.py` went red on the same hole, so it is written once.


@pytest.mark.parametrize(
    "name,path,text",
    [(n, p, t) for n, p, t, _ in english.KNOWN_BAD],
    ids=[n.replace(" ", "_") for n, _, _, _ in english.KNOWN_BAD],
)
def test_every_known_bad_input_is_caught(name, path, text):
    is_italian, found = english.inspect(path, text, english.CORPUS_CONFIG)
    assert is_italian, (
        "%s: the gate did not fire. Words it saw: %s" % (name, sorted(found)))


@pytest.mark.parametrize(
    "name,path,text", list(english.MUST_NOT_FIRE),
    ids=[n.replace(" ", "_") for n, _, _ in english.MUST_NOT_FIRE],
)
def test_every_case_that_must_stay_silent_stays_silent(name, path, text):
    is_italian, found = english.inspect(path, text, english.CORPUS_CONFIG)
    assert not is_italian, (
        "%s: false positive on %s. Words it saw: %s"
        % (name, path, sorted(found)))


def test_the_corpus_cannot_be_quietly_emptied():
    """A gate keeps its teeth only while somebody keeps writing known-bads.

    The counts are a floor, not a target: they exist so that deleting cases
    shows up as a red instead of as a shorter file nobody reads.
    """
    assert len(english.KNOWN_BAD) >= 10, len(english.KNOWN_BAD)
    assert len(english.MUST_NOT_FIRE) >= 6, len(english.MUST_NOT_FIRE)


def test_this_repository_is_silent_under_its_own_gate():
    """The condition the translation existed to reach, asserted rather than hoped.

    ⛔ A GATE BORN RED ON WHAT ALREADY EXISTS TEACHES PEOPLE TO BYPASS IT, and
    this project has written that down twice. That is why the translation came
    first and this assertion second, and not the other way round.
    """
    config = english.config_for(_ROOT)
    guilty = english.scan(_ROOT, english.tracked(_ROOT), config)
    assert not guilty, (
        "%d tracked file(s) are not in English:\n  " % len(guilty)
        + "\n  ".join("%s: %s" % (p, ", ".join(w[:5])) for p, w in guilty))


def test_the_green_says_what_it_looked_at():
    """A perimeter is a property of a gate; a perimeter inferred from its green
    is a hope. Measured once when the extension list silently missed `.js`,
    `.css` and `.html` while the front end was 67 KB of script."""
    config = english.config_for(_ROOT)
    covered, uncovered = english.coverage(_ROOT, config)
    assert covered, "the gate covers no file at all, which cannot be right"
    assert len(covered) + len(uncovered) == len(english.tracked(_ROOT))
    # Everything left outside has to be something with no prose in it.
    for rel in uncovered:
        assert not rel.endswith(config.extensions), rel


def test_an_exclusion_that_names_nothing_is_reported(tmp_path):
    """⛔ THE CLASS THAT LET TWO COPIES DRIFT WITHOUT EITHER GOING RED.

    An exclusion is a hole cut in a gate on purpose. A dead one never makes
    anything red, so nobody finds it - and measured 2026-09-15, FOUR of the five
    entries in AIHawk's copy named paths that exist only in the wrapper, carried
    across by the copy itself. One of them, the vendored Node driver, is dead in
    both since Node was removed.
    """
    (tmp_path / "real.py").write_text("x = 1\n", encoding="utf-8")
    config = english.Config(excluded=("real.py", "gone/", "also_gone.py"))
    dead = english.dead_exclusions(tmp_path, config)
    assert dead == ["gone/", "also_gone.py"], dead


def test_an_exclusion_that_names_something_is_not_reported(tmp_path):
    """The must-not-fire half: a live hole is a decision, not a defect."""
    (tmp_path / "vendored").mkdir()
    (tmp_path / "one.py").write_text("x = 1\n", encoding="utf-8")
    config = english.Config(excluded=("vendored/", "one.py"))
    assert english.dead_exclusions(tmp_path, config) == []


def test_the_self_exemption_is_one_exact_path_and_not_a_prefix():
    """⛔ A PREFIX WOULD QUIETLY STOP COVERING THE WHOLE PACKAGE.

    This module exempts itself because it carries the Italian corpus. If that
    exemption were `src/invisible_core/` the gate would go silent over every
    shipped module at once, and it would look exactly the same from outside.
    """
    silent, _ = english.inspect(english._SELF, english._ITA,
                                english.CORPUS_CONFIG)
    assert not silent, "the module must not accuse its own corpus"

    sibling = english._SELF.replace("english.py", "prefs.py")
    loud, _ = english.inspect(sibling, english._ITA, english.CORPUS_CONFIG)
    assert loud, (
        "%s is exempt too, so the exemption is acting as a prefix and the whole "
        "package is unwatched" % sibling)


def test_a_repository_that_declares_nothing_excludes_nothing(tmp_path):
    """An absent declaration must mean "scan everything", never "scan nothing".

    The opposite default is the shape of a gate that turns itself off on a fresh
    checkout, which is how a repository ends up unwatched without anyone
    choosing it - the same failure as the copy that was never made here.
    """
    config = english.config_for(tmp_path)
    assert config.excluded == ()
    assert config.marker is None
    assert config.extensions == english.EXTENSIONS


def test_the_declaration_is_read_from_the_repository_being_judged(tmp_path):
    """Per-repo data comes from the repo, which is the half that must not be
    shared: copying it is what gave one repository the other's vendored
    folders."""
    (tmp_path / "pyproject.toml").write_text(
        "\n".join([
            "[tool.invisible.english]",
            'excluded = ["vendored/"]',
            'marker = "PATCHED by us"',
        ]) + "\n", encoding="utf-8")
    config = english.config_for(tmp_path)
    assert config.excluded == ("vendored/",)
    assert config.marker == "PATCHED by us"


def test_a_vendored_folder_without_a_marker_is_simply_skipped():
    """A repository can vendor code and patch none of it. Without a marker there
    is nothing of ours in there to read, and inventing one would make the gate
    read somebody else's prose."""
    config = english.Config(excluded=("vendored/",), marker=None)
    is_italian, _ = english.inspect("vendored/x.py", english._ITA, config)
    assert not is_italian


def test_the_root_is_an_argument_so_there_is_nothing_to_refuse():
    """⛔ THE PATCH THAT IS NOT PORTED, and why it is not.

    The script version derived its root from `__file__`, so it could only judge
    its own repository while looking like it judged whatever you pointed it at.
    It grew a guard that REFUSED when the two disagreed - a correct patch on a
    wrong shape. Here the tree is a parameter, so a cross-repo run is ordinary
    and correct rather than something to catch.
    """
    other = pathlib.Path(__file__).resolve().parents[1]
    assert english.scan(other, [], english.Config()) == []
    # And the module names its subject in the green, so a right answer to the
    # wrong question cannot be mistaken for a right answer.
    r = subprocess.run([sys.executable, "-m", "invisible_core.english",
                        "--root", str(other)],
                       capture_output=True, text=True, cwd=str(other))
    assert r.returncode == 0, r.stdout + r.stderr
    assert str(other) in r.stdout, r.stdout


def test_the_selftest_is_runnable_as_a_command():
    """The hook runs it as a command, so the command is what has to work."""
    r = subprocess.run([sys.executable, "-m", "invisible_core.english",
                        "--selftest"], capture_output=True, text=True,
                       cwd=str(_ROOT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL GOOD" in r.stdout, r.stdout
