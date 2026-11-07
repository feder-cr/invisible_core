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

**And the layout is not here**: it lives in `keylayout.py`, GENERATED from
the bundle by `scripts/gen_key_layout.py`. Here there is only how a name is
resolved, which is the algorithm of `buildLayoutClosure` read in the driver
and rewritten.
"""
from __future__ import annotations

import time
from typing import Optional

from .keylayout import LAYOUT
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


#: The keyboard a persona types on, by the language it declares. The layout
#: is the one Windows installs with that language: a persona whose locale is
#: it-IT types on an Italian keyboard, where `@` is AltGr+ò and `ò` is a key.
#: Languages with no table here type on the US keyboard, as before.
LAYOUT_OF_LANGUAGE = {"it": "it", "de": "de", "fr": "fr", "es": "es"}
LAYOUT_OF_LANGUAGE_REGION = {("pt", "BR"): "br", ("en", "GB"): "uk"}


def layout_for(languages, region: Optional[str] = None) -> str:
    """The layout name for a session's language list (`SessionLocale.languages`,
    or the `intl.accept_languages` string) and region: the first tag decides."""
    if isinstance(languages, str):
        languages = [t.strip() for t in languages.split(",") if t.strip()]
    if not languages:
        return "us"
    parts = languages[0].replace("_", "-").split("-")
    lang = parts[0].lower()
    reg = (region or (parts[1] if len(parts) > 1 and len(parts[1]) == 2 else "")).upper()
    return (LAYOUT_OF_LANGUAGE_REGION.get((lang, reg))
            or LAYOUT_OF_LANGUAGE.get(lang) or "us")


def _layout_table(layout: str) -> dict:
    """The bundle's table with the printable keys of this Windows layout in
    place of the US ones: `key`/`shiftKey`/`altGrKey`/`shiftAltGrKey` and
    Firefox's `keyCode` on Windows (59 for `;`, not Chromium's 186). Keys the
    bundle has no entry for (IntlBackslash, IntlRo, NumpadComma) are added;
    `AltRight` is `AltGraph` on a layout that has AltGr, as Firefox reports it."""
    win = WINDOWS_LAYOUTS[layout]
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
    """The closure of a layout, built once."""
    if layout not in _CLOSURES:
        _CLOSURES[layout] = _build_closure(layout)
    return _CLOSURES[layout]


#: The US keyboard's closure: what a session with no layout of its own types on.
LAYOUT_CLOSURE = closure_for("us")


class Keyboard:
    """The state of modifiers and pressed keys, plus the four verbs.

    STATE is the reason this is a class and not three functions:
    `press("Shift+a")` must send `A` and not `a`, and to know that it must
    remember that Shift is down between Shift's keydown and `a`'s.
    """

    def __init__(self, connection, session: str, persona=None, *, acts,
                 latency=None, layout: str = "us"):
        self.c = connection
        self.session = session
        self.modifiers: set = set()
        self.pressed: set = set()
        #: The codes `down` pressed Shift for on its own (see `down`), so `up`
        #: of the same key releases it; the same for AltGraph.
        self._implicit_shift: set = set()
        self._implicit_altgr: set = set()
        #: The keyboard this persona types on (`layout_for`): which key a
        #: character is, and with which modifiers. "us" when nothing says.
        self.layout = layout
        self._closure = closure_for(layout)
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
        # ⛔ A RUN OF CHARACTERS THAT ONLY EXIST WITH SHIFT IS TYPED UNDER ONE
        # SHIFT, the way a hand types `ABC` or `!!` (B270). Shift goes down
        # its lead before the run's first key and comes up its lag after the
        # run's last; both are taken out of the gaps around the run, so the
        # per-key rhythm `plan_typing` drew stays what it was.
        shifted = [self.needs_shift(ch) and "Shift" not in self.modifiers
                   for ch in text]
        starts = [s and (i == 0 or not shifted[i - 1])
                  for i, s in enumerate(shifted)]
        ends = [s and (i == len(text) - 1 or not shifted[i + 1])
                for i, s in enumerate(shifted)]
        timing = iter(self._plan_shift(sum(starts)))
        lead_at, lag_at = {}, {}
        for i in range(len(text)):
            if starts[i]:
                lead_at[i], lag = next(timing)
            if ends[i]:
                lag_at[i] = lag
        for i, ch in enumerate(text):
            dwell_ms, gap_ms = plan[i]
            if starts[i]:
                self.down("Shift")
                _sleep_ms(lead_at[i])
            if ch in self._closure:
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
        return plan_typing(text, self.persona, nonce=self.acts.next("typing"))

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
