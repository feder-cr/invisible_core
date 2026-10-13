"""networkidle2: `load`, then at most two requests in flight for IDLE_QUIET.

pyppeteer's `networkidle2` is Chrome's `networkAlmostIdle`: no more than two
network connections for 500 ms. Until 43.39.0 the lifecycle had only
networkidle (none in flight), and invisible_puppeteer mapped both of
pyppeteer's names onto it, so a page that keeps two long polls open never
reached `networkidle2` and `goto(..., waitUntil="networkidle2")` timed out.
"""
from __future__ import annotations

import time

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import IDLE_QUIET, Lifecycle

pytestmark = pytest.mark.unit


class FakeConnection(EventListeners):
    def send(self, method, params=None, session=None, timeout=30):
        return None


def _loaded():
    c = FakeConnection()
    v = Lifecycle(c, "S1")
    for method, params in (("Page.frameAttached", {"frameId": "F1"}),
                           ("Page.eventFired", {"frameId": "F1", "name": "load"})):
        c.dispatch_event(method, params, "S1")
    return c, v


def _start(c, *ids):
    for i in ids:
        c.dispatch_event("Network.requestWillBeSent", {"requestId": i}, "S1")


def _finish(c, *ids):
    for i in ids:
        c.dispatch_event("Network.requestFinished", {"requestId": i}, "S1")


def test_two_requests_in_flight_reach_networkidle2_and_not_networkidle():
    c, v = _loaded()
    _start(c, "R1", "R2")
    v.wait_for_state("F1", "networkidle2", timeout=IDLE_QUIET * 4)
    with pytest.raises(TimeoutError):
        v.wait_for_state("F1", "networkidle", timeout=IDLE_QUIET * 2)


def test_three_requests_in_flight_hold_networkidle2_until_one_ends():
    c, v = _loaded()
    _start(c, "R1", "R2", "R3")
    with pytest.raises(TimeoutError):
        v.wait_for_state("F1", "networkidle2", timeout=IDLE_QUIET * 2)
    _finish(c, "R3")
    v.wait_for_state("F1", "networkidle2", timeout=IDLE_QUIET * 4)


def test_the_quiet_is_counted_from_the_end_of_the_crowd():
    c, v = _loaded()
    _start(c, "R1", "R2", "R3")
    time.sleep(IDLE_QUIET * 2)
    _finish(c, "R3")
    with pytest.raises(TimeoutError):
        v.wait_for_state("F1", "networkidle2", timeout=IDLE_QUIET / 3)
    v.wait_for_state("F1", "networkidle2", timeout=IDLE_QUIET * 4)
