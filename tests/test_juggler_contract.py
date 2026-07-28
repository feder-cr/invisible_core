"""The juggler provenance contract exists twice. This is what compares them.

WHAT THE CONTRACT IS. Four file paths inside a built engine, two marker byte
strings that must appear in them, and the directory those paths live under.
Together they answer "is this a build of OUR patched Firefox, or a stock one?" -
the question that decides whether an engine is sealed, whether an archive may be
released, and whether a cached tree may be launched.

WHY IT EXISTS TWICE. The runtime copy is `invisible_core.seal`, in a published
package. The producer copy is `scripts/validate_release.py` in the SOURCE repo,
which cannot import the core: it runs inside a Firefox build checkout, before any
of this is installed, and making a release script depend on a package from the
index would invert the dependency that release is trying to produce.

So two copies is the right answer here, and the wrong part was that **nothing
compared them.** `18-gate-inventory.md` A1 recorded them as byte-identical,
compared by hand on 2026-07-25, "with nothing gating that" - and open item 4 said
the same. A hand comparison dated three days ago is a fact about that afternoon.

WHAT DRIFT WOULD COST. The publish side requires 4/4 marked entries and the
runtime tolerates 2/4, each reading its own tuple. A juggler restructure that
renames one of the four paths makes the producer refuse a good build, or - worse
in the other direction - lets the runtime accept a tree whose provenance was
never actually established, because two of the four paths it looks for no longer
exist and it only needs two.

The source repo is not always present, so this skips outside the workbench. The
producer copy is PARSED, never imported: that file lives in a Firefox checkout
with its own imports and running it here would prove nothing about it.
"""
from __future__ import annotations

import ast
import os
import pathlib

import pytest

from invisible_core import seal

pytestmark = pytest.mark.unit

_FF_SRC = pathlib.Path(os.environ.get("STEALTH_FIREFOX_SRC", "C:/ff/source"))
_PRODUCER = _FF_SRC / "scripts" / "validate_release.py"

#: The names that must agree. `JUGGLER_MIN_MARKED` is deliberately NOT here: the
#: producer requires all four and the runtime two, which is the asymmetry the
#: test below pins on purpose rather than the drift it refuses.
_SHARED = ("JUGGLER_ENTRIES", "JUGGLER_DIR_REL", "JUGGLER_MARKERS")


def _module_constants(path: pathlib.Path, names) -> dict:
    """Read module-level assignments by parsing. No import, no execution."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in names:
                try:
                    found[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pytest.fail(
                        f"{path.name}: {target.id} is not a literal any more, so "
                        f"this comparison cannot see it. If it became computed, "
                        f"compare the computed value instead of deleting this test")
    return found


@pytest.mark.skipif(not _PRODUCER.is_file(),
                    reason="no Firefox source checkout beside this one - set "
                           "STEALTH_FIREFOX_SRC to point at one")
def test_the_two_copies_of_the_juggler_contract_agree():
    ours = {name: getattr(seal, name) for name in _SHARED}
    theirs = _module_constants(_PRODUCER, set(_SHARED))

    absent = sorted(set(_SHARED) - set(theirs))
    assert not absent, (
        f"{_PRODUCER} no longer defines {absent}. Either it started importing "
        f"them from somewhere - in which case say so here and compare that - or "
        f"the producer has stopped checking juggler provenance at all")

    differing = {n: (ours[n], theirs[n]) for n in _SHARED if ours[n] != theirs[n]}
    assert not differing, (
        "the runtime and the producer disagree about what proves a build is "
        f"ours:\n  " +
        "\n  ".join(f"{n}: core={o!r} producer={t!r}" for n, (o, t) in differing.items()) +
        f"\n\nOne of them will refuse a good build or accept an unproven tree. "
        f"Both copies exist on purpose ({_PRODUCER.name} runs inside a Firefox "
        f"checkout and cannot import this package); keeping them equal is what "
        f"was missing.")


def test_the_two_thresholds_are_different_on_purpose_and_stay_where_they_are():
    """Not drift - a deliberate asymmetry, pinned so a change is a decision.

    The producer requires 4 of 4 marked entries, because it is looking at an
    archive it has just built and anything less is an eroding build. The runtime
    accepts 2 of 4, because it is looking at a user's extracted tree and a
    stricter rule there turns a cosmetic upstream change into a launch refusal on
    every machine.

    `make_seal.py` is a third value, 0/4 - it refuses only a completely unmarked
    build - and that one is documented nowhere but `18-gate-inventory.md` D1. It
    is not asserted here because it lives in the source repo's own test.
    """
    assert seal.JUGGLER_MIN_MARKED == 2, (
        f"the runtime now needs {seal.JUGGLER_MIN_MARKED} of "
        f"{len(seal.JUGGLER_ENTRIES)} marked entries. Raising it turns an "
        f"upstream rename into a launch refusal for every user; lowering it to 1 "
        f"means a single string decides provenance")
    assert len(seal.JUGGLER_ENTRIES) == 4, len(seal.JUGGLER_ENTRIES)
    assert seal.JUGGLER_MIN_MARKED < len(seal.JUGGLER_ENTRIES), (
        "the runtime threshold reached the producer's, so the asymmetry this "
        "test describes is gone. If that was deliberate, rewrite this test - the "
        "reason it existed is in its docstring")


@pytest.mark.skipif(not _PRODUCER.is_file(), reason="no Firefox source checkout")
def test_the_comparison_would_notice_a_changed_path():
    """Its known-bad input: the check must fail on a difference it is handed.

    Without this, a `_module_constants` that quietly returned `{}` for everything
    would make the comparison above pass on any two files - the empty-set shape
    that has been the recurring defect in this codebase's gates.
    """
    theirs = _module_constants(_PRODUCER, set(_SHARED))
    assert theirs, "parsed nothing out of the producer; every comparison is vacuous"
    tampered = dict(theirs)
    tampered["JUGGLER_DIR_REL"] = "chrome/juggler-renamed"
    differing = {n for n in _SHARED if getattr(seal, n) != tampered[n]}
    assert differing == {"JUGGLER_DIR_REL"}, differing
