"""A frame the engine re-announces is the same frame, and keeps what it knows.

⛔ THE KNOWN-BAD INPUT, recorded on firefox-41 on 2026-10-12 with
`C:/tmp/b230/reattach_events.py` (the Page events of one `goto` on a page that
calls `location.replace` 50 ms after `load`, to another site):

    navigationStarted   nav-25            (ours)
    frameAttached       mainframe-11      <- the same id again
    navigationStarted   nav-25
    navigationCommitted nav-25
    eventFired          DOMContentLoaded, load
    navigationStarted   nav-12884901889   (the page's own)
    frameAttached       mainframe-11      <- again
    navigationStarted   nav-12884901889
    navigationCommitted nav-12884901889
    eventFired          DOMContentLoaded, load

A navigation that moves the frame to another content process brings up a new
PageAgent there, and it announces the frames it holds and re-sends the
navigation in flight. `Lifecycle` built a NEW Frame on every `frameAttached`, so
after the page's own navigation the frame had never heard of ours, `follows`
([B228]) answered no for ever, and the `goto` waited for the timeout:
`networkidle` 12 in 12 on a local page, and `domcontentloaded` in the
invisible_dots VMs, where the events above arrive together (45 s).

The [B228] unit test missed it because its event order was written from the
protocol's description rather than recorded from a browser: it had no second
`frameAttached`. These tests deliver the recorded order, all at once, the way a
loaded host does.
"""
from __future__ import annotations

import threading
import time

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import Lifecycle, NavigationInterrupted

pytestmark = pytest.mark.unit

S = "S1"
F = "mainframe-11"
OURS = "nav-25"
THEIRS = "nav-12884901889"


class _Engine(EventListeners):
    """Answers `Page.navigate` the way Juggler does, after delivering the
    recorded events; `later` are delivered from another thread afterwards."""

    def __init__(self, now, later=(), later_after=0.3):
        super().__init__()
        self.now = now
        self.later = later
        self.later_after = later_after

    def emit(self, events):
        for method, params in events:
            self.dispatch_event(method, params, S)

    def send(self, method, params=None, session=None, timeout=None):
        assert method == "Page.navigate", method
        self.emit(self.now)
        if self.later:
            threading.Timer(self.later_after, self.emit, (self.later,)).start()
        return {"navigationId": OURS}


def _ours_with_its_reattach():
    return [
        ("Page.navigationStarted", {"frameId": F, "navigationId": OURS}),
        ("Page.frameAttached", {"frameId": F}),
        ("Page.navigationStarted", {"frameId": F, "navigationId": OURS}),
        ("Page.navigationCommitted", {"frameId": F, "navigationId": OURS,
                                      "url": "http://127.0.0.1/x50"}),
        ("Page.eventFired", {"frameId": F, "name": "DOMContentLoaded"}),
        ("Page.eventFired", {"frameId": F, "name": "load"}),
    ]


def _theirs_starts():
    return [
        ("Page.navigationStarted", {"frameId": F, "navigationId": THEIRS}),
        ("Page.frameAttached", {"frameId": F}),
        ("Page.navigationStarted", {"frameId": F, "navigationId": THEIRS}),
    ]


def _theirs_loads():
    return [
        ("Page.navigationCommitted", {"frameId": F, "navigationId": THEIRS,
                                      "url": "http://localhost/static"}),
        ("Page.eventFired", {"frameId": F, "name": "DOMContentLoaded"}),
        ("Page.eventFired", {"frameId": F, "name": "load"}),
    ]


def _lifecycle(engine):
    life = Lifecycle(engine, S)
    engine.dispatch_event("Page.frameAttached", {"frameId": F}, S)
    return life


def test_goto_answers_when_the_page_moves_on_before_the_wait_looks():
    """The invisible_dots order: ours commits and loads, and the page's own
    navigation starts and re-attaches the frame before `goto` looks; its
    document arrives later."""
    engine = _Engine(_ours_with_its_reattach() + _theirs_starts(),
                     later=_theirs_loads())
    life = _lifecycle(engine)
    started = time.monotonic()
    life.goto("http://127.0.0.1/x50", frame_id=F, until="domcontentloaded",
              timeout=3)
    assert time.monotonic() - started < 2
    assert life.frames[F].url == "http://localhost/static"


