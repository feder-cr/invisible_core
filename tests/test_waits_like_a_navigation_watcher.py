"""The two rules of pyppeteer's `NavigatorWatcher`, as the lifecycle offers
them to a client that asks: a wait FOLLOWS a document that replaced the
navigation, and the frame's CHILDREN must reach the state too.

pyppeteer completes a navigation wait when the frame carries a document other
than the one it had when the wait began and that document, and every child
frame, has the expected lifecycle events (`navigator_watcher.py`
`_checkLifecycleComplete` and `_checkLifecycle`). It never says "interrupted":
a document that committed in place of the one asked for is waited on in its
place, and `goto` answers with that document's response. Playwright and
Marionette decide on the first document instead, so both rules are opt-in and
the defaults are unchanged.

The children are counted when they have started loading, as puppeteer does
since it stopped waiting for frames that never would (`_hasStartedLoading`):
measured on firefox-44, a `loading=lazy` iframe below the fold never starts,
and counting it would keep every wait to its timeout.
"""
from __future__ import annotations

import threading
import time

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import Lifecycle, NavigationInterrupted

pytestmark = pytest.mark.unit


class FakeConnection(EventListeners):
    def __init__(self, responses=None):
        super().__init__()
        self._responses = responses or {}

    def send(self, method, params=None, session=None, timeout=30):
        return self._responses.get(method)

    def send_placed(self, method, params=None, session=None, timeout=30):
        return self._responses.get(method), self.delivered


def lifecycle(responses=None):
    c = FakeConnection(responses)
    return c, Lifecycle(c, "S1")


def events(v, *pairs):
    for method, params in pairs:
        v.c.dispatch_event(method, params, "S1")


def _replaced_before_commit(v):
    """P is the page being left, A is ours and has started; then P commits in
    A's place, with a load (the known-bad input of the raising rule)."""
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "P"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "A"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "P",
                                         "url": "http://p/", "name": ""}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))


def test_a_wait_that_follows_settles_on_the_document_that_replaced_ours():
    c, v = lifecycle()
    _replaced_before_commit(v)
    started = time.monotonic()
    landed = v.wait_for_state("F1", "load", navigation="A", timeout=5,
                              replaced="follow")
    assert landed == "P"
    assert time.monotonic() - started < 1


def test_a_wait_that_does_not_follow_still_says_it_was_interrupted():
    c, v = lifecycle()
    _replaced_before_commit(v)
    with pytest.raises(NavigationInterrupted):
        v.wait_for_state("F1", "load", navigation="A", timeout=5)


def test_a_wait_answers_with_the_document_it_ended_on():
    """Ours committed, then the page replaced itself ([B228]): the wait ends
    on the new document, and says which."""
    c, v = lifecycle()
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "A"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "A",
                                         "url": "http://a/", "name": ""}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "B"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "B",
                                         "url": "http://a/next", "name": ""}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    assert v.wait_for_state("F1", "load", navigation="A", timeout=0.5,
                            replaced="follow") == "B"


def test_a_replaced_value_nobody_defined_is_refused():
    c, v = lifecycle()
    _replaced_before_commit(v)
    with pytest.raises(ValueError):
        v.wait_for_state("F1", "load", navigation="A", timeout=1,
                         replaced="ignore")


def _parent_with_children(v):
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "A"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "A",
                                         "url": "http://a/", "name": ""}),
           ("Page.eventFired", {"frameId": "F1", "name": "DOMContentLoaded"}),
           # A child that started loading and has not reached it yet...
           ("Page.frameAttached", {"frameId": "C1", "parentFrameId": "F1"}),
           ("Page.navigationStarted", {"frameId": "C1", "navigationId": "c1"}),
           # ... its own child, done...
           ("Page.frameAttached", {"frameId": "C2", "parentFrameId": "C1"}),
           ("Page.navigationStarted", {"frameId": "C2", "navigationId": "c2"}),
           ("Page.navigationCommitted", {"frameId": "C2", "navigationId": "c2",
                                         "url": "about:blank", "name": ""}),
           ("Page.eventFired", {"frameId": "C2", "name": "load"}),
           # ... and a lazy one that never starts.
           ("Page.frameAttached", {"frameId": "L1", "parentFrameId": "F1"}))


def test_the_children_hold_the_wait_until_every_one_that_started_has_the_state():
    c, v = lifecycle()
    _parent_with_children(v)
    # The frame alone is there already.
    v.wait_for_state("F1", "domcontentloaded", navigation="A", timeout=0.2)
    with pytest.raises(TimeoutError):
        v.wait_for_state("F1", "domcontentloaded", navigation="A", timeout=0.3,
                         children=True)

    def later():
        time.sleep(0.2)
        events(v,
               ("Page.navigationCommitted", {"frameId": "C1",
                                             "navigationId": "c1",
                                             "url": "http://a/child",
                                             "name": ""}),
               ("Page.eventFired", {"frameId": "C1",
                                    "name": "DOMContentLoaded"}))
    threading.Thread(target=later, daemon=True).start()
    started = time.monotonic()
    v.wait_for_state("F1", "domcontentloaded", navigation="A", timeout=5,
                     children=True)
    assert 0.15 < time.monotonic() - started < 2


def test_goto_that_follows_answers_with_the_document_it_landed_on():
    c, v = lifecycle({"Page.navigate": {"navigationId": "A"}})
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "O"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "O",
                                         "url": "http://o/", "name": ""}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))

    def later():
        time.sleep(0.1)
        events(v,
               ("Page.navigationStarted", {"frameId": "F1",
                                           "navigationId": "A"}),
               ("Page.navigationStarted", {"frameId": "F1",
                                           "navigationId": "P"}),
               ("Page.navigationCommitted", {"frameId": "F1",
                                             "navigationId": "P",
                                             "url": "http://p/", "name": ""}),
               ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    threading.Thread(target=later, daemon=True).start()
    result = v.goto("http://a/", until="load", timeout=5, replaced="follow",
                    children=True)
    assert result["navigationId"] == "A"
    assert result["landed"] == "P"
    assert result["url"] == "http://p/"
