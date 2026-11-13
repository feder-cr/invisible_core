"""A key is sent at its planned time from the start of the action, not at "the previous reply plus the gap".

THE KNOWN-BAD, measured 2026-11-13 on GitHub runners: the page saw each key 0 ms after it was sent, and the last key of a 19-character
address was sent 575 ms (Linux) / 681 ms (Windows) after its plan alone, 1114 / 1518 ms with four workers; stock Playwright 96 / 134 and
97 / 140. The engine holds the reply of a key event for its `latency` (PageAgent `_dispatchKeyEvent`: `await setTimeout(latency)`), and the
core slept the plan after each reply, so the slip was the sum of the latencies (458 ms for seed 2) plus every round trip.

No browser starts here: the real `Keyboard`, the real plan, a clock that only moves when something sleeps or the fake engine answers, and a
connection whose reply takes the event's own `latency` plus a round trip the test picks.

Known-bad, run before this file was trusted: `Keyboard.type` as of core 63.68.0 (sleeps after the reply) fails the first test at every
round trip, with a slip at the last key equal to the sum of the latencies and the round trips.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler import keyboard as kb
from invisible_core.juggler._behaviour import InputLatency, PageActs, TypingPersona, plan_typing

pytestmark = pytest.mark.unit

TEXT = "someone@example.com"
SEED = 2


class _Clock:
    """`time` for the keyboard: `monotonic` and `sleep` over a number the test owns."""

    def __init__(self) -> None:
        self.t = 1000.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0.0, "a negative sleep"
        self.t += seconds


class _Engine:
    """A connection that answers a key event after the engine's hold (`latency`) and `rtt_ms` of round trip, and records when each was sent."""

    def __init__(self, clock: _Clock, rtt_ms: float, late=None) -> None:
        self.clock = clock
        self.rtt_ms = rtt_ms
        self.late = late or {}      # index of the command -> extra ms before its reply
        self.sent = []              # (seconds, type, key)
        self.replied = []           # seconds, when each reply came back

    def send(self, method, params, session=None, timeout=None):
        assert method == "Page.dispatchKeyEvent"
        index = len(self.sent)
        self.sent.append((self.clock.t, params["type"], params["key"]))
        self.clock.t += (params.get("latency", 0.0) + self.rtt_ms + self.late.get(index, 0.0)) / 1000.0
        self.replied.append(self.clock.t)
        return {}


def _type(monkeypatch, rtt_ms, late=None):
    clock = _Clock()
    monkeypatch.setattr(kb, "time", clock)
    engine = _Engine(clock, rtt_ms, late)
    board = kb.Keyboard(engine, "s", TypingPersona.from_seed(SEED), acts=PageActs(0), latency=InputLatency.from_seed(SEED))
    start = clock.t
    board.type(TEXT)
    return engine, start


def _plan_times():
    """The planned keydown time of every character (ms from the start): the same plan the keyboard draws first on page 0."""
    plan = plan_typing(TEXT, TypingPersona.from_seed(SEED), nonce=PageActs(0).next("typing"))
    out, t = [], 0.0
    for dwell, gap in plan:
        out.append(t)
        t += dwell + gap
    return out, plan


def _keydowns(engine, start):
    return [(t - start) * 1000.0 for t, kind, key in engine.sent if kind == "keydown" and key != "Shift"]


