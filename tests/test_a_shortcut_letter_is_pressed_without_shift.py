"""`press("Control+A")` is Control+A: no Shift is held for the capital letter of a shortcut.

⛔ THE KNOWN-BAD, measured on the published wrapper 0.50.1 and the engine of the giro 7, in an <input> and a <textarea> with text and focus:
`keyboard.press("Control+A")` left the selection where it was (selectionStart == selectionEnd), `press("Control+a")` selected all. The name
`A` is a character that only exists with Shift (B270, `needs_shift`), so the keyboard held Shift for it and the browser got Control+Shift+A,
which is not the select-all command. Playwright's own Firefox selects all with `Control+A`. A hand that presses the shortcut holds no Shift,
and the key reports `key: "a"`.

These tests read the protocol messages the keyboard sends. The B270 floor stays everywhere else: a bare `A`, a symbol under Control, Alt+A.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler._behaviour import PageActs
from invisible_core.juggler.keyboard import Keyboard

pytestmark = pytest.mark.unit


class _Fake:
    def __init__(self):
        self.events = []

    def send(self, method, params, **kw):
        self.events.append((params["type"], params["key"], params["code"]))
        return {}


def _kb():
    c = _Fake()
    return c, Keyboard(c, "S", None, acts=PageActs(1))


def _keys(events):
    return [(t, k) for t, k, _ in events]


@pytest.mark.parametrize("chord, modifier", [("Control+A", "Control"), ("ControlOrMeta+A", "Control"), ("Meta+A", "Meta"),
                                             ("ControlLeft+A", "Control"), ("Control+Z", "Control"), ("Control+C", "Control")])
def test_a_capital_letter_under_control_or_meta_is_its_own_key_and_no_shift_is_held(chord, modifier):
    c, kb = _kb()
    kb.press(chord)
    letter = chord[-1]
    assert _keys(c.events) == [("keydown", modifier), ("keydown", letter.lower()), ("keyup", letter.lower()), ("keyup", modifier)], (
        "%s sent %r" % (chord, _keys(c.events)))
    assert not [e for e in c.events if e[1] == "Shift"], "Shift was held for a shortcut: %r" % c.events
    assert c.events[1][2] == "Key" + letter, "the key code is the letter's own key"
    assert kb.modifiers == set() and kb.pressed == set()


def test_the_lowercase_name_sends_the_same_events_as_the_capital_one():
    c1, kb1 = _kb()
    kb1.press("Control+A")
    c2, kb2 = _kb()
    kb2.press("Control+a")
    assert c1.events == c2.events


def test_the_keys_pressed_one_by_one_are_the_same_shortcut():
    c, kb = _kb()
    kb.down("Control")
    kb.press("A")
    kb.up("Control")
    assert _keys(c.events) == [("keydown", "Control"), ("keydown", "a"), ("keyup", "a"), ("keyup", "Control")]


def test_a_raw_down_and_up_of_the_capital_letter_under_control_pair_up_even_if_control_is_let_go_first():
    c, kb = _kb()
    kb.down("Control")
    kb.down("A")
    kb.up("Control")
    kb.up("A")
    assert _keys(c.events) == [("keydown", "Control"), ("keydown", "a"), ("keyup", "Control"), ("keyup", "a")]
    assert kb.pressed == set() and not kb._lowered


def test_control_shift_a_keeps_its_shift_and_its_capital():
    c, kb = _kb()
    kb.press("Control+Shift+A")
    assert _keys(c.events) == [("keydown", "Control"), ("keydown", "Shift"), ("keydown", "A"), ("keyup", "A"), ("keyup", "Shift"),
                               ("keyup", "Control")]
    c, kb = _kb()
    kb.down("Shift")
    kb.press("Control+A")
    assert ("keydown", "A") in _keys(c.events), "Shift was held by the caller: the capital stays"


def test_the_floor_of_the_shifted_character_is_where_it_was():
    # A bare capital, a symbol under Control and a capital under Alt only exist with Shift: it is held for them (B270).
    for chord, key in (("A", "A"), ("Control+@", "@"), ("Alt+A", "A")):
        c, kb = _kb()
        kb.press(chord)
        assert ("keydown", "Shift") in _keys(c.events), "%s: Shift was not held: %r" % (chord, _keys(c.events))
        assert ("keydown", key) in _keys(c.events), "%s: %r" % (chord, _keys(c.events))


def test_a_keyboard_without_the_lowercase_key_resolves_the_capital_as_before_and_never_raises_a_key_error(monkeypatch):
    full = _kb()[1]._closure
    reduced = {k: v for k, v in full.items() if k != "a"}
    monkeypatch.setattr(Keyboard, "_closure", property(lambda self: reduced))
    c, kb = _kb()
    kb.press("Control+A")  # nothing is lowered: the capital keeps resolving, with the Shift of B270, as it did
    assert ("keydown", "A") in _keys(c.events)
    assert ("keydown", "Shift") in _keys(c.events)
    c, kb = _kb()
    kb.down("Control")
    kb.down("A")
    kb.up("A")
    kb.up("Control")
    assert ("keydown", "A") in _keys(c.events) and not kb._lowered


def test_a_down_that_was_never_closed_does_not_change_the_key_of_a_bare_capital_pressed_later():
    c, kb = _kb()
    kb.down("Control")
    kb.down("A")  # the shortcut key, never released
    kb.up("Control")
    assert kb._lowered, "the lowered key is remembered for its up"
    c.events.clear()
    kb.press("A")  # a bare capital: Shift, the capital key, and its up is the capital too
    assert _keys(c.events) == [("keydown", "Shift"), ("keydown", "A"), ("keyup", "A"), ("keyup", "Shift")]
    assert not kb._lowered
    # The entry is for the `down` that made it: a later `down` of the same key that is not a shortcut clears it at once.
    c, kb = _kb()
    kb.down("Control")
    kb.down("A")
    kb.up("Control")
    kb.down("A")
    assert not kb._lowered, "the entry of a down that was never closed outlived the next down of the same key"
