"""The arithmetic that turns a seed into a number is the same on every supported Python.

Found by a CI job, not by a person: the wrapper's golden pointer path passed on 3.12, 3.13 and
3.14 and failed on 3.11, one bit off in a coordinate. Two builtins were the cause (see
``invisible_core._floats``): ``sum()`` of floats is compensated from 3.12 and is not on 3.11, and
``math.log2()`` is not the correctly rounded value for about 1 float in 20 on 3.11.17.

These tests state the answer, not the interpreter: each helper must equal the exactly rounded
value, computed here with ``fractions`` and with ``decimal`` at a higher precision than the
helper uses. That is true on every version, so the file runs the same on the whole matrix, and
it fails on the builtins wherever the builtins are wrong.
"""
from __future__ import annotations

import math
import random
import re
from decimal import Context, Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from invisible_core import _floats
from invisible_core.juggler import _motion

pytestmark = pytest.mark.unit

SRC = Path(_floats.__file__).resolve().parent


def _exact_log2(x: float) -> float:
    ctx = Context(prec=120)
    return float(ctx.divide(ctx.ln(Decimal(x)), ctx.ln(Decimal(2))))


def _step_lists():
    """What the sampler adds up: log-normal durations, many sizes, in the tens to the 160."""
    rng = random.Random(20261110)
    for n in (2, 3, 7, 30, 61, 160):
        for sigma in (0.1, 0.35, 1.2):
            for _ in range(8):
                yield [math.exp(rng.gauss(0.0, sigma)) for _ in range(n)]


# ── the sum ────────────────────────────────────────────────────────────────

def test_a_sum_of_floats_is_the_exactly_rounded_one():
    wrong = []
    for values in _step_lists():
        reference = float(sum(Fraction(v) for v in values))
        if _floats.exact_sum(values) != reference:
            wrong.append(len(values))
    assert not wrong, "exact_sum differs from the exact sum on lists of length %r" % wrong


def test_the_builtin_sum_is_what_this_replaces():
    """The reason the helper exists, kept as a fact: on a list where the terms differ in size
    the builtin can be a bit off (3.11 always adds left to right), and exact_sum is not."""
    values = [1e9] + [1e-7] * 59
    reference = float(sum(Fraction(v) for v in values))
    assert _floats.exact_sum(values) == reference
    assert _floats.exact_sum([0.1] * 10) == 1.0


def test_a_sum_of_counts_stays_an_integer():
    assert _floats.exact_sum([True, False, True]) == 2
    assert type(_floats.exact_sum([True, False, True])) is int
    assert type(_floats.exact_sum([])) is int
    assert type(_floats.exact_sum(iter([1, 2, 3]))) is int


def test_the_pointer_sampler_adds_its_steps_exactly():
    """_sample_times divides by the TOTAL of its steps, so a last-bit difference in the total
    is a last-bit difference in every time and in every coordinate after it. The 61 steps
    below (the draws of seed 2) add to ...811 left to right and to ...812 exactly: this failed
    on 3.11 with the builtin sum."""
    st = _motion.style_for_seed(5)
    n = 61
    duration = n * st.step_ms

    class Replay:
        def __init__(self):
            self.draws = random.Random(2)

        def gauss(self, mu, sigma):
            return self.draws.gauss(0.0, 0.3)

    times = _motion._sample_times(Replay(), duration, st)
    draws = random.Random(2)
    incs = [math.exp(draws.gauss(0.0, 0.3)) for _ in range(n)]
    total = float(sum(Fraction(v) for v in incs))
    acc, expected = 0.0, [0.0]
    for c in incs:
        acc += c
        expected.append(duration * acc / total)
    expected[-1] = duration
    kept = [expected[0]]
    for tm in expected[1:-1]:
        if tm - kept[-1] >= _motion.SAMPLE_FLOOR_MS:
            kept.append(tm)
    if len(kept) > 1 and duration - kept[-1] < _motion.SAMPLE_FLOOR_MS:
        kept.pop()
    kept.append(duration)
    assert times == kept


# ── the logarithm ──────────────────────────────────────────────────────────

def test_log2_is_the_correctly_rounded_one_on_the_values_the_planner_asks_for():
    """Fitts' law asks log2(distance / width + 1): 1 to a few hundred. On 3.11.17 138 of 3000
    of these come back one bit off; this must be the exact value for every one."""
    rng = random.Random(7)
    xs = [abs(rng.uniform(-300, 300)) + 1.0 for _ in range(3000)]
    wrong = [x for x in xs if _floats.log2(x) != _exact_log2(x)]
    assert not wrong, "%d of %d are not correctly rounded, the first is %r" % (len(wrong), len(xs), wrong[0])


def test_log2_of_a_power_of_two_is_the_integer():
    for k in range(-1000, 1001):
        assert _floats.log2(2.0 ** k) == float(k)
    assert _floats.log2(1.0) == 0.0


def test_log2_outside_its_domain_answers_as_math_log2_does():
    for bad in (0.0, -1.0, -0.0):
        with pytest.raises(ValueError):
            _floats.log2(bad)
    assert _floats.log2(math.inf) == math.inf
    assert math.isnan(_floats.log2(math.nan))


def test_the_movement_duration_uses_the_correctly_rounded_log2():
    """_movement_duration on the distances a page produces: the Fitts term is the exact one.
    Failed on 3.11.17, where the builtin is one bit off for 1 distance in 20."""
    st = _motion.style_for_seed(9)
    rng = random.Random(3)
    wrong = []
    for k in range(400):
        dist = rng.uniform(5.0, 1500.0)
        axis = _motion._Axis.between(0.0, 0.0, dist, 0.0)
        width = rng.uniform(4.0, 120.0)
        got = _motion._movement_duration(random.Random(k), axis, st, width)
        bits = _exact_log2(axis.dist / width + 1.0)
        want = _motion._clamp((st.fitts_a_ms + st.fitts_b_ms * bits)
                              * math.exp(random.Random(k).gauss(0.0, st.dur_jitter)),
                              _motion.MIN_DURATION_MS, _motion.MAX_DURATION_MS)
        if got != want:
            wrong.append((dist, width))
    assert not wrong, "%d of 400 durations are not the exact Fitts value, first %r" % (len(wrong), wrong[0])


# ── nothing is left on the builtins ────────────────────────────────────────

#: The files whose numbers come from a seed. A new `math.log2(` or a bare `sum(` of floats in
#: one of them brings the defect back, and the failure would be a CI job on one Python.
SEEDED = ("juggler/_motion.py", "juggler/_behaviour.py", "juggler/actions.py",
          "juggler/keyboard.py", "_fpforge/_network.py", "_locale.py", "_webgl_personas.py")


@pytest.mark.parametrize("rel", SEEDED)
def test_a_seeded_file_calls_neither_math_log2_nor_the_builtin_sum(rel):
    text = (SRC / rel).read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"#.*", "", line) for line in text.splitlines())
    assert "math.log2(" not in code, "%s calls math.log2: use invisible_core._floats.log2" % rel
    bare = re.findall(r"(?<![\w.])sum\(", code)
    assert not bare, "%s calls the builtin sum(): use invisible_core._floats.exact_sum" % rel