@pytest.mark.parametrize("rtt_ms", [0.0, 5.0, 60.0])
def test_every_key_is_sent_within_2_ms_of_the_plan(monkeypatch, rtt_ms):
    engine, start = _type(monkeypatch, rtt_ms)
    planned, _ = _plan_times()
    sent = _keydowns(engine, start)
    assert len(sent) == len(planned) == len(TEXT)
    # A key can only be later than its plan by the time the pipe is not free: the command before it was still waiting for its reply (with a
    # 60 ms round trip a keydown and a keyup cost 2 x (60 + latency), more than the shortest `dwell + gap` of the plan). Then it goes the
    # moment that reply is in, and not a millisecond after.
    for i, (s, p) in enumerate(zip(sent, planned)):
        if s - p <= 2.0:
            continue
        previous_reply = [(t - start) * 1000.0 for (t, kind, key), t in zip(engine.sent, engine.replied)
                          if (t - start) * 1000.0 <= s + 0.5]
        free_at = max(previous_reply)
        assert s <= free_at + 2.0 and abs(s - p) <= free_at - p + 2.0, "key %d is %.1f ms late with the pipe free" % (i, s - p)
    assert abs(sent[-1] - planned[-1]) <= 2.0, "the last key is %.1f ms off the plan" % (sent[-1] - planned[-1])
    if rtt_ms <= 5.0:
        assert max(abs(s - p) for s, p in zip(sent, planned)) <= 2.0


@pytest.mark.parametrize("rtt_ms", [0.0, 5.0, 60.0])
def test_a_key_is_held_for_its_dwell_from_the_moment_it_was_sent(monkeypatch, rtt_ms):
    engine, _start = _type(monkeypatch, rtt_ms)
    _, plan = _plan_times()
    downs = [t for t, kind, key in engine.sent if kind == "keydown" and key != "Shift"]
    ups = [t for t, kind, key in engine.sent if kind == "keyup" and key != "Shift"]
    for (dwell, _gap), down, up in zip(plan, downs, ups):
        held = (up - down) * 1000.0
        assert held >= dwell - 0.5, "held %.1f ms, planned %.1f" % (held, dwell)
        # not longer than the plan unless the reply of the keydown itself came later than the dwell
        assert held <= max(dwell, 11.0 + 30.0 + rtt_ms) + 2.0


def test_a_reply_later_than_the_next_deadline_keeps_the_order_and_the_rest_on_plan(monkeypatch):
    """The reply of the 5th command (the keydown of the third key) comes 1.5 s late, far past the next deadlines."""
    engine, start = _type(monkeypatch, 5.0, late={4: 1500.0})
    planned, plan = _plan_times()
    sent = _keydowns(engine, start)
    kinds = [(kind, key) for _t, kind, key in engine.sent if key != "Shift"]
    expected = []
    for ch in TEXT:
        expected += [("keydown", ch), ("keyup", ch)]
    assert kinds == expected, "a key was skipped, doubled or swapped"
    times = [t for t, _kind, _key in engine.sent]
    assert times == sorted(times), "events out of order in time"
    # The stalled reply costs the keys inside its stall and no more: after it every key is back on its own planned time.
    stalled = [i for i, s in enumerate(sent) if s - planned[i] > 2.0]
    assert stalled, "the stall did not reach the plan: the test is not testing"
    for i in range(max(stalled) + 1, len(TEXT)):
        assert abs(sent[i] - planned[i]) <= 2.0, "key %d is %.1f ms off after the stall" % (i, sent[i] - planned[i])
    assert max(stalled) < len(TEXT) - 3, "the stall is still shifting the end of the text"
    # No burst: a late key is never closer to the release before it than the floor (or the plan's own spacing, if smaller).
    keydowns = [t for t, kind, key in engine.sent if kind == "keydown" and key != "Shift"]
    keyups = [t for t, kind, key in engine.sent if kind == "keyup" and key != "Shift"]
    for i in range(1, len(TEXT)):
        spacing = planned[i] - (planned[i - 1] + plan[i - 1][0])
        floor = min(max(spacing, 0.0), kb.MIN_KEY_SPACING_MS)
        assert (keydowns[i] - keyups[i - 1]) * 1000.0 >= floor - 0.5, "key %d is a burst after the stall" % i


def test_the_same_seed_is_sent_at_the_same_times_twice(monkeypatch):
    first = _type(monkeypatch, 5.0)
    second = _type(monkeypatch, 5.0)
    a = [(round((t - first[1]) * 1000.0, 6), kind, key) for t, kind, key in first[0].sent]
    b = [(round((t - second[1]) * 1000.0, 6), kind, key) for t, kind, key in second[0].sent]
    assert a == b
