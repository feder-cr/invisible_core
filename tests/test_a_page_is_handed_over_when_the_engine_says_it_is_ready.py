"""A page is handed to the caller only when the engine has said it is ready (`Page.ready`), so the first `goto` cannot meet the page's
own initial `about:blank`.

⛔ THE KNOWN-BAD INPUT. The engine announces a new page's main frame, replies to `Browser.newPage`, and only afterwards commits the
page's initial `about:blank` (the "transient navigations" that `Page.ready` waits for, PageHandler.js). A caller that gets the page at the
frame announcement and calls `goto` at once sends `Page.navigate` BEFORE that commit arrives. `Frame.outcome` takes the first document
committed after the reply of the command: it is the initial about:blank, not ours, and the `goto` raises "interrupted by another navigation
to about:blank". On an idle machine the initial document is long committed by then; on a loaded one it is not (three different e2e
tests in two CI runs, and `new_context().new_page()` + `goto` in a loop on main). The state machine is tested by feeding it events, in the
order the loaded machine produces.
"""
from __future__ import annotations

import threading
import time

import pytest

from invisible_core.juggler.connection import EventListeners, TargetClosedError
from invisible_core.juggler.lifecycle import Lifecycle, NavigationInterrupted


class FakeConnection(EventListeners):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.on_navigate = None

    def send(self, method, params=None, session=None, timeout=30):
        self.sent.append((method, params, session))
        if method == "Page.navigate":
            if self.on_navigate:
                self.on_navigate()
            return {"navigationId": "N1"}
        return None


def feed(c, *pairs):
    for method, params in pairs:
        c.dispatch_event(method, params, "S1")


INITIAL = (("Page.navigationStarted", {"frameId": "F1", "navigationId": "N0", "url": "about:blank"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "N0", "url": "about:blank"}))
OURS = (("Page.navigationStarted", {"frameId": "F1", "navigationId": "N1", "url": "http://a/"}),
        ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "N1", "url": "http://a/"}),
        ("Page.eventFired", {"frameId": "F1", "name": "load"}))


def test_the_page_is_not_ready_until_the_engine_says_so():
    c = FakeConnection()
    v = Lifecycle(c, "S1")
    feed(c, ("Page.frameAttached", {"frameId": "F1"}))
    with pytest.raises(TimeoutError) as e:
        v.wait_for_page_ready(timeout=0.2)
    assert "not ready" in str(e.value)


def test_the_wait_ends_when_page_ready_arrives_from_another_thread():
    c = FakeConnection()
    v = Lifecycle(c, "S1")
    feed(c, ("Page.frameAttached", {"frameId": "F1"}))
    done = []
    t = threading.Thread(target=lambda: (v.wait_for_page_ready(timeout=5), done.append(time.monotonic())))
    t.start()
    time.sleep(0.2)
    assert not done, "the wait ended before Page.ready"
    sent = time.monotonic()
    feed(c, *INITIAL, ("Page.ready", {}))
    t.join(5)
    assert done and done[0] >= sent


def test_a_page_that_was_ready_before_the_lifecycle_existed_returns_at_once():
    """`Page.ready` is replayed to a lifecycle built after it was sent (the events a page missed before its `Browser.newPage` reply)."""
    c = FakeConnection()
    v = Lifecycle(c, "S1")
    feed(c, ("Page.frameAttached", {"frameId": "F1"}), *INITIAL, ("Page.ready", {}))
    t0 = time.monotonic()
    v.wait_for_page_ready(timeout=5)
    assert time.monotonic() - t0 < 0.5


def test_the_browser_closing_ends_the_wait():
    c = FakeConnection()
    v = Lifecycle(c, "S1")
    feed(c, ("Page.frameAttached", {"frameId": "F1"}))
    threading.Timer(0.2, lambda: [fn() for fn in list(c._close_listeners)] if hasattr(c, "_close_listeners") else None).start()
    with pytest.raises((TargetClosedError, TimeoutError)):
        v.wait_for_page_ready(timeout=2)


def test_a_goto_right_after_a_ready_page_is_never_interrupted_by_the_initial_document():
    """The loaded machine's order: frame announced, `Browser.newPage` answered, THEN the initial about:blank commits, then Page.ready.
    The caller waits for the page to be ready, so its goto starts after the initial commit and is the first document committed after the
    reply of its command."""
    c = FakeConnection()
    v = Lifecycle(c, "S1")
    feed(c, ("Page.frameAttached", {"frameId": "F1"}))

    def engine():
        time.sleep(0.15)          # the initial document is slow on a loaded machine
        feed(c, *INITIAL, ("Page.ready", {}))
    threading.Thread(target=engine).start()

    v.wait_for_page_ready(timeout=5)       # what the page hand-over does
    c.on_navigate = lambda: feed(c, *OURS)  # the engine's events for OUR navigation follow the command
    result = v.goto("http://a/", timeout=5)
    assert result["landed"] == "N1" and result["url"] == "http://a/"


def test_without_the_wait_the_late_initial_document_interrupts_the_goto():
    """The failure itself, pinned: the same events with a caller that did not wait for the page (the old hand-over). The initial about:blank
    commits after the reply of `Page.navigate`: `Frame.outcome` answers with it, and the goto is 'interrupted by another navigation'."""
    c = FakeConnection()
    v = Lifecycle(c, "S1")
    feed(c, ("Page.frameAttached", {"frameId": "F1"}))
    # the command's reply, then the initial document commits, then ours
    c.on_navigate = lambda: None

    real_send = c.send

    def send(method, params=None, session=None, timeout=30):
        out = real_send(method, params, session, timeout)
        if method == "Page.navigate":
            # after the reply: the loaded engine commits the initial document late, ours behind it
            threading.Timer(0.05, lambda: feed(c, *INITIAL, *OURS)).start()
        return out
    c.send = send
    with pytest.raises(NavigationInterrupted) as e:
        v.goto("http://a/", timeout=3)
    assert "about:blank" in str(e.value)
