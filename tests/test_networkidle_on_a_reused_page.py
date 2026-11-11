"""networkidle on a page that is reused: one request that never ends must not poison the fetches that follow.

A client that fetches many addresses on ONE page (tf-fetch: dozens of `goto(..., "networkidle")` on the same page) met
`networkidle` that never came, from the second address on, on real news sites; a new context per address idled in 4-9 s.
The lifecycle counted the requests in flight with ONE integer for the page (`+1` on `Network.requestWillBeSent`, `-1` on an
end), never reset by a navigation and not keyed by request. A request whose end the page never gets (a beacon cancelled
with the page, a font the route aborted twice, a request closed before the client answered) left the integer above zero
for good, and every later document waited for it.

Playwright keeps a SET of request ids per frame: a new document clears the requests of its frame (the navigation's own
request stays until it ends), a detached frame drops its own, a redirect replaces the request it redirects, and an end event
for an id it does not hold is nothing. Same here.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import IDLE_QUIET, Lifecycle

pytestmark = pytest.mark.unit

S = "S1"


class FakeConnection(EventListeners):
    def send(self, method, params=None, session=None, timeout=30):
        return None


def _page():
    c = FakeConnection()
    v = Lifecycle(c, S)
    c.dispatch_event("Page.frameAttached", {"frameId": "F1"}, S)
    return c, v


def _document(c, nav, url="http://a.test/", request=None, ends=True):
    """The events of a navigation: started, its own request, commit, load."""
    c.dispatch_event("Page.navigationStarted", {"frameId": "F1", "navigationId": nav, "url": url}, S)
    rid = request or "doc-" + nav
    c.dispatch_event("Network.requestWillBeSent", {"requestId": rid, "frameId": "F1", "navigationId": nav, "url": url}, S)
    c.dispatch_event("Page.navigationCommitted", {"frameId": "F1", "navigationId": nav, "url": url, "name": ""}, S)
    if ends:
        c.dispatch_event("Network.requestFinished", {"requestId": rid}, S)
    c.dispatch_event("Page.eventFired", {"frameId": "F1", "name": "load"}, S)


def _sub(c, rid, frame="F1", end=None, **extra):
    c.dispatch_event("Network.requestWillBeSent", dict({"requestId": rid, "frameId": frame, "url": "http://a.test/" + rid}, **extra), S)
    if end:
        c.dispatch_event("Network." + end, {"requestId": rid}, S)


def _idle(v, timeout=IDLE_QUIET * 4):
    v.wait_for_state("F1", "networkidle", timeout=timeout)


def test_a_request_that_never_ends_does_not_poison_the_next_document():
    c, v = _page()
    _document(c, "N1")
    _sub(c, "beacon-1")                       # never gets requestFinished / requestFailed
    with pytest.raises(TimeoutError):
        _idle(v, IDLE_QUIET * 2)              # on this document it IS open: stock waits for it too
    _document(c, "N2", "http://b.test/")      # the same page goes to another address
    _idle(v)                                   # red before: the integer stayed at 1


def test_ten_leaks_in_a_row_do_not_add_up():
    c, v = _page()
    for n in range(10):
        _document(c, "N%d" % n)
        for k in range(5):
            _sub(c, "leak-%d-%d" % (n, k))
    _document(c, "N99", "http://c.test/")
    _idle(v)


def test_the_request_of_the_navigation_itself_is_kept_until_it_ends():
    c, v = _page()
    _document(c, "N1", ends=False)
    with pytest.raises(TimeoutError):
        _idle(v, IDLE_QUIET * 2)              # the document's own request is still open
    c.dispatch_event("Network.requestFinished", {"requestId": "doc-N1"}, S)
    _idle(v)


def test_a_request_of_the_new_document_that_started_before_its_commit_counts():
    """Subresources can start between the navigation's request and its commit event; they belong to the new document."""
    c, v = _page()
    _document(c, "N1")
    c.dispatch_event("Page.navigationStarted", {"frameId": "F1", "navigationId": "N2", "url": "http://b.test/"}, S)
    c.dispatch_event("Network.requestWillBeSent", {"requestId": "doc-N2", "frameId": "F1", "navigationId": "N2"}, S)
    c.dispatch_event("Page.navigationCommitted", {"frameId": "F1", "navigationId": "N2", "url": "http://b.test/", "name": ""}, S)
    _sub(c, "css-1")                           # after the commit: open
    c.dispatch_event("Network.requestFinished", {"requestId": "doc-N2"}, S)
    c.dispatch_event("Page.eventFired", {"frameId": "F1", "name": "load"}, S)
    with pytest.raises(TimeoutError):
        _idle(v, IDLE_QUIET * 2)
    c.dispatch_event("Network.requestFinished", {"requestId": "css-1"}, S)
    _idle(v)


