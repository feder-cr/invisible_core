"""One seed mixer in the package, and it answers what the three copies answered.

Until 38.34.0 the FNV-1a mix that turns a session seed into tagged streams
existed three times: `_cookies._sub_seed` here, and a byte-identical
`_sub_seed` plus a 64-bit-seeded, int31-reduced `_mix` in the Playwright
wrapper's pointer planner and motion generator. That wrapper counted them with
a test and checked they agreed. When the Juggler client moved into this package
(decision D85) the three met in one place and became `seedmix`; the census
moved here with them, and it now expects ONE.

The digests below were taken from the three copies BEFORE the merge, over the
corpus the wrapper's test used plus `server:drag`. They are what every shipped
persona's cookies and every seed's pointer paths are made of, so a change that
moves them is a change to every user's fingerprint, whatever else it fixes.
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import random

import pytest

import invisible_core
from invisible_core.seedmix import fnv1a, sub_seed

pytestmark = pytest.mark.unit

_PKG = pathlib.Path(invisible_core.__file__).resolve().parent
_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3

# Zero, negative, above 2**32 (where the old seed masks differed) and the int31
# ceiling, then three hundred seeds across 2**48.
_SEEDS = [0, 1, -1, -7, 42, 2**31 - 1, 2**31, 2**32 - 1, 2**32,
          2**40, 10**18, -(2**31), -(2**40)]
_SEEDS += [random.Random(917).randrange(-(2**48), 2**48) for _ in range(300)]
_TAGS = ["motion:style", "motion:move:0", "motion:move:137", "pointer-persona",
         "idle:0", "scroll:7", "approach:12", "aimless:3", "session-gate",
         "pointer-origin", "google", "dom:example", "server:drag", "", "x" * 64]

#: `_cookies._sub_seed` and `_behaviour._sub_seed` (identical) before the merge.
_SUB_SEED_DIGEST = "5a4f44013912132e63518b89d16b17a99281b139921b6159afd280dc3badfef0"
#: `_motion._mix` before the merge.
_MOTION_DIGEST = "6b5ee1409726b96fda91be9ec8838b4fa13601e924585aa35ab0cbacb2236524"


def _digest(fn) -> str:
    h = hashlib.sha256()
    for seed in _SEEDS:
        for tag in _TAGS:
            h.update(b"%d|" % fn(seed, tag))
    return h.hexdigest()


def _places_with_the_fnv_constants() -> set:
    """Every module (and function) that spells out the FNV-1a constants."""
    found = set()
    for path in sorted(_PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(_PKG).with_suffix("").as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in (_FNV_OFFSET, _FNV_PRIME) \
                    and not isinstance(node.value, bool):
                found.add(rel)
    return found


def test_the_mix_is_spelled_out_in_one_module():
    """A second copy must be a decision, not a surprise - including one pasted
    into a module that has nothing to do with seeds."""
    assert _places_with_the_fnv_constants() == {"seedmix"}, (
        "the FNV-1a constants appear outside `seedmix`: that is a copy of the "
        "seed mixer. Import `invisible_core.seedmix.sub_seed` (or `fnv1a`) instead.")


def test_sub_seed_answers_what_the_two_sub_seed_copies_answered():
    assert _digest(sub_seed) == _SUB_SEED_DIGEST


def test_the_motion_reduction_answers_what_mix_answered():
    """`_mix` masked the seed to 64 bits and reduced to int31; `fnv1a` masks to
    32. Same answer, because the low 31 bits of an FNV-1a product depend only
    on the low 31 bits of its operands - which is what lets one function serve
    both, and what this digest proves for the corpus."""
    assert _digest(lambda s, t: fnv1a(s, t) & 0x7FFFFFFF) == _MOTION_DIGEST


def test_the_three_former_owners_draw_from_seedmix(monkeypatch):
    """The digests above are worth something only if the code that seeds the
    streams calls the function they pin: the cookies, the pointer persona and
    the motion style each ask `seedmix` for their tag."""
    import invisible_core._cookies as cookies
    import invisible_core.juggler._behaviour as behaviour
    import invisible_core.juggler._motion as motion

    asked = []

    def spy(real):
        def wrapper(seed, tag):
            asked.append(tag)
            return real(seed, tag)
        return wrapper

    monkeypatch.setattr(cookies, "sub_seed", spy(sub_seed))
    monkeypatch.setattr(behaviour, "sub_seed", spy(sub_seed))
    monkeypatch.setattr(motion, "fnv1a", spy(fnv1a))

    from types import SimpleNamespace
    persona = SimpleNamespace(seed=42, browsing_history=[
        {"name": "example.com", "category": "dev", "cookie_profile": "minimal"}])
    cookies.persona_cookies(persona, "en-US", now=1_700_000_000)
    behaviour.PointerPersona.from_seed(42)
    motion.style_for_seed(42)
    assert {"google", "dom:example.com", "pointer-persona:0", "motion:style"} <= set(asked), asked


def test_a_stream_seed_is_never_zero():
    """A zero seed would collapse two tags onto one stream; `sub_seed` falls
    back to a constant where the raw mix could be zero."""
    assert sub_seed(0, "") != 0
    for seed in _SEEDS[:60]:
        for tag in _TAGS:
            assert 0 < sub_seed(seed, tag) < 2**64
            assert 0 <= fnv1a(seed, tag) & 0x7FFFFFFF < 2**31
