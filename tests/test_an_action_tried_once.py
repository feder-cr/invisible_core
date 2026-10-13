"""`_retry(once=True)`: one attempt, its reason at once, and the timeout left
to what the action itself waits for.

Selenium (Marionette) and pyppeteer try an action once and raise what they
found; only Playwright waits for an element to become actionable. Before
`once` the two clients had only the timeout to say "one attempt" with, and a
timeout of 0 also took away the time the action spends on its own waits, the
typing persona's pause before the first key among them.
"""
from __future__ import annotations

import time

import pytest

from invisible_core.juggler.actions import Actions, ElementNotActionable

pytestmark = pytest.mark.unit


class _Lifecycle:
    main_frame = "F1"


class _Injected:
    def __init__(self, ok):
        self.ok = ok
        self.turns = 0

    def element_states(self, frame, element, states):
        self.turns += 1
        return {"ok": True} if self.ok else {"ok": False, "missing": "enabled"}

    def dispose(self, frame, element):
        pass


def _actions(ok):
    actions = Actions.__new__(Actions)
    actions.lifecycle = _Lifecycle()
    actions.inj = _Injected(ok)
    return actions


def test_once_raises_after_one_turn_with_its_reason():
    actions = _actions(ok=False)
    began = time.monotonic()
    with pytest.raises(ElementNotActionable, match="missing enabled"):
        actions._retry("<element>", lambda f, el, p: "acted", timeout=30,
                       element_id="E1", needs_point=False, once=True)
    assert actions.inj.turns == 1
    assert time.monotonic() - began < 1.0


def test_without_once_the_loop_tries_again_until_the_timeout():
    """The control arm: the same element, the Playwright loop."""
    actions = _actions(ok=False)
    with pytest.raises(ElementNotActionable):
        actions._retry("<element>", lambda f, el, p: "acted", timeout=0.3,
                       element_id="E1", needs_point=False)
    assert actions.inj.turns > 1


def test_once_acts_when_the_element_is_actionable():
    actions = _actions(ok=True)
    assert actions._retry("<element>", lambda f, el, p: "acted", timeout=30,
                          element_id="E1", needs_point=False, once=True) == "acted"
