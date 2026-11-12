"""The keyboard: from a key name to the four fields Juggler demands.

⛔ THIS FILE EXISTS BECAUSE `Page.dispatchKeyEvent` DOES NOT FORGIVE A WRONG
FIELD SILENTLY - IT FORGIVES IT TOO MUCH. It wants `key`, `code`, `keyCode`
and `location` together, and if `code` is empty and `keyCode` is zero the
event still fires: the character shows up in the field, the action succeeds,
the tests pass, and the page reads `event.code === ""` on a key that on every
real Firefox has a physical name. It is a tell that lives in the fields
nobody looks at because the action worked.

The first draft of `_type` in `actions.py` sent exactly that:
`code: ""`, `keyCode: 0`, for every character.

**THREE DETAILS READ IN THE DRIVER'S CODE, not deduced** (`coreBundle.js`,
`RawKeyboardImpl3`), and each one changes the bytes that go out:

1. The `keyCode` field carries **`keyCodeWithoutLocation`**, not `keyCode`.
   They differ exactly on the keys that exist twice: `ShiftLeft` has
   `keyCode` 160 and `keyCodeWithoutLocation` 16, and 16 is what a real
   Firefox puts in the event.
2. For Enter, `text` must be set to an **empty string**, not to `"\\r"`:
   Gecko generates the character. Sending it explicitly inserts it twice.
3. `keyup` NEVER carries `text`. With `text` Juggler raises
   `keyup does not support text option` and typing dies halfway through.

**And the layout is not here**: the non-printable keys live in `keylayout.py`,
the printable keys of every layout in `keylayouts_windows.py`, generated on
Windows by `scripts/gen_windows_keylayouts.py`. Here there is only how a name is
resolved, which is the algorithm of `buildLayoutClosure` read in the driver
and rewritten.
"""
from __future__ import annotations

import time
from typing import Optional

from .. import _floats
from .keylayout import LAYOUT
from .keylayouts_windows import LOCALE_KEYBOARDS, LOCALE_KLID
from .keylayouts_windows import LAYOUTS as WINDOWS_LAYOUTS

#: The four that count as modifiers, in the driver's order.
MODIFIERS = ("Alt", "AltGraph", "Control", "Meta", "Shift")

#: ⛔ Firefox's mask is NOT Gecko's and is NOT `1 << index`: read in
#: `toModifiersMask2` of the bundle. Juggler translates it itself into
#: `nsIDOMWindowUtils.MODIFIER_*`, so these are the numbers that belong here.
MODIFIER_MASK = {"Alt": 1, "Control": 2, "Shift": 4, "Meta": 8}

#: ⛔ And this is YET ANOTHER encoding, for the `buttons` field: read in
#: `toButtonsMask2`. **Right and middle are swapped** relative to the
#: button number (`button`: 0 left, 1 middle, 2 right). Writing
#: `1 << button` looks right, gives 1 for the left one - and gets the other
#: two wrong.
BUTTON_MASK = {0: 1, 1: 4, 2: 2}

#: The driver's aliases: a convenient name pointing at a physical key.
ALIASES = {"ShiftLeft": ["Shift"], "ControlLeft": ["Control"],
           "AltLeft": ["Alt"], "MetaLeft": ["Meta"], "Enter": ["\n", "\r"]}

#: ⛔ Three keys where Firefox disagrees with the generic layout. Read in
#: `kFirefoxKeyOverrides`: without this, `AudioVolumeMute` comes out with the
#: wrong `code`.
FIREFOX_OVERRIDES = {
    "AudioVolumeMute": {"code": "VolumeMute", "keyCodeWithoutLocation": 181},
    "AudioVolumeDown": {"code": "VolumeDown", "keyCodeWithoutLocation": 182},
    "AudioVolumeUp": {"code": "VolumeUp", "keyCodeWithoutLocation": 183},
}


def _sleep_ms(ms: float) -> None:
    if ms > 0.0:
        time.sleep(ms / 1000.0)


class UnknownKey(ValueError):
    """The name is not in the layout. ⛔ It is REJECTED instead of inventing
    an empty event: a `keyCode: 0` does not fail, it lies."""


#: The persona's keyboards, as Windows writes its input profiles: LANGID:KLID
#: joined by semicolons, the language's first (`profiles_for`). Declared once, with the session's language and country,
#: by `prefs._apply_locale`; server-only: `instance.launch` and the wrapper's
#: server take it out of the prefs before the profile is written, and hand it
#: to every page's keyboard.
KEYBOARD_LAYOUT_PREF = "invisible.server.keyboard_layout"

