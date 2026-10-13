"""`Actions._type` hands the caller's delay to the keyboard, and the keyboard
is the one place that decides what a character with no key becomes
(`tests/test_a_character_with_no_key_is_inserted_in_its_place.py`).

Until 44.39.0 the delay was not a parameter here, so invisible-puppeteer
called the keyboard directly for `keyboard.type(text, delay=N)`, and the
keyboard's refusal of a character with no key reached the user where pyppeteer
types it.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.actions import Actions

pytestmark = pytest.mark.unit


class _Keyboard:
    def __init__(self):
        self.calls = []

    def type(self, text, **kwargs):
        self.calls.append(("type", text, kwargs))


def _actions():
    actions = Actions.__new__(Actions)
    actions.keyboard = _Keyboard()
    return actions


def test_a_delay_reaches_the_keyboard():
    actions = _actions()
    actions._type("aéb", 5.0)
    assert actions.keyboard.calls == [("type", "aéb", {"delay_ms": 5.0})]


def test_without_a_delay_the_keyboard_keeps_its_own_rhythm():
    actions = _actions()
    actions._type("ab")
    assert actions.keyboard.calls == [("type", "ab", {})]
