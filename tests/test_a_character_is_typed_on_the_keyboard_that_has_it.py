"""A character the persona's keyboard lacks is typed on the second keyboard
Windows installs with its language, with its key events.

A persona's keyboard was ONE layout (`layout_for`). On a Russian or Greek one
there is no Latin letter, so `press("x")` raised "unknown key: 'x'" and
`type("cafe")` inserted every letter as text, with no key event: the two
failures a wrapper e2e run met on a runner whose session was in a non-Latin
language. A real Russian or Greek user has English (US) as well, which Windows
installs with the language (Microsoft's table of default input profiles), and
types the `x` of an address on it, with KeyX. Now the persona has the list
(`layouts_for`), a character goes to the first keyboard that has it, and only a
character no keyboard has is inserted as text (Nepali in India: Windows
installs no second keyboard). The switch is pressed as a user presses it:
left Alt and left Shift, Windows' default shortcut between input languages,
whose key messages Firefox hands to the page.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler._behaviour import PageActs
from invisible_core.juggler.keyboard import Keyboard, layouts_for

pytestmark = pytest.mark.unit


class _Fake:
    def __init__(self):
        self.sent = []

    def send(self, method, params, **kw):
        self.sent.append((method, params))
        return {}


def _kb(layouts):
    c = _Fake()
    return c, Keyboard(c, "S", acts=PageActs(1), layout=layouts)


def _keys(sent):
    return [("insert", p["text"]) if m == "Page.insertText"
            else (p["type"], p["key"], p["code"]) for m, p in sent]


#: Left Alt down, left Shift down, left Shift up, left Alt up.
SWITCH = [("keydown", "Alt", "AltLeft"), ("keydown", "Shift", "ShiftLeft"),
          ("keyup", "Shift", "ShiftLeft"), ("keyup", "Alt", "AltLeft")]


@pytest.mark.parametrize("languages, region, layouts", [
    (["ru-RU"], "RU", ["00000419", "00000409"]),
    (["el-GR"], "GR", ["00000408", "00000409"]),
    (["ar-MA"], "MA", ["00020401", "0000040C"]),
    (["it-IT"], "IT", ["00000410"]),
    (["ne-IN"], "IN", ["00000461"]),
])
def test_the_keyboards_are_the_ones_windows_installs(languages, region, layouts):
    assert layouts_for(languages, region) == layouts


def test_a_latin_letter_on_a_russian_persona_is_typed_on_english_with_its_key():
    c, kb = _kb(layouts_for(["ru-RU"], "RU"))
    kb.press("x")
    assert _keys(c.sent) == SWITCH + [("keydown", "x", "KeyX"), ("keyup", "x", "KeyX")]
    assert kb.layout == "00000409"


def test_a_text_switches_keyboard_where_a_character_needs_it():
    c, kb = _kb(layouts_for(["ru-RU"], "RU"))
    kb.type("дa")
    assert _keys(c.sent) == [("keydown", "д", "KeyL"), ("keyup", "д", "KeyL")] + SWITCH + [
        ("keydown", "a", "KeyA"), ("keyup", "a", "KeyA")]
    assert kb.layout == "00000409"


def test_no_switch_is_pressed_for_a_character_on_the_active_keyboard():
    c, kb = _kb(layouts_for(["ru-RU"], "RU"))
    kb.type("да")
    kb.press("x")
    c.sent.clear()
    kb.type("ab.")
    kb.press("Enter")
    assert not [k for k in _keys(c.sent) if k[1] == "Alt"]


def test_a_shifted_run_is_cut_where_the_keyboard_changes():
    """Shift is up while the switch is pressed: `ДX` is Shift+Д, the switch,
    then Shift+X, not one run under one Shift."""
    c, kb = _kb(layouts_for(["ru-RU"], "RU"))
    kb.type("ДX")
    keys = _keys(c.sent)
    alt = keys.index(("keydown", "Alt", "AltLeft"))
    assert keys[alt - 1] == ("keyup", "Shift", "ShiftLeft")
    assert keys[alt:alt + 4] == SWITCH
    assert keys[alt + 4] == ("keydown", "Shift", "ShiftLeft")


def test_the_switched_keyboard_stays_until_a_character_needs_another():
    c, kb = _kb(layouts_for(["ru-RU"], "RU"))
    kb.type("x.")
    # `.` is on both keyboards (Slash on Russian, Period on English): the
    # English one, active since the `x`, types it.
    assert _keys(c.sent)[-2:] == [("keydown", ".", "Period"), ("keyup", ".", "Period")]


def test_a_character_no_keyboard_has_is_inserted_as_text():
    c, kb = _kb(layouts_for(["ne-IN"], "IN"))
    kb.type("x")
    assert _keys(c.sent) == [("insert", "x")]


def test_one_klid_or_a_comma_list_is_accepted():
    _, one = _kb("00000419")
    _, joined = _kb("00000419,00000409")
    assert one.layouts == ["00000419"] and joined.layouts == ["00000419", "00000409"]