#: A layout is named by its Windows keyboard identifier (KLID). These short
#: names stay as aliases of the most used ones.
US = "00000409"
LAYOUT_ALIASES = {"us": US, "it": "00000410", "de": "00000407", "fr": "0000040C",
           "es": "0000040A", "br": "00000416", "uk": "00000809"}
#: The primary languages (LANGID & 0x3ff) whose system is written right to
#: left, as win32kbase's RIMIsDefaultUILanguageRTL lists them (26300.9711):
#: Arabic, Hebrew, Urdu, Persian, Sindhi, Syriac, Pashto, Divehi, Uyghur,
#: Dari, Central Kurdish; and four whole LANGIDs.
_RTL_PRIMARY = {0x01, 0x0D, 0x20, 0x29, 0x59, 0x5A, 0x63, 0x65, 0x80, 0x8C, 0x92}
_RTL_LANGIDS = {0x0846, 0x0850, 0x0860, 0x7C46, 0x7C50}


def _row_of_tag(tag: str) -> Optional[str]:
    """The locale of Windows a tag reads: its own, else its language's."""
    if tag in LOCALE_KLID:
        return tag
    lang = tag.split("-")[0]
    return lang if lang in LOCALE_KLID else None


def _klid_of_tag(tag: str) -> Optional[str]:
    row = _row_of_tag(tag)
    return LOCALE_KLID[row] if row else None


def _row_for(languages, region: Optional[str]) -> Optional[str]:
    """The row of Microsoft's table a session's keyboard comes from (see
    `layout_for`), or None for the US keyboard nothing named."""
    if isinstance(languages, str):
        languages = [t.strip() for t in languages.split(",") if t.strip()]
    if not languages:
        return None
    parts = languages[0].replace("_", "-").split("-")
    lang = parts[0].lower()
    reg = (region or next((x for x in parts[1:] if len(x) == 2), "")).upper()
    if reg and "%s-%s" % (lang, reg) in LOCALE_KLID:
        return "%s-%s" % (lang, reg)
    row = _row_of_tag(languages[0]) or _row_of_tag(lang)
    if row:
        return row
    if reg:
        from .._locale import _COUNTRY_FIREFOX_BUILDS
        for build, _share in _COUNTRY_FIREFOX_BUILDS.get(reg, ()):
            row = ("%s-%s" % (build.split("-")[0], reg)
                   if "%s-%s" % (build.split("-")[0], reg) in LOCALE_KLID
                   else _row_of_tag(build))
            if row:
                return row
    return None


def layout_for(languages, region: Optional[str] = None) -> str:
    """The keyboard (KLID) of a session, from its first language and its region.

    The order is that of a real Windows install, by Microsoft's table of default
    input profiles: the keyboard of the language in that country (German in
    Switzerland types on the Swiss keyboard); else the keyboard of the language
    (German -> German); for a language Windows has no profile for (Asturian,
    Sardinian...), the keyboard of the country's main language, which is what
    its speakers have; else the US keyboard. An IME language types Latin text
    through the keyboard under its IME. Nothing here reads the host."""
    row = _row_for(languages, region)
    return LOCALE_KLID[row] if row else US


def profiles_for(languages, region: Optional[str] = None) -> list:
    """Every input profile the session's Windows has, in Windows' order, as
    (LANGID, KLID): what Windows installs with the language in that country,
    the keyboards-to-install of its locale (locale.nls). Russian has Russian
    and English US; Persian has Persian, Persian (Standard) and English US;
    Armenian has two Armenian keyboards and Russian. A user types a character
    the active keyboard lacks by switching to one that has it."""
    row = _row_for(languages, region)
    if not row:
        return [("0409", US)]
    return [tuple(p) for p in LOCALE_KEYBOARDS[row]]


def layouts_for(languages, region: Optional[str] = None) -> list:
    """The KLIDs of `profiles_for`, in order, once each."""
    out = []
    for _lang, klid in profiles_for(languages, region):
        if klid not in out:
            out.append(klid)
    return out


def profiles_pref(languages, region: Optional[str] = None) -> str:
    """`profiles_for` as Windows writes it: LANGID:KLID joined by semicolons."""
    return ";".join("%s:%s" % p for p in profiles_for(languages, region))


