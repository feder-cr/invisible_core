"""The retry cycle and the actions, without a browser.

⛔ This is the piece that fails silently: if a condition is checked ONCE
and then acted on, the page can change between the check and the action.
It does not break - it breaks one time in twenty.

Until 38.34.0 this file lived in invisible-playwright, invisible-selenium and
invisible-puppeteer, three copies a few lines apart, testing the copies of
the client each carried. The client is one module here now (decision D85),
and so are its tests. The ones that need a real browser stay in the
Playwright wrapper, whose e2e job has the engine, and run there in both
pointer modes (`engine_approach` False and True).
"""
from __future__ import annotations

import pytest

from invisible_core.juggler._behaviour import PageActs
from invisible_core.juggler.actions import Actions

pytestmark = pytest.mark.unit


def test_a_lifecycle_without_a_frame_SAYS_SO_instead_of_timing_out():
    class FakeLifecycle:
        main_frame = None
    actions = Actions(None, "S", FakeLifecycle(), None, acts=PageActs(1))
    with pytest.raises(RuntimeError) as e:
        actions.click("#x")
    assert "main frame" in str(e.value)


def test_typing_does_NOT_send_keypress():
    """⛔ Juggler's `_dispatchKeyEvent` only knows `keydown` and `keyup`, and
    raises `Unknown type` on everything else. A `keypress` - which is what
    you would write out of habit - makes the entire typing action fail.

    ⛔ And now it asks the EVENTS instead of the source of `_type`.
    Reading the text of a function ties the test to WHERE the code lives: the
    typing moved into `keyboard.py` and this test went red over a property
    that was still perfectly true. A test that breaks when the code moves
    teaches people to delete it.
    """
    from invisible_core.juggler.keyboard import Keyboard

    class Fake:
        def __init__(self):
            self.types = []

        def send(self, method, params, **kw):
            self.types.append(params.get("type"))
            return {}

    c = Fake()
    Keyboard(c, "S", acts=PageActs(1)).type("ab")
    assert c.types, "no key event"
    assert set(c.types) == {"keydown", "keyup"}, (
        "types Juggler rejects: %r" % sorted(set(c.types)))


class _Fake:
    """A connection that records instead of talking to the browser."""

    def __init__(self):
        self.events = []

    def send(self, method, params, **kw):
        self.events.append(params)
        return {}


def test_the_button_mask_is_NOT_a_shifted_one():
    """⛔ THE KNOWN-BAD INPUT OF THE MASKS, and it comes from a real defect
    that lived in this file.

    `click` wrote `buttons = 1 << button`, which gives 1 for the left button
    - so it looks right - and gets the other two wrong: 2 for the middle and
    4 for the right. Firefox wants them the other way around, read in
    `toButtonsMask2` of the bundle: left 1, RIGHT 2, MIDDLE 4. A right click
    therefore came out declaring the middle button pressed, with the action
    succeeding perfectly and no test able to see it.

    The mutation to reintroduce to try this test: `BUTTON_MASK` set to
    `{0: 1, 1: 2, 2: 4}`.
    """
    from invisible_core.juggler.keyboard import BUTTON_MASK
    assert BUTTON_MASK == {0: 1, 1: 4, 2: 2}, (
        "left 1, right 2, middle 4 - not 1<<button")


def test_the_modifiers_carry_the_FIREFOX_mask():
    """⛔ It is not `1 << index` and it is not Gecko's: Alt 1, Control 2,
    Shift 4, Meta 8, read in `toModifiersMask2`. Juggler translates it itself
    into `nsIDOMWindowUtils.MODIFIER_*`, so sending Gecko's constants from
    here would give wrong modifiers with no error at all."""
    from invisible_core.juggler.keyboard import MODIFIER_MASK
    assert MODIFIER_MASK == {"Alt": 1, "Control": 2, "Shift": 4,
                              "Meta": 8}


def test_the_layout_carries_the_keycode_WITHOUT_location():
    """⛔ `Page.dispatchKeyEvent` wants `keyCodeWithoutLocation`, not
    `keyCode`, and the two differ exactly on the keys that exist twice:
    `ShiftLeft` has 160 and 16. A real Firefox puts 16 in the event."""
    from invisible_core.juggler.keyboard import LAYOUT_CLOSURE
    s = LAYOUT_CLOSURE["ShiftLeft"]
    assert s["keyCode"] == 160 and s["keyCodeWithoutLocation"] == 16
    assert LAYOUT_CLOSURE["Shift"]["code"] == "ShiftLeft"


def test_a_key_that_does_not_exist_gets_REJECTED_instead_of_coming_out_empty():
    """⛔ The defect that made `keyboard.py` come into being.

    The first `_type` sent `code: ""` and `keyCode: 0` for every character:
    the event goes out, the text goes in, the action succeeds and the tests
    pass, while the page reads an empty `event.code` on a key that every
    real Firefox names.
    """
    from invisible_core.juggler.keyboard import Keyboard, UnknownKey
    c = _Fake()
    t = Keyboard(c, "S", acts=PageActs(1))
    with pytest.raises(UnknownKey):
        t.type(chr(0x4E2D))
    assert not c.events, "sent an event for a key that does not exist"
    t.press("a")
    assert all(e["code"] and e["keyCode"] for e in c.events), (
        "an event came out with empty code or keyCode: %r" % c.events)


