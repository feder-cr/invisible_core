"""The hand that types a character is the hand of its key on the persona's keyboard.

⛔ KNOWN-BAD. Hands were assigned from the US characters: on QWERTZ `z` (KeyY,
right hand) was counted left, on an Italian keyboard `ò` had no hand at all.
The same-hand and alternate-hand factors of the typing rhythm were applied to
the wrong pairs on every persona that does not type on the US keyboard.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler._behaviour import (
    _LEFT_HAND, _RIGHT_HAND, TypingPersona, _hand, plan_typing,
)
from invisible_core.juggler.keyboard import closure_for

pytestmark = pytest.mark.unit


def test_the_us_answer_is_unchanged_character_for_character():
    for ch in _LEFT_HAND:
        assert _hand(ch) == "L" and _hand(ch, "us") == "L"
    for ch in _RIGHT_HAND:
        assert _hand(ch) == "R"
    assert _hand("é") == "?"


def test_on_qwertz_z_is_typed_by_the_right_hand():
    assert closure_for("de")["z"]["code"] == "KeyY"
    assert _hand("z", "de") == "R", "known-bad: z counted left on a German keyboard"
    assert _hand("y", "de") == "L"


def test_on_azerty_the_hand_follows_the_key():
    assert _hand("q", "fr") == "L"  # KeyA
    assert _hand("m", "fr") == "R"  # Semicolon
    assert _hand("w", "fr") == "L"  # KeyZ


def test_a_letter_of_the_italian_keyboard_has_a_hand():
    assert closure_for("it")["ò"]["code"] == "Semicolon"
    assert _hand("ò", "it") == "R", "known-bad: no hand for a key the persona has"


def test_the_rhythm_uses_the_persona_s_keyboard():
    """German `zu`: both right hand on QWERTZ (same-hand factor), alternating on
    US. Same seed, same nonce: only the hand factor differs."""
    p = TypingPersona.from_seed(4242)
    us = plan_typing("zu", p, nonce=1)[0][1]
    de = plan_typing("zu", p, nonce=1, layout="de")[0][1]
    assert de / us == pytest.approx(p.same_hand_factor / p.alternate_hand_factor)
    assert plan_typing("hello", p, nonce=2) == plan_typing("hello", p, nonce=2, layout="us")