def _layout_table(layout: str) -> dict:
    """The bundle's table with the printable keys of this Windows layout in
    place of the US ones: `key`/`shiftKey`/`altGrKey`/`shiftAltGrKey` and
    Firefox's `keyCode` on Windows (59 for `;`, not Chromium's 186). Keys the
    bundle has no entry for (IntlBackslash, IntlRo, NumpadComma) are added;
    `AltRight` is `AltGraph` on a layout that has AltGr, as Firefox reports it."""
    win = WINDOWS_LAYOUTS[LAYOUT_ALIASES.get(layout, layout)]
    table = {}
    for code, d in LAYOUT.items():
        if code == "AltGraph" and win["hasAltGr"]:
            continue  # not a DOM code: Firefox on Windows reports AltRight as AltGraph
        w = win["keys"].get(code)
        if w is None:
            table[code] = dict(d)
            continue
        e = {k: v for k, v in d.items() if k not in ("key", "shiftKey", "shiftKeyCode",
                                                      "keyCode", "keyCodeWithoutLocation")}
        e["keyCode"] = w["keyCode"]
        for level in ("key", "shiftKey", "altGrKey", "shiftAltGrKey"):
            if w.get(level):
                e[level] = w[level]
        e.setdefault("key", "")
        table[code] = e
    for code, w in win["keys"].items():
        if code not in table:
            table[code] = {k: v for k, v in w.items() if k != "dead"}
    if win["hasAltGr"]:
        table["AltRight"] = dict(table["AltRight"], key="AltGraph")
    return table


def _build_closure(layout: str = "us") -> dict:
    """The name -> description dict, in the same shape as the driver.

    A key is reachable by `code` (`KeyA`), by `key` if it is a single
    character (`a`), by its shifted form (`A`), and by aliases (`Shift`).
    ⛔ A key with `location` does NOT get entered by `key`: `NumpadEnter`
    must not respond to the name `Enter`, or `press("Enter")` would end up
    on the numpad.
    """
    out: dict = {}
    table = _layout_table(layout)
    for code, d in table.items():
        key = d.get("key") or ""
        descr = {
            "key": key,
            "keyCode": d.get("keyCode") or 0,
            "keyCodeWithoutLocation": d.get("keyCodeWithoutLocation")
                                      or d.get("keyCode") or 0,
            "code": code,
            "text": d.get("text") or "",
            "location": d.get("location") or 0,
        }
        if len(key) == 1:
            descr["text"] = key
        shifted = None
        if d.get("shiftKey"):
            shifted = dict(descr)
            shifted["key"] = d["shiftKey"]
            shifted["text"] = d["shiftKey"]
            if d.get("shiftKeyCode"):
                shifted["keyCode"] = d["shiftKeyCode"]
        altgr = shift_altgr = None
        if d.get("altGrKey"):
            altgr = dict(descr, key=d["altGrKey"], text=d["altGrKey"])
        if d.get("shiftAltGrKey"):
            shift_altgr = dict(descr, key=d["shiftAltGrKey"], text=d["shiftAltGrKey"])
        out[code] = dict(descr, shifted=shifted, altgr=altgr, shift_altgr=shift_altgr)
        for alias in ALIASES.get(code, []):
            out[alias] = descr
        if key == "AltGraph":
            out["AltGraph"] = descr  # the name Playwright gives the key
        if d.get("location"):
            continue
        # ⛔ THE CHARACTER RESOLVES LIKE ITS KEY, shifted form included. It
        # used to point at the description WITHOUT it, so with Shift held
        # `press("2")` sent `key: "2"` with `shiftKey: true` - Shift on Digit2
        # is `@` on every keyboard - while `press("Digit2")` sent `@` (B270).
        if len(descr["key"]) == 1:
            out.setdefault(descr["key"], dict(descr, shifted=shifted))
        # ⛔ AND A CHARACTER THAT ONLY EXISTS WITH SHIFT SAYS SO: `@`, `!`,
        # `A` are typed with Shift down, and `down` holds it for them
        # whoever forgot to (see `down`).
        if shifted:
            out.setdefault(shifted["key"],
                           dict(shifted, shifted=None, needs_shift=True))
        # ⛔ AND A CHARACTER THAT ONLY EXISTS WITH ALTGR SAYS SO: `@` on an
        # Italian keyboard is AltGr+ò, `{` on it is AltGr+Shift+è; `down`
        # and `press` hold AltGraph (and Shift) for them.
        if altgr:
            out.setdefault(altgr["key"], dict(altgr, shifted=None, needs_altgr=True))
        if shift_altgr:
            out.setdefault(shift_altgr["key"],
                           dict(shift_altgr, shifted=None, needs_shift=True,
                                needs_altgr=True))
    return out


_CLOSURES: dict = {}


