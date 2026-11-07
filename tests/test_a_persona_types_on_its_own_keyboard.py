"""A persona types on the keyboard of its language, with Firefox's key codes on Windows.

⛔ KNOWN-BAD. One table, the US layout extracted from the driver bundle with
Chromium's key codes, for every persona. A persona with an Italian locale typed
`ò` with no key event at all (`insertText`, the fallback for a character the
US layout has no key for), `@` as Shift+Digit2 where an Italian keyboard sends
AltGr+ò, `z` on `KeyZ` where a German keyboard has it on `KeyY`; and every
persona sent `keyCode` 186 for `;`, 187 for `=`, 189 for `-`, where Firefox on
Windows sends 59, 61 and 173 (`widget/windows/KeyboardLayout.cpp`,
`ConvertNativeKeyCodeToDOMKeyCode`: the key code of an OEM key comes from the
character it produces). A page's key listener reads all of this without a probe.

The tables come from the layouts Windows ships, read through the calls
Firefox's widget uses (`scripts/gen_windows_keylayouts.py`); the layout is
chosen from the session's language (`layout_for`).
"""
from __future__ import annotations

import pytest

from invisible_core.juggler._behaviour import PageActs
from invisible_core.juggler.keyboard import (
    LAYOUT_CLOSURE, Keyboard, closure_for, layout_for,
)
from invisible_core.juggler.keylayouts_windows import LAYOUTS

pytestmark = pytest.mark.unit


class _Fake:
    def __init__(self):
        self.events = []

    def send(self, method, params, **kw):
        if method == "Page.insertText":
            self.events.append(("insert", params["text"]))
        else:
            self.events.append((params["type"], params["key"], params["code"],
                                params["keyCode"]))
        return {}


def _kb(layout="us"):
    c = _Fake()
    return c, Keyboard(c, "S", None, acts=PageActs(1), layout=layout)


def _downs(events):
    return [(k, code, kc) for t, k, code, *rest in
            [e for e in events if e[0] != "insert"] for kc in [rest[0]] if t == "keydown"]


# ── the layout is the language's ───────────────────────────────────────────

def test_the_layout_follows_the_first_language_tag():
    assert layout_for(["it-IT", "it", "en-US", "en"]) == "00000410"
    assert layout_for("de-DE,de,en-US,en") == "00000407"
    assert layout_for(["fr-FR"]) == "0000040C"
    assert layout_for(["es-ES"]) == "0000040A"
    assert layout_for(["pt-BR", "pt"]) == "00000416"
    assert layout_for(["en-GB", "en"]) == "00000809"
    assert layout_for(["en-US", "en"]) == "00000409"
    assert layout_for([]) == "00000409"


# ── an Italian persona ─────────────────────────────────────────────────────

def test_an_italian_persona_has_a_key_for_o_grave():
    c, kb = _kb("it")
    kb.type("ò")
    assert c.events == [("keydown", "ò", "Semicolon", 192), ("keyup", "ò", "Semicolon", 192)], (
        "known-bad: insertText with no key event: %r" % c.events)


def test_an_italian_persona_reaches_at_with_altgr():
    c, kb = _kb("it")
    kb.press("@")
    assert c.events == [("keydown", "AltGraph", "AltRight", 18),
                        ("keydown", "@", "Semicolon", 192),
                        ("keyup", "@", "Semicolon", 192),
                        ("keyup", "AltGraph", "AltRight", 18)], (
        "known-bad: Shift+Digit2, the US way: %r" % c.events)
    assert kb.modifiers == set()


def test_altgr_and_shift_together_for_a_brace():
    c, kb = _kb("it")
    kb.press("{")  # AltGr+Shift+è on an Italian keyboard
    downs = _downs(c.events)
    assert [k for k, _, _ in downs] == ["AltGraph", "Shift", "{"]
    assert downs[-1][1] == "BracketLeft"
    assert [e[1] for e in c.events if e[0] == "keyup"] == ["{", "Shift", "AltGraph"]


