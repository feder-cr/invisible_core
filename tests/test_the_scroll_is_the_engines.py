"""The scroll before an action is the engine's `Page.scrollIntoViewIfNeeded`.

Until firefox-58 that command failed on every node (the engine called a method
its Element binding does not declare), so the core brought an element into view
with a script of its own run in the page's utility world, and a caller's point
(`position`) was dropped: the element was centred, not the point. The engine
now answers the command with the page's own `scrollIntoView` through the Xray,
only when needed, and honours a rect of the element; the core sends it, with
the point as a rect of size 0.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.injected import InjectedScript

pytestmark = pytest.mark.unit

S = "S1"
F = "frame-1"


class _Engine(EventListeners):
    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, method, params=None, session=None, timeout=None):
        self.sent.append((method, params, session))
        return {}


def test_the_element_is_scrolled_by_the_engine():
    engine = _Engine()
    InjectedScript(engine, S).scroll_into_view(F, "el-1")
    assert engine.sent[-1] == ("Page.scrollIntoViewIfNeeded",
                               {"frameId": F, "objectId": "el-1"}, S)
    assert not any(m.startswith("Runtime.") for m, _, _ in engine.sent)


def test_a_point_of_the_element_is_sent_as_its_rect():
    engine = _Engine()
    rect = {"x": 12.0, "y": 900.0, "width": 0, "height": 0}
    InjectedScript(engine, S).scroll_into_view(F, "el-1", rect)
    assert engine.sent[-1] == ("Page.scrollIntoViewIfNeeded",
                               {"frameId": F, "objectId": "el-1", "rect": rect}, S)
