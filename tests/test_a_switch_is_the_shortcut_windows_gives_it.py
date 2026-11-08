"""A persona switches keyboard with the shortcut Windows gives it.

A persona's keyboards are the input profiles Windows installs with its language
(locale.nls, LANGID:KLID). With no values in HKCU\\Keyboard Layout\\Toggle, as on
a new install, msctf switches between input languages with left Alt + Shift and
between the keyboards of one language with Ctrl + Shift (msctf.dll 26300.9711,
the function that reads Toggle). Windows keeps one active keyboard per language.
Until now every switch was Alt+Shift to the next keyboard, so a second keyboard
of the same language (Armenian Typewriter after Armenian Phonetic) was reached
with the wrong shortcut.

A persona whose language is written right to left is an open case: msctf gives
the switch inside a language no shortcut when SM_MIDEASTENABLED is set, and what
sets it is not known yet, so that persona never switches inside a language.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler._behaviour import PageActs
from invisible_core.juggler.keyboard import Keyboard, closure_for, profiles_for

pytestmark = pytest.mark.unit

ALT = [("keydown", "AltLeft"), ("keydown", "ShiftLeft"), ("keyup", "ShiftLeft"), ("keyup", "AltLeft")]
CTRL = [("keydown", "ControlLeft"), ("keydown", "ShiftLeft"), ("keyup", "ShiftLeft"), ("keyup", "ControlLeft")]


class _Fake:
    def __init__(self):
        self.sent = []

    def send(self, method, params, **kw):
        self.sent.append((method, params))
        return {}


def _kb(languages, region):
    c = _Fake()
    return c, Keyboard(c, "S", acts=PageActs(1), layout=profiles_for(languages, region))


def _events(sent):
    return [("insert", p["text"]) if m == "Page.insertText" else (p["type"], p["code"]) for m, p in sent]


def _only_on(klid, *others):
    chars = {n for n in closure_for(klid) if len(n) == 1}
    for other in others:
        chars -= {n for n in closure_for(other) if len(n) == 1}
    return sorted(chars)[0]


def test_the_keyboards_are_windows_profiles_in_its_order():
    assert profiles_for(["fa-IR"], "IR") == [("0429", "00000429"), ("0429", "00050429"), ("0409", "00000409")]
    assert profiles_for(["hy-AM"], "AM") == [("042b", "0002042B"), ("042b", "0003042B"), ("0419", "00000419")]


def test_a_second_keyboard_of_the_same_language_is_ctrl_shift():
    c, kb = _kb(["hy-AM"], "AM")
    typewriter = _only_on("0003042B", "0002042B", "00000419")
    kb.press(typewriter)
    assert _events(c.sent)[:4] == CTRL
    assert kb.layout == "0003042B"


def test_another_language_is_alt_shift():
    c, kb = _kb(["hy-AM"], "AM")
    kb.press("д")
    assert _events(c.sent)[:4] == ALT
    assert kb.layout == "00000419"


def test_a_language_comes_back_on_the_keyboard_it_was_left_on():
    c, kb = _kb(["hy-AM"], "AM")
    kb.press(_only_on("0003042B", "0002042B", "00000419"))   # Ctrl+Shift: Typewriter
    kb.press("д")                                           # Alt+Shift: Russian
    c.sent.clear()
    armenian = sorted(({n for n in closure_for("0002042B") if len(n) == 1}
                       & {n for n in closure_for("0003042B") if len(n) == 1})
                      - {n for n in closure_for("00000419") if len(n) == 1})[0]
    kb.press(armenian)                                      # on both Armenian keyboards
    events = _events(c.sent)
    assert events[:4] == ALT and ("keydown", "ControlLeft") not in events
    assert kb.layout == "0003042B"                          # where Armenian was left


def test_the_round_of_languages_comes_back_to_the_first():
    c, kb = _kb(["ba-RU"], "RU")                       # Bashkir, Russian, English US
    kb.press("x")
    assert _events(c.sent)[:8] == ALT + ALT             # Bashkir -> Russian -> English
    c.sent.clear()
    kb.press(_only_on("0000046D", "00000419", "00000409"))
    assert _events(c.sent)[:4] == ALT                   # English -> Bashkir: the round closes
    assert kb.layout == "0000046D"


def test_persian_reaches_english_with_alt_shift():
    c, kb = _kb(["fa-IR"], "IR")
    kb.press("x")
    assert _events(c.sent) == ALT + [("keydown", "KeyX"), ("keyup", "KeyX")]


def test_a_right_to_left_persona_never_switches_inside_its_language():
    # The open case: Persian (Standard) is reachable only with the switch inside a
    # language, which has no shortcut while SM_MIDEASTENABLED is not known.
    c, kb = _kb(["fa-IR"], "IR")
    standard_only = _only_on("00050429", "00000429", "00000409")
    kb.type(standard_only)
    assert _events(c.sent) == [("insert", standard_only)]
    assert kb.layout == "00000429"