def test_typed_text_mixes_plain_shift_and_altgr_keys():
    c, kb = _kb("it")
    kb.type("a@é")  # é is Shift+è on an Italian keyboard
    downs = _downs(c.events)
    assert downs == [("a", "KeyA", 65),
                     ("AltGraph", "AltRight", 18), ("@", "Semicolon", 192),
                     ("Shift", "ShiftLeft", 16), ("é", "BracketLeft", 59)]
    assert kb.modifiers == set() and kb.pressed == set()


def test_a_character_the_italian_keyboard_composes_is_still_inserted():
    """`~` on an Italian keyboard is AltGr+ì only as a dead key on some
    layouts, and `ê` needs a dead key everywhere: inserted, not faked."""
    c, kb = _kb("it")
    kb.type("ê")
    assert c.events == [("insert", "ê")]


# ── other keyboards ────────────────────────────────────────────────────────

def test_a_german_persona_has_z_and_y_swapped():
    c, kb = _kb("de")
    kb.type("zy")
    assert _downs(c.events) == [("z", "KeyY", 90), ("y", "KeyZ", 89)]


def test_a_french_persona_types_on_azerty():
    c, kb = _kb("fr")
    kb.type("a")
    assert _downs(c.events) == [("a", "KeyQ", 65)]
    c, kb = _kb("fr")
    kb.type("é")  # a plain key on AZERTY (Digit2), no Shift
    assert _downs(c.events) == [("é", "Digit2", 50)]


def test_a_british_persona_has_pound_on_three_and_at_on_quote():
    c, kb = _kb("uk")
    kb.type("£@")
    downs = _downs(c.events)
    assert ("£", "Digit3", 51) in downs and ("@", "Quote", 222) in downs
    assert "AltGraph" not in [k for k, _, _ in downs]


def test_a_brazilian_persona_has_c_cedilla_as_a_key():
    c, kb = _kb("br")
    kb.type("ç")
    assert _downs(c.events) == [("ç", "Semicolon", 59)]


# ── the US keyboard, Firefox's numbers ─────────────────────────────────────

def test_the_us_keyboard_sends_firefox_s_key_codes():
    c, kb = _kb("us")
    kb.type(";=-")
    assert _downs(c.events) == [(";", "Semicolon", 59), ("=", "Equal", 61), ("-", "Minus", 173)], (
        "known-bad: Chromium's 186/187/189: %r" % c.events)


def test_the_us_keyboard_is_the_default_and_the_module_closure():
    c, kb = _kb()
    assert kb.layout == "us" and kb._closure is LAYOUT_CLOSURE
    kb.press("@")
    assert _downs(c.events) == [("Shift", "ShiftLeft", 16), ("@", "Digit2", 50)]


def test_the_windows_us_table_agrees_with_the_bundle_on_every_character():
    """The generator reads the US layout file's bytes; the bundle's US table is
    the independent record of the same keyboard. They may
    differ only in `keyCode`, on the three keys where Firefox and Chromium do."""
    from invisible_core.juggler.keylayout import LAYOUT
    different_keycodes = set()
    for code, w in LAYOUTS["00000409"]["keys"].items():
        b = LAYOUT.get(code)
        if b is None:
            assert code in ("IntlBackslash", "IntlRo", "NumpadComma"), code
            continue
        assert w.get("key", "") == b.get("key", ""), code
        if code != "Space":
            assert w.get("shiftKey") == b.get("shiftKey"), code
        if w["keyCode"] != b["keyCode"]:
            different_keycodes.add(code)
    assert different_keycodes == {"Semicolon", "Equal", "Minus"}


def test_every_layout_keeps_the_keys_that_do_not_move():
    for name in LAYOUTS:
        cl = closure_for(name)
        assert cl["Enter"]["code"] == "Enter" and cl["Shift"]["code"] == "ShiftLeft"
        assert cl["Space"]["keyCode"] == 32 and cl["Digit1"]["keyCode"] == 49
        assert cl["AltGraph"]["code"] == ("AltRight" if LAYOUTS[name]["hasAltGr"] else "AltGraph")
