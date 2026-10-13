"""Typing text with a character the US layout has no key for presses the keys
that exist and inserts that character in its place, as Playwright's
`keyboard.type` and pyppeteer's do, character by character.

Until 44.39.0 `Keyboard.type` refused the whole text, and of its three callers
one inserted the whole text without a single key event (`Actions._type`: a
field filled with "café" saw no keystroke at all) and two passed the refusal
to the caller: the wrapper's `page.type("#i", "café")` and
`page.keyboard.type("café")` raised "'é' has no key on the US layout".

A key event is never faked for a character without a key: no
`dispatchKeyEvent` goes out with an empty `code`, and `press` of a key that
does not exist is still refused.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler._behaviour import PageActs
from invisible_core.juggler.keyboard import Keyboard, UnknownKey

pytestmark = pytest.mark.unit


class _Fake:
    def __init__(self):
        self.sent = []

    def send(self, method, params, **kw):
        self.sent.append((method, params))
        return {}


def _kb():
    c = _Fake()
    return c, Keyboard(c, "S", acts=PageActs(1))


def _what(sent):
    out = []
    for method, p in sent:
        if method == "Page.insertText":
            out.append(("insert", p["text"]))
        else:
            out.append((p["type"], p["key"]))
    return out


def test_the_keys_are_pressed_and_the_keyless_character_inserted_in_its_place():
    c, kb = _kb()
    kb.type("café!")
    assert _what(c.sent) == [
        ("keydown", "c"), ("keyup", "c"), ("keydown", "a"), ("keyup", "a"),
        ("keydown", "f"), ("keyup", "f"), ("insert", "é"),
        ("keydown", "Shift"), ("keydown", "!"), ("keyup", "!"),
        ("keyup", "Shift")]


def test_no_key_event_comes_out_empty_for_a_character_without_a_key():
    c, kb = _kb()
    kb.type("中a\U0001f600")
    keys = [p for m, p in c.sent if m == "Page.dispatchKeyEvent"]
    assert keys and all(p["code"] and p["keyCode"] for p in keys), keys
    assert _what(c.sent) == [("insert", "中"), ("keydown", "a"),
                             ("keyup", "a"), ("insert", "\U0001f600")]


def test_pressing_a_key_that_does_not_exist_is_still_refused():
    c, kb = _kb()
    with pytest.raises(UnknownKey):
        kb.press("中")
    assert not c.sent