def closure_for(layout: str) -> dict:
    """The closure of a layout (KLID or alias), built once."""
    layout = LAYOUT_ALIASES.get(layout, layout)
    if layout not in _CLOSURES:
        _CLOSURES[layout] = _build_closure(layout)
    return _CLOSURES[layout]


#: The US keyboard's closure: what a session with no layout of its own types on.
LAYOUT_CLOSURE = closure_for("us")


def _parse_profiles(layout) -> list:
    """(LANGID, KLID) pairs from Windows' form, KLIDs joined by commas (each its
    own language, numbered), or a list of either."""
    if isinstance(layout, str):
        layout = [x.strip() for x in layout.replace(";", ",").split(",") if x.strip()] or ["us"]
    out = []
    for n, item in enumerate(layout):
        if isinstance(item, (tuple, list)):
            lang, klid = item
        elif ":" in item:
            lang, klid = item.split(":", 1)
        else:
            lang, klid = "%04x" % (0xF000 + n), item
        out.append((lang.lower(), klid if klid in LAYOUT_ALIASES else klid.upper()))
    return out


class Keyboard:
    """The state of modifiers and pressed keys, plus the four verbs.

    STATE is the reason this is a class and not three functions:
    `press("Shift+a")` must send `A` and not `a`, and to know that it must
    remember that Shift is down between Shift's keydown and `a`'s.
    """

    def __init__(self, connection, session: str, persona=None, *, acts,
                 latency=None, layout="us"):
        self.c = connection
        self.session = session
        self.modifiers: set = set()
        self.pressed: set = set()
        #: The codes `down` pressed Shift for on its own (see `down`), so `up`
        #: of the same key releases it; the same for AltGraph.
        self._implicit_shift: set = set()
        self._implicit_altgr: set = set()
        #: The codes `down` sent as the lowercase key of a shortcut (see `_shortcut_letter`), so `up` of the same
        #: name releases that key even if Control was let go first.
        self._lowered: set = set()
        #: The input profiles this persona has (`profiles_for`), the first one
        #: active: which key a character is, and with which modifiers. Windows'
        #: own form (LANGID:KLID joined by semicolons), or KLIDs joined by
        #: commas (each its own language), or a list; "us" when nothing says.
        #:
        #: ⛔ A CHARACTER THE ACTIVE KEYBOARD LACKS IS TYPED ON ONE THAT HAS
        #: IT, with its key events, switched to as a user switches: left Alt +
        #: left Shift to the next input language, Ctrl + Shift to the next
        #: keyboard of the same language (msctf's defaults with no Toggle
        #: values). Windows keeps one active keyboard per language, and so does
        #: this class. Only a character no keyboard has is inserted as text.
        self.profiles = _parse_profiles(layout)
        self.layouts = [klid for _lang, klid in self.profiles]
        self._closures = [closure_for(l) for l in self.layouts]
        self._languages = list(dict.fromkeys(lang for lang, _klid in self.profiles))
        #: The active keyboard of each language, as Windows remembers it.
        self._current = {lang: next(i for i, (l, _k) in enumerate(self.profiles) if l == lang)
                         for lang in self._languages}
        self._active = 0
        #: ⛔ AN OPEN CASE, DECLARED: a right-to-left system. msctf gives the
        #: switch between keyboards of one language no shortcut when
        #: SM_MIDEASTENABLED is set, and what sets it is not yet known (the
        #: bit of gpsi->dwSRVIFlags; win32k's RTL test reads the UI language).
        #: Until it is, a persona whose language is written right to left
        #: never switches keyboard inside a language: a character only another
        #: keyboard of the same language has is inserted as text.
        first = int(self.profiles[0][0], 16)
        self._intra_switch = not ((first & 0x3FF) in _RTL_PRIMARY or first in _RTL_LANGIDS)
        #: ⛔ THE RHYTHM HAS AN OWNER NOW, and this is it.
        #:
        #: A keypress used to be two protocol messages back to back, so a page
        #: saw `keydown` and `keyup` about 2.4 ms apart and two characters
        #: about 5 ms apart, against the 60-250 ms a hand produces. No probe is
        #: needed to collect that: a site only has to listen, and a password
        #: field is where the listening is already installed.
        #:
        #: The persona is DRAWN FROM THE SESSION SEED and computed by the
        #: wrapper, not here: this class obeys a plan, it does not invent one.
        #: `None` means no rhythm, which is what a caller who turned humanising
        #: off asked for.
        self.persona = persona
        #: One stream per typed string, so two `type()` calls in a session do
        #: not replay the same intervals. Without it a page could match the
        #: sequence of gaps between two form fields.
        #:
        #: ⛔ THE PAGE'S `acts`, NOT A COUNTER OF ITS OWN. This was an integer
        #: starting at 0 in every keyboard, and there is one keyboard per page,
        #: so the first string typed in every tab of a session had the same
        #: intervals. `Actions` hands in its page's numbering, and it is
        #: required for the reason given there: a default is how a page would
        #: quietly be numbered as the first one again.
        self.acts = acts
        #: How long after its birth each key reaches the page
        #: (`_behaviour.InputLatency`, [B299]), drawn per key event; None
        #: sends none, and the engine hands the key over as it is born.
        self.latency = latency

    # ── the resolution ──────────────────────────────────────────────────────
    @property
    def layout(self) -> str:
        """The active keyboard."""
        return self.layouts[self._active]

    @property
    def _closure(self) -> dict:
        return self._closures[self._active]

    def _reachable(self, board: int, current: dict) -> bool:
        """Whether a user can make `board` active: always, unless switching
        inside a language has no shortcut (the open right-to-left case), when
        only each language's active keyboard is reachable."""
        return self._intra_switch or current[self.profiles[board][0]] == board

    def _keyboard_of(self, name: str, active: int, current: Optional[dict] = None) -> Optional[int]:
        """Which keyboard `name` is typed on with `active` the active one: the
        active one if it has it, else the reachable one that does in the fewest
        presses, as a user goes (back to Armenian, a letter both Armenian
        keyboards have is typed on the one Armenian was left on), Windows' order
        breaking a tie; None when none has it."""
        current = self._current if current is None else current
        if name in self._closures[active]:
            return active
        best = None
        for i, closure in enumerate(self._closures):
            if name in closure and self._reachable(i, current):
                alt, ctrl, _a, _c = self._steps(i, active, current)
                if best is None or alt + ctrl < best[0]:
                    best = (alt + ctrl, i)
        return None if best is None else best[1]

    def _switch_for(self, name: str) -> None:
        """Make active the keyboard `name` is typed on, when it is another."""
        self._switch_to(self._keyboard_of(name, self._active))

    def _chord(self, first: str) -> None:
        """`first` + left Shift, pressed and released with this hand's timing:
        Windows switches once both are up, and the page sees both keys
        (widget/windows hands every key message it gets to the page)."""
        lead_ms, lag_ms = self._plan_shift(1)[0]
        self.down(first)
        _sleep_ms(lead_ms)
        self.down("ShiftLeft")
        _sleep_ms(self._dwell_ms())
        self.up("ShiftLeft")
        _sleep_ms(lag_ms)
        self.up(first)

    def _steps(self, board: int, active: int, current: dict) -> tuple:
        """(Alt+Shift presses, Ctrl+Shift presses) from `active` to `board`, and
        the state after: languages step forward in their order, the keyboards
        of one language in theirs, and a language comes back on the keyboard it
        was left on."""
        lang = self.profiles[board][0]
        alt = 0
        if lang != self.profiles[active][0]:
            order = self._languages
            alt = (order.index(lang) - order.index(self.profiles[active][0])) % len(order)
            active = current[lang]
        ctrl = 0
        if active != board:
            own = [i for i, (l, _k) in enumerate(self.profiles) if l == lang]
            ctrl = (own.index(board) - own.index(active)) % len(own)
        current = dict(current, **{lang: board})
        return alt, ctrl, board, current

    def _switch_to(self, board: Optional[int]) -> None:
        """Make keyboard `board` the active one, the way a user does: left Alt
        + left Shift until its language, then Ctrl + Shift until the keyboard.
        Nothing is sent when it is already the active one."""
        if board is None or board == self._active:
            return
        alt, ctrl, self._active, self._current = self._steps(board, self._active, self._current)
        for _ in range(alt):
            self._chord("AltLeft")
        for _ in range(ctrl):
            self._chord("ControlLeft")

    def _shortcut_letter(self, key: str, held=()) -> str:
        """A capital letter under Control or Meta (and no Shift) is that letter's own key, lowercase.

        ⛔ `press("Control+A")` IS Control+A, NOT Control+Shift+A. The name `A`
        is a character that only exists with Shift (`needs_shift`), and the
        floor of B270 held Shift for it, so the browser got Control+Shift+A:
        `Control+A` did not select all, `Control+C` did not copy, on every
        field of every page, while `Control+a` worked. A hand that presses the
        select-all shortcut does not hold Shift, and the key reports `key: "a"`
        (a capital would need Shift or CapsLock). `Control+Shift+A` is still
        what it says: with Shift held, by the caller or by `held`, nothing
        changes. Only A-Z; any other character that needs Shift is a symbol and
        keeps its Shift.
        """
        if len(key) != 1 or not (key.isascii() and key.isupper()):
            return key
        names = set(self.modifiers)
        for m in held:
            try:
                names.add(self.describe(m)["key"])
            except UnknownKey:
                pass  # the press raises for it where it sends the key
        if "Shift" in names or not names & {"Control", "Meta"}:
            return key
        return key.lower()

    def describe(self, key: str) -> dict:
        name = "Control" if key == "ControlOrMeta" else key
        d = self._closure.get(name)
        if d is None:
            raise UnknownKey(
                "unknown key: %r. Valid names are the layout's `code` "
                "values (KeyA, Digit1, Enter), a single character, or an "
                "alias (Shift, Control, Alt, Meta)." % key)
        shift, altgr = "Shift" in self.modifiers, "AltGraph" in self.modifiers
        if altgr and shift and d.get("shift_altgr"):
            d = d["shift_altgr"]
        elif altgr and d.get("altgr"):
            d = d["altgr"]
        elif shift and d.get("shifted"):
            d = d["shifted"]
        d = dict(d)
        # ⛔ With a modifier other than Shift held down, `text` does NOT
        # come out: `Control+a` does not write an "a" into the field. Read
        # in the driver, and without this line a `press("Control+a")` would
        # insert the character. AltGraph is the other exception: it is how
        # a European keyboard reaches `@`, `[`, `{`.
        if self.modifiers - {"Shift", "AltGraph"}:
            d["text"] = ""
        d.update(FIREFOX_OVERRIDES.get(d["key"], {}))
        return d

    def modifier_mask(self) -> int:
        m = 0
        for name in self.modifiers:
            m |= MODIFIER_MASK.get(name, 0)
        return m

    def needs_shift(self, key: str) -> bool:
        """True for a character that only exists with Shift down (`A`, `@`,
        `!`) - by name, never by key code: `Digit2` is a key, `@` is what Shift
        makes of it."""
        d = self._closure.get(key)
        return bool(d and d.get("needs_shift"))

    def needs_altgr(self, key: str) -> bool:
        """True for a character that only exists with AltGr down on this
        keyboard (`@` on an Italian one, `[` on a German one)."""
        d = self._closure.get(key)
        return bool(d and d.get("needs_altgr"))

    # ── the verbs ────────────────────────────────────────────────────────────
    def down(self, key: str) -> None:
        # ⛔ A CHARACTER THAT ONLY EXISTS WITH SHIFT IS NEVER SENT WITHOUT IT
        # (B270). `press` and `type` hold Shift themselves, with the timing of
        # this hand; this is the floor under every other way in - a raw
        # `keyboard.down("@")` - so no path can send `@` with `shiftKey:
        # false`, which no keyboard produces. Released by `up` of the same key.
        asked = key
        key = self._shortcut_letter(key)
        if key != asked:
            self._lowered.add(self._closure[key]["code"])
        self._switch_for(key)
        if self.needs_altgr(key) and "AltGraph" not in self.modifiers:
            self.down("AltGraph")
            self._implicit_altgr.add(self._closure[key]["code"])
        if self.needs_shift(key) and "Shift" not in self.modifiers:
            self.down("Shift")
            self._implicit_shift.add(self._closure[key]["code"])
        d = self.describe(key)
        repeat = d["code"] in self.pressed
        self.pressed.add(d["code"])
        if d["key"] in MODIFIERS:
            self.modifiers.add(d["key"])
        text = d["text"]
        # ⛔ Enter: Gecko generates the text. Sending it explicitly inserts
        # it twice. Read in `RawKeyboardImpl3.keydown`.
        if text == "\r":
            text = ""
        self.c.send("Page.dispatchKeyEvent", self._born(
                     {"type": "keydown", "key": d["key"], "code": d["code"],
                      "keyCode": d["keyCodeWithoutLocation"],
                      "location": d["location"], "repeat": repeat,
                      "text": text}),
                     session=self.session, timeout=None)

    def up(self, key: str) -> None:
        lower = key.lower()
        if (len(key) == 1 and key.isascii() and key.isupper()
                and self._closure.get(lower, {}).get("code") in self._lowered):
            self._lowered.discard(self._closure[lower]["code"])
            key = lower
        d = self.describe(key)
        if d["key"] in MODIFIERS:
            self.modifiers.discard(d["key"])
        self.pressed.discard(d["code"])
        # ⛔ NO `text` here: Juggler raises `keyup does not support text
        # option` and typing dies halfway through.
        self.c.send("Page.dispatchKeyEvent", self._born(
                     {"type": "keyup", "key": d["key"], "code": d["code"],
                      "keyCode": d["keyCodeWithoutLocation"],
                      "location": d["location"], "repeat": False}),
                     session=self.session, timeout=None)
        if d["code"] in self._implicit_shift:
            self._implicit_shift.discard(d["code"])
            self.up("Shift")
        if d["code"] in self._implicit_altgr:
            self._implicit_altgr.discard(d["code"])
            self.up("AltGraph")

    def _born(self, params: dict) -> dict:
        """A key event's parameters with its latency, when the session has
        one: the engine creates the event, waits that long and hands it
        over, so the page's handler runs after the event's birth as it does
        for a real keyboard ([B299])."""
        from ._behaviour import born
        return born(params, self.latency, self.acts)

    def press(self, key: str, *, dwell_ms: Optional[float] = None) -> None:
        """`press("a")`, `press("Enter")`, `press("Control+Shift+KeyA")`.

        The modifiers are held down for the whole final key and released
        in reverse order, the way a hand would.

        ⛔ `dwell_ms` IS HOW LONG THE KEY STAYS DOWN, and the unit is in the
        name on purpose: see the note on `type`. Zero was the only value this
        could produce before, which put `keyup` one protocol round trip after
        `keydown` - about 2.4 ms, where a finger takes 60-120.

        It applies to the FINAL key only. A modifier is held across the whole
        press by construction, so giving it a dwell of its own would be a
        second statement about the same duration.
        """
        pieces = key.split("+")
        final, held = pieces[-1], pieces[:-1]
        # ⛔ A trailing `+` is the plus key, not a separator: `press("+")`
        # must work. `"+".split("+")` gives `["", ""]`, so the empty piece
        # has to be put back in place instead of raising
        # "unknown key: ''".
        if final == "" and held:
            final, held = "+", held[:-1]
        # ⛔ `None` means "ask the hand", not "zero". Playwright's own
        # `keyboard.press(key, delay=)` IS this number - it documents it as the
        # wait between keydown and keyup - so a caller who passes it overrides
        # the persona for that press, and a caller who passes nothing gets the
        # session's hand rather than a pipe round trip.
        if dwell_ms is None:
            dwell_ms = self._dwell_ms()
        # ⛔ A FINAL KEY THAT ONLY EXISTS WITH SHIFT GETS SHIFT, with this
        # hand's lead and release (B270): `press("@")` used to send `@` with
        # `shiftKey: false`.
        # ⛔ AND ONE THAT ONLY EXISTS WITH ALTGR GETS ALTGRAPH the same way:
        # `press("@")` on an Italian keyboard is AltGr+ò, `press("{")` is
        # AltGr+Shift+è. Each held modifier has its own lead and release.
        final = self._shortcut_letter(final, held)
        self._switch_for(final)
        holds = [m for m, needed in (("AltGraph", self.needs_altgr(final)),
                                     ("Shift", self.needs_shift(final)))
                 if needed and m not in self.modifiers and m not in held]
        timings = self._plan_shift(len(holds))
        for m in held:
            self.down(m)
        try:
            for m, (lead_ms, _) in zip(holds, timings):
                self.down(m)
                _sleep_ms(lead_ms)
            try:
                self.down(final)
                _sleep_ms(dwell_ms)
                self.up(final)
            finally:
                for m, (_, lag_ms) in reversed(list(zip(holds, timings))):
                    _sleep_ms(lag_ms)
                    self.up(m)
        finally:
            for m in reversed(held):
                self.up(m)

    def type(self, text: str, *, delay_ms: float = 0.0) -> None:
        """One key per character, with the rhythm of a hand.

        ⛔ A CHARACTER THE LAYOUT HAS NO KEY FOR IS INSERTED IN ITS PLACE, and
        the keys around it are still pressed, as Playwright's `keyboard.type`
        and pyppeteer's do, character by character. Its key is never faked:
        typing something with no key would mean sending `code: ""`, which is
        exactly the defect this file exists to not have. Until 44.39.0 the
        whole text was refused here, and of the three callers one inserted
        all of it without a key event ("café" typed with no keystroke at all)
        and two raised to the user: `page.type("#i", "café")` failed.

        ⛔ THE UNIT IS IN THE NAME, AND IT IS A FIX. The parameter used to be
        called `delay` and was handed straight to `time.sleep`, which takes
        SECONDS, while the public API documents it in MILLISECONDS. On the one
        path that actually forwarded it - the ElementHandle one - `type("abc",
        delay=100)` therefore slept for five minutes instead of 0.3 seconds.
        The six paths that dropped the value were accidentally protected from
        the bug the seventh had.

        ⛔ AND THE CALLER'S VALUE IS AN OVERRIDE, NOT THE DEFENCE. Without a
        persona this class used to emit keys as fast as the pipe allows, so the
        only thing standing between a caller and an inhuman rhythm was a
        parameter they had to know to pass - and every install that passed
        `delay=100` would have shared one flat rhythm, which is a key that
        links them. The persona is per-session and is what runs when nobody
        asks for anything.
        """
        plan = self._plan(text)
        # The keyboard each character is typed on, decided before the first
        # key: the Shift runs below depend on it.
        boards, active, current = [], self._active, dict(self._current)
        for ch in text:
            board = self._keyboard_of(ch, active, current)
            boards.append(board)
            if board is not None and board != active:
                _a, _c, active, current = self._steps(board, active, current)
        # ⛔ A RUN OF CHARACTERS THAT ONLY EXIST WITH SHIFT IS TYPED UNDER ONE
        # SHIFT, the way a hand types `ABC` or `!!` (B270). Shift goes down
        # its lead before the run's first key and comes up its lag after the
        # run's last; both are taken out of the gaps around the run, so the
        # per-key rhythm `plan_typing` drew stays what it was.
        shifted = [board is not None
                   and bool(self._closures[board][ch].get("needs_shift"))
                   and "Shift" not in self.modifiers
                   for ch, board in zip(text, boards)]
        # A run ends where the keyboard changes: the switch is pressed with
        # Shift up.
        starts = [s and (i == 0 or not shifted[i - 1] or boards[i - 1] != boards[i])
                  for i, s in enumerate(shifted)]
        ends = [s and (i == len(text) - 1 or not shifted[i + 1]
                       or boards[i + 1] != boards[i])
                for i, s in enumerate(shifted)]
        timing = iter(self._plan_shift(_floats.exact_sum(starts)))
        lead_at, lag_at = {}, {}
        for i in range(len(text)):
            if starts[i]:
                lead_at[i], lag = next(timing)
            if ends[i]:
                lag_at[i] = lag
        for i, ch in enumerate(text):
            dwell_ms, gap_ms = plan[i]
            self._switch_to(boards[i])
            if starts[i]:
                self.down("Shift")
                _sleep_ms(lead_at[i])
            if boards[i] is not None:
                self.press(ch, dwell_ms=dwell_ms)
            else:
                self.insert_text(ch)
            if i == len(text) - 1:
                if ends[i]:
                    _sleep_ms(lag_at[i])
                    self.up("Shift")
                break
            wait_ms = delay_ms if delay_ms else gap_ms
            if ends[i]:
                _sleep_ms(lag_at[i])
                self.up("Shift")
                wait_ms -= lag_at[i]
            if starts[i + 1]:
                wait_ms -= lead_at[i + 1]
            _sleep_ms(wait_ms)

    def _dwell_ms(self) -> float:
        """How long ONE key stays down, for a press that is not part of typed
        text. Zero without a persona, which is what turning humanising off
        means."""
        if self.persona is None:
            return 0.0
        return self._plan("x")[0][0]

    def _plan(self, text: str):
        """One `(dwell_ms, gap_ms)` per character, from the session's persona.

        ⛔ Falls back to no rhythm rather than to a made-up one. A default
        constant here would be the shared invariant the persona exists to
        remove, and it would be invisible: every install would type alike and
        nothing would report it.
        """
        if self.persona is None:
            return [(0.0, 0.0)] * len(text)
        from ._behaviour import plan_typing
        return plan_typing(text, self.persona, nonce=self.acts.next("typing"),
                           layout=self.layout)

    def _plan_shift(self, runs: int):
        """One `(lead_ms, lag_ms)` per run typed under Shift, from the
        session's persona; no timing without one, which is what turning
        humanising off means. Its own act stream, so drawing it moves no
        number `_plan` draws."""
        if self.persona is None or runs == 0:
            return [(0.0, 0.0)] * runs
        from ._behaviour import plan_shift
        return plan_shift(self.persona, runs, nonce=self.acts.next("typing-shift"))

    def insert_text(self, text: str) -> None:
        """The text goes in without key events. This is what is needed for
        a character that is not on the layout."""
        self.c.send("Page.insertText", {"text": text},
                     session=self.session, timeout=None)
