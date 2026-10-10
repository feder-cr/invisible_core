"""A character that only exists with Shift is typed with Shift down (B270).

⛔ THE KNOWN-BAD, measured on the published wrapper 0.31.0 and firefox-39, with
a page reading `shiftKey` and `getModifierState("Shift")`: typing
`john.doe@example.com` and `S3cret-pass!` sent `@` with `code: "Digit2"`, `S`
and `!` all with `shiftKey: false` and no Shift keydown at all - on every
release back to 0.29.0. No keyboard produces `@` from Digit2 without Shift, and
a page does not have to probe for it: a login form's key listener already sees
it. And holding Shift by hand was no better: `down("Shift")` then `press("2")`
sent `key: "2"` with `shiftKey: true`, because the name `"2"` pointed at the
key's description without its shifted form.

The engine carries `shiftKey` from the Shift it was sent (measured: a Shift
keydown through the protocol makes the next keys report `shiftKey: true`), so
the fix is that the keyboard sends Shift: these tests read the protocol
messages it sends.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler._behaviour import PageActs, TypingPersona, plan_typing
from invisible_core.juggler.keyboard import LAYOUT_CLOSURE, Keyboard

pytestmark = pytest.mark.unit


class _Fake:
    def __init__(self):
        self.events = []

    def send(self, method, params, **kw):
        self.events.append((params["type"], params["key"], params["code"]))
        return {}


def _kb(persona=None):
    c = _Fake()
    return c, Keyboard(c, "S", persona, acts=PageActs(1))


def _shift_state_at_each_key(events):
    """For every non-Shift keydown: was Shift down at that moment?"""
    down, out = False, []
    for typ, key, code in events:
        if key == "Shift":
            down = typ == "keydown"
        elif typ == "keydown":
            out.append((key, down))
    return out


def test_the_name_of_a_character_resolves_like_its_key_under_shift():
    c, kb = _kb()
    kb.down("Shift")
    kb.press("2")
    kb.press("a")
    kb.up("Shift")
    keys = [(k, code) for t, k, code in c.events if t == "keydown" and k != "Shift"]
    assert keys == [("@", "Digit2"), ("A", "KeyA")], (
        "with Shift held, the character name sent its unshifted key: %r" % keys)


def test_press_of_a_character_that_needs_shift_holds_shift_around_it():
    c, kb = _kb()
    kb.press("@")
    assert c.events == [("keydown", "Shift", "ShiftLeft"), ("keydown", "@", "Digit2"),
                        ("keyup", "@", "Digit2"), ("keyup", "Shift", "ShiftLeft")]
    assert kb.modifiers == set()


def test_press_with_shift_already_held_does_not_press_it_again():
    c, kb = _kb()
    kb.press("Shift+@")
    assert [e for e in c.events if e[1] == "Shift"] == [
        ("keydown", "Shift", "ShiftLeft"), ("keyup", "Shift", "ShiftLeft")]


def test_typing_puts_every_shifted_character_under_shift_and_runs_under_one():
    c, kb = _kb()
    kb.type("Hi AB@x!")
    state = _shift_state_at_each_key(c.events)
    assert state == [("H", True), ("i", False), (" ", False), ("A", True), ("B", True),
                     ("@", True), ("x", False), ("!", True)], state
    shift_downs = sum(1 for t, k, _ in c.events if k == "Shift" and t == "keydown")
    assert shift_downs == 3, "one Shift per run: H, AB@, !"
    assert kb.modifiers == set(), "Shift left down after typing"


def test_a_raw_down_of_a_shifted_character_cannot_go_out_without_shift():
    c, kb = _kb()
    kb.down("!")
    kb.up("!")
    assert c.events == [("keydown", "Shift", "ShiftLeft"), ("keydown", "!", "Digit1"),
                        ("keyup", "!", "Digit1"), ("keyup", "Shift", "ShiftLeft")]
    assert kb.modifiers == set()


def test_every_shifted_character_of_the_layout_is_marked_and_no_base_one_is():
    marked = {k for k, d in LAYOUT_CLOSURE.items() if d.get("needs_shift")}
    assert {"A", "Z", "@", "!", "?", ":", "_", "+", "{", "~", '"'} <= marked
    assert not ({"a", "z", "2", "1", "/", ";", "-", "=", "[", "`", "'", " "} & marked)


def test_the_hand_times_the_shift_and_the_key_rhythm_of_a_seed_does_not_move():
    """The persona draws the Shift timing LAST and on its own stream: the
    per-key plan of seed 4242 is the one it was before this change, pinned
    from `origin/main` on 2026-10-10."""
    p = TypingPersona.from_seed(4242)
    plan = [(round(d, 6), round(g, 6)) for d, g in plan_typing("Hello, World!", p, nonce=3)]
    assert plan == [(114.65857, 154.211357), (131.162475, 91.352553), (132.63849, 290.1226),
                    (117.868332, 343.404377), (129.345486, 141.153416), (141.75128, 188.589969),
                    (137.981243, 106.045622), (128.580899, 278.266019), (167.420031, 153.71402),
                    (109.163412, 251.218245), (101.823371, 260.994631), (110.444906, 305.228794),
                    (114.635763, 0.0)]
    assert round(p.hesitation_sigma, 6) == 0.662276
    assert 70.0 <= p.shift_lead_median_ms <= 170.0
    assert 25.0 <= p.shift_lag_median_ms <= 110.0
