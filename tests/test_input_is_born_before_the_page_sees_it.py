"""Every key and mouse event carries the latency the session's machine adds
between its birth and the page's handler ([B299]).

Measured on retail Windows Firefox 151 with a real keyboard and mouse: a
handler reads `event.timeStamp` 3 to 18 ms below its `performance.now()`.
Ours were equal, born at their dispatch, and a tracker that keeps only events
from the past dropped 25 keys of 30. The core draws the number per event from
the session seed (`InputLatency`) and the engine takes it from firefox-45.
"""
from __future__ import annotations

import statistics

import pytest

from invisible_core.juggler._behaviour import InputLatency, PageActs
from invisible_core.juggler.actions import Actions
from invisible_core.juggler.keyboard import Keyboard

pytestmark = pytest.mark.unit


class _Fake:
    def __init__(self):
        self.sent = []

    def send(self, method, params, **kw):
        self.sent.append((method, dict(params)))
        return {}


def test_one_seed_is_one_machine_and_two_seeds_are_two():
    assert InputLatency.from_seed(7) == InputLatency.from_seed(7)
    assert InputLatency.from_seed(7) != InputLatency.from_seed(8)
    assert InputLatency.from_seed(7).draw(3) == InputLatency.from_seed(7).draw(3)


@pytest.mark.parametrize("seed", [1, 42, 4300, 987654])
def test_the_draws_are_the_retail_range(seed):
    latency = InputLatency.from_seed(seed)
    draws = [latency.draw(n) for n in range(2000)]
    assert min(draws) >= 2.0 and max(draws) <= 30.0
    inside = sum(3.0 <= d <= 19.0 for d in draws) / len(draws)
    assert inside > 0.85, inside
    assert 5.0 < statistics.median(draws) < 12.0


def test_every_key_event_carries_a_latency_when_the_session_has_one():
    c = _Fake()
    kb = Keyboard(c, "S", acts=PageActs(1), latency=InputLatency.from_seed(42))
    kb.press("a", dwell_ms=0)
    keys = [p for m, p in c.sent if m == "Page.dispatchKeyEvent"]
    assert [p["type"] for p in keys] == ["keydown", "keyup"]
    assert all(2.0 <= p["latency"] <= 30.0 for p in keys), keys
    assert keys[0]["latency"] != keys[1]["latency"]


def test_without_a_latency_no_key_event_carries_one():
    c = _Fake()
    kb = Keyboard(c, "S", acts=PageActs(1))
    kb.press("a", dwell_ms=0)
    assert all("latency" not in p for m, p in c.sent)


def test_every_mouse_event_carries_a_latency_when_humanising_is_on():
    c = _Fake()
    actions = Actions(c, "S", None, None, acts=PageActs(1), session_seed=42)
    actions._mouse_event("mousemove", (10, 20))
    actions._mouse_event("mousedown", (10, 20), button=0, buttons=1,
                         click_count=1)
    mouse = [p for m, p in c.sent if m == "Page.dispatchMouseEvent"]
    assert len(mouse) == 2
    assert all(2.0 <= p["latency"] <= 30.0 for p in mouse), mouse
    # The keyboard of the same page draws from the same machine.
    assert actions.keyboard.latency == actions.input_latency


def test_with_humanising_off_no_mouse_event_carries_one():
    c = _Fake()
    actions = Actions(c, "S", None, None, acts=PageActs(1))
    actions._mouse_event("mousemove", (10, 20))
    assert all("latency" not in p for m, p in c.sent)
