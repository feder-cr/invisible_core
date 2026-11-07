"""Generates `juggler/keylayouts_windows.py`: the keyboard of a Windows persona, per locale.

⛔ RUN ON WINDOWS, NEVER HAND-EDIT THE OUTPUT. The tables come from the layouts
Windows itself ships (kbdus, kbdit, kbdgr, kbdfr, kbdsp, kbdbr ABNT2, kbduk) read
through the same Win32 calls Firefox's widget uses (`MapVirtualKeyEx`,
`ToUnicodeEx`), and `keyCode` is computed with the rules of
`widget/windows/KeyboardLayout.cpp` (`ConvertNativeKeyCodeToDOMKeyCode`) and
`widget/SharedWidgetUtils.cpp` (`ComputeKeyCodeFromChar`), read on 2026-11-07
in the FIREFOX_159 tree. So what a page reads in `event.code`, `event.key` and
`event.keyCode` is what retail Firefox on Windows gives for that keyboard.

⛔ WHAT IT REPLACES. One US table, extracted from the driver bundle with
Chromium's key codes: a persona with an Italian locale typed `ò` with no key
event at all (`insertText`, the fallback for a character the US layout has no
key for), `@` as Shift+Digit2 where an Italian keyboard sends AltGr+ò, and
every persona sent `keyCode` 186/187/189 for `;`/`=`/`-` where Firefox on
Windows sends 59/61/173.

    python scripts/gen_windows_keylayouts.py            # regenerates the module
    python scripts/gen_windows_keylayouts.py --check    # 1 if the tree file is stale

Each entry is indexed by `code` (the physical key) and carries `keyCode`, the
unshifted `key`, and where they exist `shiftKey`, `altGrKey`, `shiftAltGrKey`.
A level that is a dead key is absent (the page would see `key: "Dead"` and a
composition; the keyboard inserts such a character without a key event, as
before). Non-printable keys keep the bundle's entries (`keylayout.LAYOUT`),
whose names and codes are the same on every layout; `AltRight` becomes
`AltGraph` on a layout that has AltGr, as Firefox reports it on Windows.
"""
from __future__ import annotations

import argparse
import ctypes
import pathlib
import pprint
import sys
from ctypes import wintypes

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "src" / "invisible_core" / "juggler" / "keylayouts_windows.py"

#: Windows keyboard layout identifiers (KLID), the DLL Windows loads for each.
KLIDS = {
    "us": "00000409",  # kbdus: United States
    "it": "00000410",  # kbdit: Italian
    "de": "00000407",  # kbdgr: German
    "fr": "0000040C",  # kbdfr: French (AZERTY)
    "es": "0000040A",  # kbdsp: Spanish
    "br": "00010416",  # kbdbr: Portuguese (Brazil ABNT2)
    "uk": "00000809",  # kbduk: United Kingdom
}

#: Physical keys that produce characters, scan code -> DOM `code`
#: (widget/NativeKeyToDOMCodeName.inc, CODE_MAP_WIN).
PRINTABLE_SCANCODES = {
    0x29: "Backquote", 0x02: "Digit1", 0x03: "Digit2", 0x04: "Digit3", 0x05: "Digit4",
    0x06: "Digit5", 0x07: "Digit6", 0x08: "Digit7", 0x09: "Digit8", 0x0A: "Digit9",
    0x0B: "Digit0", 0x0C: "Minus", 0x0D: "Equal",
    0x10: "KeyQ", 0x11: "KeyW", 0x12: "KeyE", 0x13: "KeyR", 0x14: "KeyT", 0x15: "KeyY",
    0x16: "KeyU", 0x17: "KeyI", 0x18: "KeyO", 0x19: "KeyP", 0x1A: "BracketLeft",
    0x1B: "BracketRight",
    0x1E: "KeyA", 0x1F: "KeyS", 0x20: "KeyD", 0x21: "KeyF", 0x22: "KeyG", 0x23: "KeyH",
    0x24: "KeyJ", 0x25: "KeyK", 0x26: "KeyL", 0x27: "Semicolon", 0x28: "Quote",
    0x2B: "Backslash",
    0x2C: "KeyZ", 0x2D: "KeyX", 0x2E: "KeyC", 0x2F: "KeyV", 0x30: "KeyB", 0x31: "KeyN",
    0x32: "KeyM", 0x33: "Comma", 0x34: "Period", 0x35: "Slash",
    0x56: "IntlBackslash", 0x73: "IntlRo", 0x7D: "IntlYen", 0x7E: "NumpadComma",
    0x39: "Space",
}

