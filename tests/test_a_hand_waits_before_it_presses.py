"""Between the pointer arriving and the button going down, a hand waits (B271).

⛔ THE KNOWN-BAD, measured on 2026-10-10 against a person on Firefox 151 on
Windows doing the same login form: fourteen clicks, 46-1063 ms between the last
mousemove and the mousedown, median about 180 ms. The product pressed 8-16 ms
after arriving - one protocol round trip - on 0.29.0, 0.30.0 and 0.31.0.
"""
from __future__ import annotations

import dataclasses
import statistics

import pytest

from invisible_core.juggler import actions as actions_module
from invisible_core.juggler._behaviour import (
    PageActs, PointerPersona, plan_click, plan_press_settle,
)
from invisible_core.juggler.actions import Actions

pytestmark = pytest.mark.unit


def test_the_pause_has_the_shape_of_the_measured_hand():
    """Across seeds and draws: no round-trip-sized pause, a median in the
    band a person produced, and a tail - one person paused over a second."""
    draws = [plan_press_settle(PointerPersona.from_seed(s), nonce=n)
             for s in range(40) for n in range(25)]
    assert min(draws) > 15.0, "a pause the size of a protocol round trip"
    assert 120.0 <= statistics.median(draws) <= 280.0, statistics.median(draws)
    assert max(draws) > 600.0, "no tail: every press equally prompt"


def test_the_existing_hand_of_a_seed_does_not_move():
    """Appended fields, own stream: pinned from `origin/main` on 2026-10-10."""
    p = PointerPersona.from_seed(4242)
    got = {k: round(v, 6) for k, v in dataclasses.asdict(p).items()
           if isinstance(v, float) and not k.startswith("press_settle")}
    assert got == {
        'sample_ms': 19.196257, 'fitts_a_ms': 153.487839, 'fitts_b_ms': 133.859506,
        'mt_sigma': 0.258474, 'peak_frac': 0.444423, 'curvature': 0.028779,
        'tremor_px': 1.394632, 'drift_px': 16.160688, 'reposition_px': 357.792079,
        'fidget_scale': 0.911775, 'pause_median_ms': 1643.74295, 'pause_sigma': 0.894945,
        'overshoot_bias': 1.205418, 'aimless_rate': 0.328338,
        'click_dwell_median_ms': 72.743081, 'click_dwell_sigma': 0.384486,
        'dblclick_gap_median_ms': 96.965709}
    assert [(round(a, 6), round(b, 6)) for a, b in plan_click(p, 2, nonce=5)] == [
        (155.713251, 66.45212), (91.186339, 0.0)]


class _Fake:
    def __init__(self, clock):
        self.clock = clock
        self.sent = []

    def send(self, method, params, **kw):
        self.sent.append((self.clock[0], params.get("type")))
        return {}


def _actions(monkeypatch, persona):
    clock = [0.0]
    monkeypatch.setattr(actions_module.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    c = _Fake(clock)
    a = Actions(c, "S", None, None, acts=PageActs(1))
    a.pointer_persona = persona
    a.engine_approach = False
    a._mouse_event = lambda typ, point, **kw: c.sent.append((clock[0], typ))
    return a, c


def test_click_at_waits_after_the_approach_and_before_the_press(monkeypatch):
    a, c = _actions(monkeypatch, PointerPersona.from_seed(7))
    a.click_at(100, 100)
    types = [t for _, t in c.sent]
    assert types == ["mousemove", "mousedown", "mouseup"], types
    arrived, pressed = c.sent[0][0], c.sent[1][0]
    assert pressed - arrived >= 0.015, "pressed as soon as it arrived"


def test_without_a_hand_there_is_no_pause(monkeypatch):
    a, c = _actions(monkeypatch, None)
    a.click_at(100, 100)
    assert c.sent[1][0] - c.sent[0][0] == 0.0
