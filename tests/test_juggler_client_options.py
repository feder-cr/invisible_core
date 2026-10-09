"""What each client does its own way is an OPTION of the one Juggler client.

Until 38.34.0 these lived in two different copies of the client: the
Playwright wrapper's had no `abort`, no engine-drawn approach and no
`glide_to`; Selenium's and Puppeteer's had them and lacked five remedies of the
wrapper's. One module now carries both halves, so each half is pinned here
against the module, not against a client.
"""
from __future__ import annotations

import os
import time

import pytest

from invisible_core.juggler._behaviour import PageActs
from invisible_core.juggler.actions import Actions
from invisible_core.juggler.connection import Connection, Interrupted, ProtocolError

POINT = (300.0, 200.0)


class _Lifecycle:
    main_frame = "frame-1"


class _Injected:
    def evaluate(self, *a, **kw):
        return {"w": 1280, "h": 800}


class _Engine:
    """Answers every mouse event with an id and remembers where it went."""

    def __init__(self):
        self.moves: list = []

    def send(self, method, params=None, session=None, timeout=None, abort=None):
        if method == "Page.dispatchMouseEvent":
            if params["type"] == "mousemove":
                self.moves.append((params["x"], params["y"]))
            return {"eventId": len(self.moves)}
        return {}


def _actions(engine_approach: bool, seed=1234):
    return Actions(_Engine(), "s", _Lifecycle(), _Injected(), acts=PageActs(1),
                   session_seed=seed, motion_budget_s=0.2,
                   engine_approach=engine_approach)


@pytest.mark.unit
def test_with_a_client_cursor_the_approach_is_the_one_event_onto_the_point():
    """The Playwright wrapper's cursor has already walked onto the element."""
    a = _actions(engine_approach=False)
    assert a._approach(POINT) == 1
    assert a.c.moves == [POINT]


@pytest.mark.unit
def test_without_a_client_cursor_the_engine_draws_the_approach():
    """Known-bad in the wrapper's copy for Selenium: every click arrived as a
    single jump from wherever the pointer was."""
    a = _actions(engine_approach=True)
    sent = a._approach(POINT)
    assert sent > 1
    assert a.c.moves[-1] == POINT and len(a.c.moves) == sent


@pytest.mark.unit
@pytest.mark.parametrize("engine_approach", [False, True])
def test_a_hover_approach_leaves_the_last_move_to_its_commit(engine_approach):
    """Two identical moves back to back are a pair no device produces."""
    a = _actions(engine_approach=engine_approach)
    a._approach(POINT, stop_short=True)
    assert POINT not in a.c.moves


@pytest.mark.unit
def test_glide_to_draws_a_path_for_a_caller_driving_the_pointer():
    """Selenium's ActionChains move the pointer themselves."""
    a = _actions(engine_approach=False)
    assert a.glide_to(*POINT) > 1
    assert a.c.moves[-1] == POINT


@pytest.mark.unit
def test_without_humanising_every_approach_is_one_event():
    a = _actions(engine_approach=True, seed=None)
    assert a._approach(POINT) == 1


def _pipe_connection():
    """A real Connection whose browser never answers."""
    to_r, to_w = os.pipe()
    from_r, from_w = os.pipe()
    c = Connection(to_w, from_r)
    return c, (to_r, from_w)


@pytest.mark.unit
def test_an_abort_condition_ends_the_wait_with_Interrupted():
    """A click that opened `alert()` suspends the page's process: the reply
    cannot come, and the caller says so with `abort`."""
    c, keep = _pipe_connection()
    t0 = time.monotonic()
    with pytest.raises(Interrupted):
        c.send("Page.pointerLanded", {}, timeout=10, abort=lambda: True)
    assert time.monotonic() - t0 < 2


@pytest.mark.unit
def test_without_abort_the_wait_is_the_timeout():
    c, keep = _pipe_connection()
    with pytest.raises(ProtocolError) as e:
        c.send("Page.pointerLanded", {}, timeout=0.2)
    assert not isinstance(e.value, Interrupted)
