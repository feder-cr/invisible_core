"""While a button is held, every move says so: `event.buttons` is the mask of
what is down, not zero.

⛔ THE DEFECT. `mouse.down(); mouse.move(...); mouse.up()` reached the page as a
press, moves with `buttons === 0`, and a release. A real mouse and stock
Playwright send `buttons === 1` on the moves of a left-button drag, so a slider
or a canvas that tests `event.buttons` never followed the pointer, and the
zero was a tell on its own. Nothing remembered what `mouse_down` had pressed.

The pressed buttons are the state of the mouse (`Actions._pressed`): set by the
press, cleared by the release, per button (left 1, right 2, middle 4), and
carried by every move on every path - plain, stepped, and the drawn glide.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler._behaviour import PageActs
from invisible_core.juggler.actions import Actions

pytestmark = pytest.mark.unit


class _Fake:
    def __init__(self):
        self.sent = []

    def send(self, method, params, **kw):
        self.sent.append((method, dict(params)))
        return {}


def _actions(**kw):
    c = _Fake()
    return Actions(c, "S", None, None, acts=PageActs(1), **kw), c


def _events(c):
    return [(p["type"], p["buttons"]) for m, p in c.sent
            if m == "Page.dispatchMouseEvent"]


def test_a_left_drag_moves_with_the_left_button_down_and_ends_at_zero():
    a, c = _actions()
    a.move(5, 5)
    a.mouse_down()
    a.move(40, 40)
    a.move(90, 90, steps=3)
    a.mouse_up()
    a.move(100, 100)
    assert _events(c) == [
        ("mousemove", 0),
        ("mousedown", 1),
        ("mousemove", 1),
        ("mousemove", 1), ("mousemove", 1), ("mousemove", 1),
        ("mouseup", 0),
        ("mousemove", 0),
    ]


def test_each_button_is_its_own_bit_and_a_release_leaves_the_others():
    a, c = _actions()
    a.mouse_down(button=0)
    a.mouse_down(button=2)
    a.move(10, 10)
    a.mouse_up(button=0)
    a.move(20, 20)
    a.mouse_up(button=2)
    a.mouse_down(button=1)
    a.move(30, 30)
    a.mouse_up(button=1)
    assert _events(c) == [
        ("mousedown", 1), ("mousedown", 3), ("mousemove", 3),
        ("mouseup", 2), ("mousemove", 2), ("mouseup", 0),
        ("mousedown", 4), ("mousemove", 4), ("mouseup", 0),
    ]


def test_the_drawn_glide_and_the_approach_carry_the_held_buttons():
    a, c = _actions(session_seed=7)
    a.mouse_down()
    a.glide_to(200, 150)
    a._approach((210, 160))
    a.mouse_up()
    moves = [b for t, b in _events(c) if t == "mousemove"]
    assert moves and set(moves) == {1}
    assert _events(c)[-1] == ("mouseup", 0)


def test_a_click_while_another_button_is_held_keeps_that_one():
    a, c = _actions()
    a.mouse_down(button=2)
    a.click_at(10, 10)
    a.mouse_up(button=2)
    assert [e for e in _events(c) if e[0] != "mousemove"] == [
        ("mousedown", 2), ("mousedown", 3), ("mouseup", 2), ("mouseup", 0)]