#: `WidgetUtils::ComputeKeyCodeFromChar` for the characters it names
#: (letters and digits are computed).
KEYCODE_OF_CHAR = {
    " ": 0x20, "\t": 0x09, ":": 0x3A, ";": 0x3B, "<": 0x3C, "=": 0x3D, ">": 0x3E,
    "?": 0x3F, "@": 0x40, "^": 0xA0, "!": 0xA1, '"': 0xA2, "#": 0xA3, "$": 0xA4,
    "%": 0xA5, "&": 0xA6, "_": 0xA7, "(": 0xA8, ")": 0xA9, "*": 0xAA, "+": 0xAB,
    "|": 0xAC, "-": 0xAD, "{": 0xAE, "}": 0xAF, "~": 0xB0, ",": 0xBC, ".": 0xBE,
    "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}

#: `GetFallbackKeyCodeOfPunctuationKey`, by the virtual key Windows gives the
#: physical key, when neither its unshifted nor its shifted character is ASCII.
VK_OEM_1, VK_OEM_PLUS, VK_OEM_COMMA, VK_OEM_MINUS, VK_OEM_PERIOD = 0xBA, 0xBB, 0xBC, 0xBD, 0xBE
VK_OEM_2, VK_OEM_3, VK_OEM_4, VK_OEM_5, VK_OEM_6, VK_OEM_7, VK_OEM_8 = 0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF
VK_OEM_102, VK_ABNT_C1, VK_ABNT_C2 = 0xE2, 0xC1, 0xC2
FALLBACK_KEYCODE = {
    VK_OEM_1: 0x3B, VK_OEM_PLUS: 0x3D, VK_OEM_COMMA: 0xBC, VK_OEM_MINUS: 0xAD,
    VK_OEM_PERIOD: 0xBE, VK_OEM_2: 0xBF, VK_OEM_3: 0xC0, VK_OEM_4: 0xDB, VK_OEM_5: 0xDC,
    VK_OEM_6: 0xDD, VK_OEM_7: 0xDE, VK_OEM_8: 0xC0, VK_OEM_102: 0xDC, VK_ABNT_C1: 0xDC,
}
OEM_VKS = set(FALLBACK_KEYCODE) | {VK_ABNT_C2}
VK_SPACE, VK_SHIFT, VK_CONTROL, VK_MENU = 0x20, 0x10, 0x11, 0x12

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.LoadKeyboardLayoutW.restype = wintypes.HKL
user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
user32.MapVirtualKeyExW.restype = wintypes.UINT
user32.MapVirtualKeyExW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.HKL]
user32.ToUnicodeEx.restype = ctypes.c_int
user32.ToUnicodeEx.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_char_p,
                               wintypes.LPWSTR, ctypes.c_int, wintypes.UINT, wintypes.HKL]
MAPVK_VSC_TO_VK_EX = 3
KLF_NOTELLSHELL = 0x80
TOUNICODE_NO_STATE_CHANGE = 0x4  # Windows 10 1607+: do not touch the dead-key state


def _chars(vk: int, sc: int, hkl, *, shift=False, altgr=False):
    """What the key gives under these modifiers: a str, '' for nothing, or
    None for a dead key."""
    state = bytearray(256)
    if shift:
        state[VK_SHIFT] = 0x80
    if altgr:
        state[VK_CONTROL] = 0x80
        state[VK_MENU] = 0x80
    buf = ctypes.create_unicode_buffer(8)
    n = user32.ToUnicodeEx(vk, sc, bytes(state), buf, 8, TOUNICODE_NO_STATE_CHANGE, hkl)
    if n < 0:
        return None
    out = buf.value[:n]
    if len(out) != 1 or ord(out) < 0x20 or ord(out) == 0x7F:
        return ""
    return out