def test_shift_changes_the_key_and_control_removes_the_text():
    """The modifier state is the reason the keyboard is a class. ⛔ And
    `Control+a` must NOT insert an "a": with a modifier other than Shift
    the `text` comes out empty, read in the driver."""
    from invisible_core.juggler.keyboard import Keyboard
    c = _Fake()
    t = Keyboard(c, "S", acts=PageActs(1))
    t.press("Shift+KeyA")
    down = [e for e in c.events
            if e["type"] == "keydown" and e["code"] == "KeyA"]
    assert down[0]["key"] == "A" and down[0]["text"] == "A"

    c.events.clear()
    t.press("Control+KeyA")
    down = [e for e in c.events
            if e["type"] == "keydown" and e["code"] == "KeyA"]
    assert down[0]["key"] == "a" and down[0]["text"] == "", (
        "Control+a inserted the character: %r" % down[0])
    assert not t.modifiers, "a modifier stayed down after the press"


def test_keyup_NEVER_carries_the_text():
    """⛔ Juggler raises `keyup does not support text option` and the typing
    dies halfway through. Read in its `_dispatchKeyEvent`, not deduced."""
    from invisible_core.juggler.keyboard import Keyboard
    c = _Fake()
    Keyboard(c, "S", acts=PageActs(1)).type("aZ1")
    up = [e for e in c.events if e["type"] == "keyup"]
    assert up and all("text" not in e for e in up)


def test_a_bare_string_does_NOT_go_through_the_option_filter():
    """⛔ THE KNOWN-BAD INPUT OF THE OPTIONS, and it comes from a measured
    fault.

    The injected script's filter starts from `matches = true` and narrows it
    only if the criterion carries `valueOrLabel`, `value`, `label` or
    `index`. A bare string has none of those: every option matches and the
    FIRST one is chosen. Measured on a select with A/a and B/b, `["b"]`
    answered `['a']` and left the value at `a` - succeeded, silent, wrong.

    The mutation to reintroduce: passing `list(options)` instead of
    `_normalize_options(options)` in `select_option`.
    """
    from invisible_core.juggler.actions import _normalize_options
    assert _normalize_options(["b"]) == [{"valueOrLabel": "b"}]
    assert _normalize_options([{"value": "b"}]) == [{"value": "b"}]
    assert _normalize_options([{"index": 1}]) == [{"index": 1}]


class _Recorder:
    """A connection and an injected script that answer without a browser:
    enough of both for the retry cycle to reach an action's own body."""

    main_frame = "main"

    def __init__(self, **answers):
        self.answers = answers
        self.sent = []

    # the connection
    def send(self, method, params=None, session=None, timeout=None, abort=None):
        self.sent.append((method, dict(params or {})))
        if method == "Page.getContentQuads":
            return {"quads": [{"p1": {"x": 390, "y": 295}, "p2": {"x": 410, "y": 295},
                               "p3": {"x": 410, "y": 305}, "p4": {"x": 390, "y": 305}}]}
        return {}

    # the injected script
    def evaluate(self, frame, expression, **kw):
        return {"w": 1280, "h": 800}

    def check_hit_target(self, frame, element, point):
        return "done"

    def call(self, frame, declaration, *args, **kw):
        for needle, answer in self.answers.items():
            if "injected.%s(" % needle in declaration:
                return answer
        return "done"

    def query_selector(self, frame, selector, strict=False):
        return "element"

    def element_states(self, frame, element, states):
        return {"ok": True}

    def scroll_into_view(self, frame, element):
        return False

    def dispose(self, frame, element):
        pass


def _recording_actions(**answers):
    rec = _Recorder(**answers)
    a = Actions.__new__(Actions)
    a.lifecycle = rec
    a.inj = rec
    a.c = rec
    a.session = "s"
    a.keyboard = None
    return a, rec


def test_select_option_hands_the_resolved_options_to_the_engine():
    """⛔ THE PAGE ONLY RESOLVES; THE ENGINE SELECTS. `injected.selectOptions`
    answers which option indices were meant, and `Page.selectOptions` selects
    them through the dropdown's own path, so Firefox fires `input`/`change`
    itself (and nothing for an option that was already selected). The
    known-bad input: select in the page and ask the engine for the events,
    which is the command the engine no longer has."""
    a, rec = _recording_actions(
        selectOptions={"indices": [2], "values": ["c"]})
    assert a.select_option("#s", ["c"], timeout=2.0) == ["c"]
    assert ("Page.selectOptions",
            {"frameId": "main", "objectId": "element", "indices": [2]}) in rec.sent
    assert "Page.dispatchTrustedInputEvents" not in [m for m, _ in rec.sent]


def test_a_picked_value_is_committed_by_the_engine():
    """A date, a color or a range is not typed: `injected.fill` answers the
    value it would hold, and the engine commits it with `Page.setUserInput`,
    Firefox's own path for a value a user picks. Nothing is typed and no event
    is requested."""
    a, rec = _recording_actions(fill={"setUserInput": "2026-01-15"})
    a.fill("#d", "2026-01-15", timeout=2.0)
    assert ("Page.setUserInput",
            {"frameId": "main", "objectId": "element",
             "value": "2026-01-15"}) in rec.sent
    assert "Page.dispatchTrustedInputEvents" not in [m for m, _ in rec.sent]
