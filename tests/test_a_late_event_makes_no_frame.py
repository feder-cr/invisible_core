"""A frame is born of `Page.frameAttached` only: a late event of a frame that left, or of one never announced, is
not a frame.

`Lifecycle._frame` created a Frame for any id an event named. An event of a frame already detached (its
`navigationStarted`, `navigationCommitted`, `eventFired` arriving after `frameDetached`, which happens when the
frame's process reports late) put the frame back, with no parent, and the client's `page.frames` listed a ghost
that no document holds. Playwright's frame manager creates frames on `frameAttached` alone and drops the events of
a frame it does not know (`frameRequestedNavigation`, `frameCommittedSameDocumentNavigation`,
`frameAbortedNavigation`, `frameLifecycleEvent`: `if (!frame) return`).
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import Lifecycle

pytestmark = pytest.mark.unit

S = "S1"
MAIN = "mainframe-1"
CHILD = "subframe-2"


class _Engine(EventListeners):
    def send(self, method, params=None, session=None, timeout=None):
        raise AssertionError("no command is sent here: %s" % method)


def _late(fid):
    """Every event of a frame that `_apply` reads, as a frame that left would still send them."""
    return [
        ("Page.navigationStarted", {"frameId": fid, "navigationId": "nav-9"}),
        ("Page.navigationCommitted", {"frameId": fid, "navigationId": "nav-9", "url": "http://b.test/late"}),
        ("Page.eventFired", {"frameId": fid, "name": "DOMContentLoaded"}),
        ("Page.eventFired", {"frameId": fid, "name": "load"}),
        ("Page.sameDocumentNavigation", {"frameId": fid, "url": "http://b.test/late#x"}),
        ("Page.navigationAborted", {"frameId": fid, "navigationId": "nav-9", "errorText": "aborted"}),
    ]


def _page():
    engine = _Engine()
    life = Lifecycle(engine, S)
    told = []
    life.announce = lambda what, fid, url=None, **detail: told.append((what, fid))
    engine.dispatch_event("Page.frameAttached", {"frameId": MAIN}, S)
    engine.dispatch_event("Page.frameAttached", {"frameId": CHILD, "parentFrameId": MAIN}, S)
    return engine, life, told


def test_the_late_events_of_a_detached_frame_do_not_bring_it_back():
    engine, life, told = _page()
    engine.dispatch_event("Page.frameDetached", {"frameId": CHILD}, S)
    for method, params in _late(CHILD):
        engine.dispatch_event(method, params, S)
    assert CHILD not in life.frames, "a frame that left is back in the page: %r" % sorted(life.frames)
    assert sorted(life.frames) == [MAIN]
    assert [t for t in told if t[1] == CHILD and t[0] != "frameDetached"] == [("frameAttached", CHILD)], told


def test_the_events_of_a_frame_never_announced_make_no_frame():
    engine, life, told = _page()
    for method, params in _late("subframe-77"):
        engine.dispatch_event(method, params, S)
    assert "subframe-77" not in life.frames, sorted(life.frames)
    assert life.main_frame == MAIN
    assert not [t for t in told if t[1] == "subframe-77"], told


def test_a_frame_attached_again_after_it_left_is_a_frame():
    """What the engine announces is a frame, whatever came before: only the late events are dropped."""
    engine, life, told = _page()
    engine.dispatch_event("Page.frameDetached", {"frameId": CHILD}, S)
    engine.dispatch_event("Page.frameAttached", {"frameId": CHILD, "parentFrameId": MAIN}, S)
    engine.dispatch_event("Page.navigationCommitted",
                          {"frameId": CHILD, "navigationId": "nav-3", "url": "http://b.test/again"}, S)
    assert life.frames[CHILD].parent == MAIN
    assert life.frames[CHILD].url == "http://b.test/again"


def test_a_goto_of_a_frame_that_left_says_it_is_detached():
    engine, life, _ = _page()
    engine.dispatch_event("Page.frameDetached", {"frameId": CHILD}, S)
    with pytest.raises(Exception, match="detached"):
        life.goto("http://b.test/x", frame_id=CHILD, timeout=1)
    assert CHILD not in life.frames
