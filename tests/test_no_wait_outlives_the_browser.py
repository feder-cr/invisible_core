"""No wait outlives the browser, and the browser module sets no limit of its own.

Since 41.37.0 the shared browser module (`invisible_core.juggler.instance`) puts
no time limit on a command or an event of its own: Selenium's driver
connection has none and pyppeteer's `Connection.send` none either, and the
clients standing in for them wait as long. Until then it had 30 s to make a
context, 20 s for a page's session and main frame and 10 s to close.

Without a limit, a wait on an EVENT needs another way to end: a reply is
answered by the closed pipe, an event is not. The connection now tells its
close listeners, and every such wait raises TargetClosedError at once.
Known-bad for each test: drop the close listener, or put a fixed limit back.
"""
from __future__ import annotations

import os
import threading

import pytest

from invisible_core.juggler.instance import Browser, Page
from invisible_core.juggler.connection import (
    Connection, EventListeners, TargetClosedError)
from invisible_core.juggler.lifecycle import Lifecycle

pytestmark = pytest.mark.unit


class _Conn(EventListeners):
    """Records every command with the timeout it was sent with."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def startup_send(self, method, params=None):
        return self.send(method, params, timeout=None)

    def send(self, method, params=None, session=None, timeout="unset"):
        self.sent.append((method, timeout))
        if method == "Browser.createBrowserContext":
            return {"browserContextId": "CTX"}
        return {}


def _closes_after(conn, seconds=0.2):
    threading.Timer(seconds, conn.announce_closed).start()


def _raised_within(fn, seconds=5.0):
    """What `fn()` raised, run on a thread that must end within `seconds`:
    a wait with no limit that the close does not wake would otherwise hang
    the suite instead of failing it."""
    out = {}

    def run():
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 - handed to the test
            out["raised"] = e
        else:
            out["raised"] = None
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), "the wait outlived the browser by %.0f s" % seconds
    return out["raised"]


def test_waiting_for_a_main_frame_ends_when_the_browser_closes():
    conn = _Conn()
    life = Lifecycle(conn, "S")
    _closes_after(conn)
    raised = _raised_within(lambda: life.wait_for_main_frame(timeout=None))
    assert isinstance(raised, TargetClosedError) and "main frame" in str(raised)


def test_waiting_for_a_load_state_with_no_limit_ends_when_the_browser_closes():
    conn = _Conn()
    life = Lifecycle(conn, "S")
    conn.dispatch_event("Page.frameAttached", {"frameId": "F"}, "S")
    _closes_after(conn)
    raised = _raised_within(lambda: life.wait_for_state("F", "load", timeout=None))
    assert isinstance(raised, TargetClosedError) and "load" in str(raised)


def test_a_state_already_reached_answers_even_after_the_close():
    """The close is news for a wait that is still waiting, not a reason to
    fail one whose answer is already there."""
    conn = _Conn()
    life = Lifecycle(conn, "S")
    conn.dispatch_event("Page.frameAttached", {"frameId": "F"}, "S")
    conn.dispatch_event("Page.eventFired", {"frameId": "F", "name": "load"}, "S")
    conn.announce_closed()
    assert _raised_within(lambda: life.wait_for_state("F", "load", timeout=None)) is None


def test_waiting_for_a_session_ends_when_the_browser_closes():
    conn = _Conn()
    browser = Browser(conn, "151.0")
    _closes_after(conn)
    raised = _raised_within(lambda: browser.session_for("T1"))
    assert isinstance(raised, TargetClosedError) and "T1" in str(raised)


def test_a_listener_registered_after_the_close_is_told_at_once():
    conn = _Conn()
    conn.announce_closed()
    told = []
    conn.add_close_listener(lambda: told.append(1))
    assert told == [1]
    life = Lifecycle(conn, "S")
    raised = _raised_within(lambda: life.wait_for_main_frame(timeout=None))
    assert isinstance(raised, TargetClosedError)


def test_the_browser_module_sends_with_no_limit_of_its_own():
    conn = _Conn()
    browser = Browser(conn, "151.0")
    context = browser.new_context({"locale": "de-DE"})
    context.send("Browser.newPage", {})
    page = Page.__new__(Page)
    page.context, page.session, page._listeners, page._detached = context, "S", [], True
    page.send("Page.navigate", {})
    page.close()
    context.close()
    limits = {m: t for m, t in conn.sent if m != "Browser.enable"}
    assert set(limits) >= {"Browser.createBrowserContext", "Browser.setLocaleOverride",
                           "Browser.newPage", "Page.navigate", "Page.close",
                           "Browser.removeBrowserContext"}, limits
    assert all(t is None for t in limits.values()), limits


def test_a_real_connection_tells_its_close_listeners_when_the_pipe_ends():
    to_r, to_w = os.pipe()
    from_r, from_w = os.pipe()
    c = Connection(to_w, from_r)
    told = threading.Event()
    c.add_close_listener(told.set)
    os.close(from_w)  # the browser's end goes away
    assert told.wait(5), "the close listener was never called"
    assert c.gone
