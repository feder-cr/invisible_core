"""A key is sent at its planned time from the start of the action, not at "the previous reply plus the gap".

THE KNOWN-BAD, measured 2026-11-13 on GitHub runners: the page saw each key 0 ms after it was sent, and the last key of a 19-character
address was sent 575 ms (Linux) / 681 ms (Windows) after its plan alone, 1114 / 1518 ms with four workers; stock Playwright 96 / 134 and
97 / 140. The engine holds the reply of a key event for its `latency` (PageAgent `_dispatchKeyEvent`: `await setTimeout(latency)`), and the
core slept the plan after each reply, so the slip was the sum of the latencies (458 ms for seed 2) plus every round trip.

Then, measured with a real browser (run 43481977643, Windows): a late key was caught up by sending it 20 ms after the one before (the
page saw keydowns 95 ms apart where the plan's smallest gap is 181), and a keyup waited its dwell from the keydown's send.
So: a late event never goes closer to the previous one than the smallest distance THIS plan has (keydown to keydown, dwell), and a keyup
is on its own deadline (the keydown's planned time plus the dwell).

No browser starts here: the real `Keyboard`, the real plan, a clock that only moves when something sleeps or the fake engine answers, and a
connection whose reply takes the event's own `latency` plus a round trip the test picks. Times are the times SENT (the page sees each
event `latency` later, a different draw per event, as on a real keyboard).

Known-bad: `Keyboard.type` as of core 63.68.0 (sleeps after the reply) fails the plan tests at every round trip; the first version of the
fix (floor of 20 ms, keyup `dwell` after the keydown's send) fails the keydown-gap test with a late reply and the keyup-deadline test.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler import keyboard as kb
from invisible_core.juggler._behaviour import InputLatency, PageActs, TypingPersona, plan_typing

pytestmark = pytest.mark.unit

TEXT = "someone@example.com"
SEED = 2
RTTS = [0.0, 5.0, 60.0]


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


def _plan():
    """What the keyboard draws first on page 0: the planned keydown time of every character, the plan, and its two minima (ms)."""
    plan = plan_typing(TEXT, TypingPersona.from_seed(SEED), nonce=PageActs(0).next("typing"))
    planned, t = [], 0.0
    for dwell, gap in plan:
        planned.append(t)
        t += dwell + gap
    min_gap = min(planned[i + 1] - planned[i] for i in range(len(planned) - 1))
    min_dwell = min(d for d, _g in plan)
    return planned, plan, min_gap, min_dwell


def _keys(engine, start, kind):
    """(sent ms from the start, index into the engine's events) of every typed key's `kind`, Shift left out."""
    return [((t - start) * 1000.0, n) for n, (t, k, key) in enumerate(engine.sent) if k == kind and key != "Shift"]


STALLS = {"no stall": None, "a reply 1.5 s late": {4: 1500.0}, "a keyup reply 1.5 s late": {7: 1500.0}}


@pytest.mark.parametrize("stall", list(STALLS))
@pytest.mark.parametrize("rtt_ms", RTTS)
def test_no_two_keydowns_are_closer_than_the_plans_smallest_gap(monkeypatch, rtt_ms, stall):
    engine, start = _type(monkeypatch, rtt_ms, STALLS[stall])
    _planned, _plan_, min_gap, _min_dwell = _plan()
    downs = [t for t, _n in _keys(engine, start, "keydown")]
    assert len(downs) == len(TEXT)
    closest = min(b - a for a, b in zip(downs, downs[1:]))
    assert closest >= min_gap - 0.5, "two keydowns %.1f ms apart, the plan's smallest gap is %.1f" % (closest, min_gap)


@pytest.mark.parametrize("stall", list(STALLS))
@pytest.mark.parametrize("rtt_ms", RTTS)
def test_every_dwell_is_one_the_plan_can_produce_and_on_plan_when_nothing_is_late(monkeypatch, rtt_ms, stall):
    engine, start = _type(monkeypatch, rtt_ms, STALLS[stall])
    planned, plan, _min_gap, min_dwell = _plan()
    downs = _keys(engine, start, "keydown")
    ups = _keys(engine, start, "keyup")
    for i, ((down, dn), (up, _un)) in enumerate(zip(downs, ups)):
        dwell = up - down
        assert dwell >= min_dwell - 0.5, "key %d held %.1f ms, the plan's smallest dwell is %.1f" % (i, dwell, min_dwell)
        on_time = abs(down - planned[i]) <= 2.0
        reply_in = (engine.replied[dn] - start) * 1000.0 - down
        if on_time and reply_in <= plan[i][0]:
            assert abs(dwell - plan[i][0]) <= 2.0, "key %d held %.1f ms, planned %.1f, nothing late" % (i, dwell, plan[i][0])


@pytest.mark.parametrize("rtt_ms", RTTS)
def test_the_last_key_is_on_the_plan_when_no_reply_is_later_than_the_next_deadline(monkeypatch, rtt_ms):
    engine, start = _type(monkeypatch, rtt_ms)
    planned, _plan_, _g, _d = _plan()
    last = _keys(engine, start, "keydown")[-1][0]
    assert abs(last - planned[-1]) <= 2.0, "the last key is %.1f ms off the plan" % (last - planned[-1])
    # and every key can only be later than its plan by the time the pipe was not free (the command before it was still unanswered), or
    # by the lateness of its own Shift (the key keeps the plan's lead after it)
    from invisible_core.juggler._behaviour import plan_shift
    lead = plan_shift(TypingPersona.from_seed(SEED), 1, nonce=PageActs(0).next("typing-shift"))[0][0]
    for i, (down, n) in enumerate(_keys(engine, start, "keydown")):
        if down - planned[i] > 2.0:
            before = [((t - start) * 1000.0, r) for (t, _k, _key), r in zip(engine.sent[:n], engine.replied[:n])]
            free = max((r - start) * 1000.0 for _t, r in before)
            allowed = free
            if engine.sent[n - 1][2] == "Shift":
                allowed = max(allowed, before[-1][0] + lead)
            assert down <= allowed + 0.5, "key %d is %.1f ms late with the pipe free" % (i, down - planned[i])


def test_a_keyup_is_on_its_own_deadline_after_a_late_keydown(monkeypatch):
    """A keydown 1.5 s late (the reply before it was): its keyup goes at the keydown's PLANNED time plus the dwell, but never sooner
    after the keydown than the plan's smallest dwell. The old rule (the dwell after the keydown's send) made it the full dwell."""
    engine, start = _type(monkeypatch, 5.0, late={4: 1500.0})
    planned, plan, _g, min_dwell = _plan()
    downs = _keys(engine, start, "keydown")
    ups = _keys(engine, start, "keyup")
    shortened = 0
    for i, ((down, dn), (up, _un)) in enumerate(zip(downs, ups)):
        wanted = max(planned[i] + plan[i][0], down + min_dwell)
        reply = (engine.replied[dn] - start) * 1000.0
        assert up == pytest.approx(max(wanted, reply), abs=1.0), "key %d: keyup at %.1f, deadline %.1f" % (i, up, wanted)
        if up - down < plan[i][0] - 2.0:
            shortened += 1
    assert shortened, "no keyup was held shorter than its dwell: the late keydown did not reach the keyup deadline"


def test_a_reply_later_than_the_next_deadline_keeps_the_order_and_pays_the_lateness_back_gradually(monkeypatch):
    """The reply of the 5th command (the keydown of the third key) comes 1.5 s late, far past the next deadlines."""
    engine, start = _type(monkeypatch, 5.0, late={4: 1500.0})
    planned, _plan_, min_gap, _min_dwell = _plan()
    kinds = [(kind, key) for _t, kind, key in engine.sent if key != "Shift"]
    expected = []
    for ch in TEXT:
        expected += [("keydown", ch), ("keyup", ch)]
    assert kinds == expected, "a key was skipped, doubled or swapped"
    times = [t for t, _kind, _key in engine.sent]
    assert times == sorted(times), "events out of order in time"
    late = [down - planned[i] for i, (down, _n) in enumerate(_keys(engine, start, "keydown"))]
    assert max(late) > 1000.0, "the stall did not reach the plan: the test is not testing"
    after = late[late.index(max(late)):]
    assert all(b <= a + 0.5 for a, b in zip(after, after[1:])), "the lateness grew again after the stall: %s" % [round(x) for x in after]
    assert after[-1] < after[0], "none of the lateness was paid back where the plan has room"
    downs = [d for d, _n in _keys(engine, start, "keydown")]
    assert min(b - a for a, b in zip(downs, downs[1:])) >= min_gap - 0.5


def test_the_same_seed_is_sent_at_the_same_times_twice(monkeypatch):
    first = _type(monkeypatch, 5.0)
    second = _type(monkeypatch, 5.0)
    a = [(round((t - first[1]) * 1000.0, 6), kind, key) for t, kind, key in first[0].sent]
    b = [(round((t - second[1]) * 1000.0, 6), kind, key) for t, kind, key in second[0].sent]
    assert a == b
