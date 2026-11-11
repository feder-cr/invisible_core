"""networkidle does not count a CORS preflight, as Playwright's own server does not.

Playwright drops an OPTIONS request that no route holds before its frame manager knows it (ffNetworkManager
`_onRequestWillBeSent`): a preflight, and any explicit OPTIONS that is not intercepted, is not a request of the page and the
page is not "busy" while it is in flight. The lifecycle here counted every request. An intercepted OPTIONS is held by a route and
stays counted, like any request.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import Lifecycle

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


def _request(c, rid, method="GET", **extra):
    c.dispatch_event("Network.requestWillBeSent",
                     dict({"requestId": rid, "frameId": "F1", "url": "http://a.test/" + rid, "method": method}, **extra), S)


def test_an_unintercepted_options_request_is_not_in_flight():
    c, v = _page()
    _request(c, "preflight", "OPTIONS")
    assert v.inflight == 0


def test_an_intercepted_options_request_is_in_flight_like_any_other():
    c, v = _page()
    _request(c, "held", "OPTIONS", isIntercepted=True)
    assert v.inflight == 1


def test_a_get_is_in_flight_and_its_end_closes_it():
    c, v = _page()
    _request(c, "one")
    _request(c, "pre", "OPTIONS")
    assert v.inflight == 1
    c.dispatch_event("Network.requestFinished", {"requestId": "pre"}, S)     # the end of a request the page never knew
    assert v.inflight == 1
    c.dispatch_event("Network.requestFinished", {"requestId": "one"}, S)
    assert v.inflight == 0