def _keycode(vk: int, unshifted: str, shifted: str) -> int:
    """`KeyboardLayout::ConvertNativeKeyCodeToDOMKeyCode` for a printable key."""
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A or 0x60 <= vk <= 0x87 or vk == VK_SPACE:
        return vk
    if vk == VK_ABNT_C2:
        return 0x6C  # NS_VK_SEPARATOR, its own case after the OEM block
    if vk not in OEM_VKS:
        return 0
    for ch in (unshifted, shifted):
        if ch and " " <= ch <= "\x7f":
            return _keycode_of_char(ch)
    return FALLBACK_KEYCODE.get(vk, 0)


def _keycode_of_char(ch: str) -> int:
    if "A" <= ch <= "Z" or "a" <= ch <= "z":
        return ord(ch.upper())
    if "0" <= ch <= "9":
        return ord(ch)
    return KEYCODE_OF_CHAR.get(ch, 0)


def read_layout(klid: str) -> dict:
    hkl = user32.LoadKeyboardLayoutW(klid, KLF_NOTELLSHELL)
    if not hkl:
        raise SystemExit("LoadKeyboardLayout(%s) failed: %d" % (klid, ctypes.get_last_error()))
    keys = {}
    has_altgr = False
    for sc, code in sorted(PRINTABLE_SCANCODES.items(), key=lambda kv: kv[1]):
        vk = user32.MapVirtualKeyExW(sc, MAPVK_VSC_TO_VK_EX, hkl)
        if not vk:
            continue
        levels = {
            "key": _chars(vk, sc, hkl),
            "shiftKey": _chars(vk, sc, hkl, shift=True),
            "altGrKey": _chars(vk, sc, hkl, altgr=True),
            "shiftAltGrKey": _chars(vk, sc, hkl, shift=True, altgr=True),
        }
        dead = any(ch is None for ch in levels.values())
        if not dead and not any(levels.values()):
            continue  # this layout has no such key (IntlRo outside ABNT2, NumpadComma)
        entry = {"keyCode": _keycode(vk, levels["key"] or "", levels["shiftKey"] or "")}
        for name, ch in levels.items():
            if ch:
                entry[name] = ch
        if "altGrKey" in entry or "shiftAltGrKey" in entry:
            has_altgr = True
        if dead:
            # a dead level (de Equal: acute and grave; fr BracketLeft: circumflex and
            # diaeresis): the page sees `key: "Dead"`; the keyboard never presses the
            # key for a character it composes, it inserts that character instead
            entry["dead"] = True
        keys[code] = entry
    return {"hasAltGr": has_altgr, "keys": keys}


def render(layouts: dict) -> str:
    body = pprint.pformat(layouts, width=96, sort_dicts=True)
    return (
        '"""The keyboard of a Windows persona, per locale. GENERATED by\n'
        "`python scripts/gen_windows_keylayouts.py` on Windows; do not hand-edit.\n\n"
        "Indexed by DOM `code`. `keyCode` is Firefox's on Windows (widget/windows/\n"
        "KeyboardLayout.cpp rules), `key`/`shiftKey`/`altGrKey`/`shiftAltGrKey` are\n"
        "what the key gives at each level; a dead level is absent. `hasAltGr` says\n"
        "whether AltRight is AltGraph on this keyboard.\n"
        '"""\n'
        "from __future__ import annotations\n\n"
        "#: %d layouts: %s\n"
        "LAYOUTS = %s\n" % (len(layouts), ", ".join(sorted(layouts)), body)
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text = render({name: read_layout(klid) for name, klid in KLIDS.items()})
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("stale: %s" % OUT)
            return 1
        print("fresh: %s" % OUT)
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