def test_an_end_that_arrives_twice_or_for_an_unknown_request_is_nothing():
    c, v = _page()
    _document(c, "N1")
    _sub(c, "a")
    _sub(c, "b")
    c.dispatch_event("Network.requestFinished", {"requestId": "a"}, S)
    c.dispatch_event("Network.requestFinished", {"requestId": "a"}, S)      # twice: must not close "b"
    c.dispatch_event("Network.requestFailed", {"requestId": "never-seen", "errorCode": "x"}, S)
    with pytest.raises(TimeoutError):
        _idle(v, IDLE_QUIET * 2)
    c.dispatch_event("Network.requestFailed", {"requestId": "b", "errorCode": "x"}, S)
    _idle(v)


def test_a_redirect_replaces_the_request_it_redirects():
    """The hop gets a new id; the one it replaces may never get an end of its own."""
    c, v = _page()
    _document(c, "N1")
    _sub(c, "img-1")
    _sub(c, "img-2", redirectedFrom="img-1")
    c.dispatch_event("Network.requestFinished", {"requestId": "img-2"}, S)
    _idle(v)


def test_a_frame_that_leaves_takes_its_requests_with_it():
    c, v = _page()
    _document(c, "N1")
    c.dispatch_event("Page.frameAttached", {"frameId": "F2", "parentFrameId": "F1"}, S)
    _sub(c, "ad-1", frame="F2")
    _sub(c, "ad-2", frame="F2")
    c.dispatch_event("Page.frameDetached", {"frameId": "F2"}, S)
    _idle(v)


def test_the_parent_committing_clears_the_requests_of_its_children_that_left_with_it():
    c, v = _page()
    _document(c, "N1")
    c.dispatch_event("Page.frameAttached", {"frameId": "F2", "parentFrameId": "F1"}, S)
    _sub(c, "ad-1", frame="F2")
    _document(c, "N2", "http://b.test/")
    _idle(v)


def test_the_requests_of_another_frame_survive_a_commit_of_this_one():
    c, v = _page()
    _document(c, "N1")
    c.dispatch_event("Page.frameAttached", {"frameId": "F2", "parentFrameId": "F1"}, S)
    c.dispatch_event("Page.navigationCommitted", {"frameId": "F2", "navigationId": "M1", "url": "http://x.test/", "name": ""}, S)
    _sub(c, "ad-1", frame="F2")
    c.dispatch_event("Page.navigationCommitted", {"frameId": "F2", "navigationId": "M2", "url": "http://x.test/2", "name": ""}, S)
    # F2's second document cleared F2's own; a request of F1 is not touched by it
    _sub(c, "app-1", frame="F1")
    c.dispatch_event("Page.navigationCommitted", {"frameId": "F2", "navigationId": "M3", "url": "http://x.test/3", "name": ""}, S)
    with pytest.raises(TimeoutError):
        _idle(v, IDLE_QUIET * 2)
    c.dispatch_event("Network.requestFinished", {"requestId": "app-1"}, S)
    _idle(v)


def test_inflight_is_the_number_of_open_requests():
    c, v = _page()
    _document(c, "N1")
    _sub(c, "a")
    _sub(c, "b")
    assert v.inflight == 2
    c.dispatch_event("Network.requestFinished", {"requestId": "a"}, S)
    assert v.inflight == 1
