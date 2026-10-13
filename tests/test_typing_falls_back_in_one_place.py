"""`Actions._type` is the one place that decides what happens to text with a
character the layout has no key for, with the session's rhythm and with a
caller's delay alike: the keyboard refuses it and the text is inserted.

Until 44.39.0 the delay was not a parameter here, so invisible-puppeteer
called the keyboard directly for `keyboard.type(text, delay=N)` and raised
the keyboard's refusal where pyppeteer types the character.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.actions import Actions
from invisible_core.juggler.keyboard import UnknownKey

pytestmark = pytest.mark.unit


class _Keyboard:
    def __init__(self, refuses: bool):
        self.refuses = refuses
        self.calls = []

    def type(self, text, **kwargs):
        self.calls.append(("type", text, kwargs))
        if self.refuses:
            raise UnknownKey("no key")

    def insert_text(self, text):
        self.calls.append(("insert", text))


def _actions(refuses):
    actions = Actions.__new__(Actions)
    actions.keyboard = _Keyboard(refuses)
    return actions


def test_a_delay_reaches_the_keyboard():
    actions = _actions(refuses=False)
    actions._type("ab", 5.0)
    assert actions.keyboard.calls == [("type", "ab", {"delay_ms": 5.0})]


def test_without_a_delay_the_keyboard_keeps_its_own_rhythm():
    actions = _actions(refuses=False)
    actions._type("ab")
    assert actions.keyboard.calls == [("type", "ab", {})]


def test_a_character_with_no_key_is_inserted_with_a_delay_too():
    actions = _actions(refuses=True)
    actions._type("aéb", 5.0)
    assert actions.keyboard.calls == [("type", "aéb", {"delay_ms": 5.0}),
                                      ("insert", "aéb")]
