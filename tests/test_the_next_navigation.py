"""`Lifecycle.wait_for_new_navigation`: the frame's NEXT navigation, of either
kind, after a mark taken before the command.

⛔ B290: history has same-document entries (a hash, a `pushState`), and going
to one starts no navigation, so a wait for a new navigation id never ended:
`go_back` to a hash entry waited out its whole timeout and raised, in the
three clients, where Playwright and pyppeteer answer null at once and
Marionette ends its wait on `hashchange`. Measured on invisible-playwright
0.35.2: 8.0 s and a TimeoutError for `go_back(timeout=8000)`, the same for
`go_forward`.

A state machine fed by events, tested by feeding it events.
"""
from __future__ import annotations

import threading
import time

import pytest

from invisible_core.juggler.lifecycle import Lifecycle, NavigationMark
from invisible_core.juggler.connection import EventListeners

pytestmark = pytest.mark.unit


class FakeConnection(EventListeners):
    def send(self, method, params=None, session=None, timeout=30):
        return None


def loaded_frame():
    c = FakeConnection()
    v = Lifecycle(c, "S1")
    for method, params in (
            ("Page.frameAttached", {"frameId": "F1"}),
            ("Page.navigationStarted", {"frameId": "F1", "navigationId": "N1"}),
            ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "N1",
                                          "url": "http://x/"}),
            ("Page.eventFired", {"frameId": "F1", "name": "load"})):
        c.dispatch_event(method, params, "S1")
    return c, v


def later(c, delay, *pairs):
    def run():
        time.sleep(delay)
        for method, params in pairs:
            c.dispatch_event(method, params, "S1")
    threading.Thread(target=run, daemon=True).start()


def test_the_mark_is_the_navigation_and_the_same_document_count():
    c, v = loaded_frame()
    assert v.mark("F1") == NavigationMark("N1", 0)
    c.dispatch_event("Page.sameDocumentNavigation",
                     {"frameId": "F1", "url": "http://x/#a"}, "S1")
    assert v.mark("F1") == NavigationMark("N1", 1)


def test_a_same_document_navigation_ends_the_wait_at_once():
    c, v = loaded_frame()
    since = v.mark("F1")
    later(c, 0.1, ("Page.sameDocumentNavigation",
                   {"frameId": "F1", "url": "http://x/#a"}))
    began = time.monotonic()
    assert v.wait_for_new_navigation("F1", since, "load", timeout=10) is None
    assert time.monotonic() - began < 2.0


def test_a_new_document_is_waited_for_until_its_state():
    c, v = loaded_frame()
    since = v.mark("F1")
    later(c, 0.1,
          ("Page.navigationStarted", {"frameId": "F1", "navigationId": "N2"}),
          ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "N2",
                                        "url": "http://x/2"}))
    later(c, 0.4, ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    began = time.monotonic()
    assert v.wait_for_new_navigation("F1", since, "load", timeout=10) == "N2"
    assert time.monotonic() - began >= 0.35, "answered before the new load"


def test_without_a_state_the_answer_is_the_start():
    c, v = loaded_frame()
    since = v.mark("F1")
    later(c, 0.1, ("Page.navigationStarted", {"frameId": "F1", "navigationId": "N2"}))
    assert v.wait_for_new_navigation("F1", since, None, timeout=10) == "N2"


def test_no_navigation_is_a_timeout():
    c, v = loaded_frame()
    since = v.mark("F1")
    with pytest.raises(TimeoutError, match="no new navigation on frame F1"):
        v.wait_for_new_navigation("F1", since, "load", timeout=0.3)