def test_networkidle_is_reached_on_the_document_that_replaced_ours():
    """The local reproduction on firefox-41: `networkidle` comes 500 ms after
    `load`, so the page's own navigation always starts first."""
    engine = _Engine(_ours_with_its_reattach() + _theirs_starts() + _theirs_loads())
    life = _lifecycle(engine)
    started = time.monotonic()
    life.goto("http://127.0.0.1/x50", frame_id=F, until="networkidle", timeout=3)
    assert time.monotonic() - started < 2


def test_a_reattach_keeps_the_frames_history():
    engine = _Engine([])
    life = _lifecycle(engine)
    frame = life.frames[F]
    engine.emit([
        ("Page.navigationStarted", {"frameId": F, "navigationId": "nav-1"}),
        ("Page.navigationAborted", {"frameId": F, "navigationId": "nav-1",
                                    "errorText": "NS_BINDING_ABORTED"}),
        ("Page.navigationStarted", {"frameId": F, "navigationId": "nav-2"}),
        ("Page.sameDocumentNavigation", {"frameId": F, "url": "http://a/#x"}),
        ("Page.frameAttached", {"frameId": F}),
    ])
    assert life.frames[F] is frame, "the re-announced frame is a new object"
    assert frame.aborted == {"nav-1": "NS_BINDING_ABORTED"}
    assert frame.same_document == 1
    # It still knows when nav-2 started, so a document committed after that is
    # the one that replaced it; a frame born again would wait for the timeout.
    engine.emit([("Page.navigationCommitted", {"frameId": F, "navigationId": "nav-3",
                                               "url": "http://a/other"})])
    with pytest.raises(NavigationInterrupted) as e:
        life.wait_for_state(F, "commit", navigation="nav-2", timeout=2)
    assert "interrupted" in str(e.value)


def test_a_child_reattached_under_a_new_parent_says_so():
    engine = _Engine([])
    life = _lifecycle(engine)
    engine.emit([("Page.frameAttached", {"frameId": "child", "parentFrameId": F}),
                 ("Page.frameAttached", {"frameId": "other"}),
                 ("Page.frameAttached", {"frameId": "child",
                                         "parentFrameId": "other"})])
    assert life.frames["child"].parent == "other"
    assert life.main_frame == "other"


def test_a_new_document_retires_the_old_ones_frames_and_says_so():
    """Recorded the same day: after the parent navigated to another process,
    no `frameDetached` came for its iframe, and the client's `page.frames`
    grew by a stale child and a repeated main frame at every navigation."""
    engine = _Engine([])
    heard = []
    life = Lifecycle(engine, S)
    life.announce = lambda what, fid, url=None: heard.append((what, fid))
    engine.emit([
        ("Page.frameAttached", {"frameId": F}),
        ("Page.navigationStarted", {"frameId": F, "navigationId": "nav-1"}),
        ("Page.navigationCommitted", {"frameId": F, "navigationId": "nav-1",
                                      "url": "http://a/withframe"}),
        ("Page.frameAttached", {"frameId": "child", "parentFrameId": F}),
        ("Page.frameAttached", {"frameId": "grandchild", "parentFrameId": "child"}),
        # the parent moves to another process: re-announced, new document
        ("Page.navigationStarted", {"frameId": F, "navigationId": "nav-2"}),
        ("Page.frameAttached", {"frameId": F}),
        ("Page.navigationStarted", {"frameId": F, "navigationId": "nav-2"}),
        ("Page.navigationCommitted", {"frameId": F, "navigationId": "nav-2",
                                      "url": "http://b/withframe"}),
        # the new document's own iframe attaches after its commit
        ("Page.frameAttached", {"frameId": "new-child", "parentFrameId": F}),
    ])
    assert sorted(life.frames) == [F, "new-child"]
    assert heard == [("frameAttached", F), ("frameAttached", "child"),
                     ("frameAttached", "grandchild"),
                     ("frameDetached", "grandchild"), ("frameDetached", "child"),
                     ("frameAttached", "new-child")], heard


def test_a_detach_of_a_frame_already_retired_is_not_told_twice():
    engine = _Engine([])
    heard = []
    life = Lifecycle(engine, S)
    life.announce = lambda what, fid, url=None: heard.append((what, fid))
    engine.emit([
        ("Page.frameAttached", {"frameId": F}),
        ("Page.frameAttached", {"frameId": "child", "parentFrameId": F}),
        ("Page.navigationCommitted", {"frameId": F, "navigationId": "nav-2",
                                      "url": "http://b/"}),
        ("Page.frameDetached", {"frameId": "child"}),
    ])
    assert heard.count(("frameDetached", "child")) == 1, heard
