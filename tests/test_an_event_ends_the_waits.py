"""An event a session was told to end its waits on ends them: the calls still
waiting for their reply, and the lifecycle's waits that began before it.

It is Marionette's rule for dialogs, which invisible_selenium asks for:
`MarionetteCommandsParent.sendQuery` races every content query against
`#deferredDialogOpened`, and the navigation wait ends on `onPromptOpened`.
Measured on retail Firefox 151 with geckodriver 0.37.1 (2026-10-13):
`execute_script("alert('hi'); return 5")` answers None in 0.4 s with the
alert open, and `get()` of a page that alerts while it loads returns in
0.05 s. Until this rule existed the same calls waited out their whole timeout
(the script timeout, 30 s; the page load timeout, 300 s).
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from invisible_core.juggler.connection import (
    Connection, EventListeners, Interrupted, ReplyTimeout)
from invisible_core.juggler.lifecycle import Lifecycle

pytestmark = pytest.mark.unit


def _dialogs(method, params):
    return method == "Page.dialogOpened" and params.get("type") != "beforeunload"


def _pipes():
    to_r, to_w = os.pipe()
    from_r, from_w = os.pipe()
    return Connection(to_w, from_r), to_r, from_w


def _later(fd, delay, *messages):
    data = b"".join(json.dumps(m).encode() + b"\x00" for m in messages)
    threading.Timer(delay, os.write, (fd, data)).start()


def _dialog(session, kind="alert"):
    return {"method": "Page.dialogOpened", "sessionId": session,
            "params": {"dialogId": "D1", "type": kind, "message": "hi"}}


def test_a_dialog_ends_the_call_its_session_is_waiting_on():
    c, _to_r, from_w = _pipes()
    c.end_waits_when("S1", _dialogs)
    _later(from_w, 0.2, _dialog("S1"))
    began = time.monotonic()
    with pytest.raises(Interrupted) as caught:
        c.send("Runtime.callFunction", {}, session="S1", timeout=3)
    assert time.monotonic() - began < 2
    assert caught.value.method == "Page.dialogOpened"
    assert caught.value.params["message"] == "hi"
    # The reply that comes once the dialog is answered is nobody's, and the
    # next command gets its own.
    _later(from_w, 0.1, {"id": 1, "result": {"late": True}},
           {"id": 2, "result": {"mine": True}})
    assert c.send("Runtime.evaluate", {}, session="S1", timeout=3) == {"mine": True}


def test_the_subscribers_have_the_event_before_the_call_wakes():
    c, _to_r, from_w = _pipes()
    c.end_waits_when("S1", _dialogs)
    seen = []
    c.add_listener(lambda method, params, session: seen.append(method))
    _later(from_w, 0.2, _dialog("S1"))
    with pytest.raises(Interrupted):
        c.send("Runtime.callFunction", {}, session="S1", timeout=3)
    assert seen == ["Page.dialogOpened"]


@pytest.mark.parametrize("event", [_dialog("S2"), _dialog("S1", "beforeunload")],
                         ids=["another-session", "not-in-the-rule"])
def test_an_event_outside_the_rule_ends_nothing(event):
    c, _to_r, from_w = _pipes()
    c.end_waits_when("S1", _dialogs)
    _later(from_w, 0.1, event)
    _later(from_w, 0.4, {"id": 1, "result": {"answered": True}})
    assert c.send("Runtime.callFunction", {}, session="S1",
                  timeout=3) == {"answered": True}


def test_without_a_rule_a_dialog_ends_nothing_and_the_rule_comes_off():
    c, _to_r, from_w = _pipes()
    _later(from_w, 0.1, _dialog("S1"))
    _later(from_w, 0.3, {"id": 1, "result": {"answered": True}})
    assert c.send("Runtime.callFunction", {}, session="S1",
                  timeout=3) == {"answered": True}
    c.end_waits_when("S1", _dialogs)
    c.end_waits_when("S1", None)
    _later(from_w, 0.1, _dialog("S1"))
    with pytest.raises(ReplyTimeout):
        c.send("Runtime.callFunction", {}, session="S1", timeout=0.5)


class FakeConnection(EventListeners):
    def send(self, method, params=None, session=None, timeout=30):
        return None


def _page():
    c = FakeConnection()
    v = Lifecycle(c, "S1")
    c.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")
    c.dispatch_event("Page.navigationStarted",
                     {"frameId": "F1", "navigationId": "N1"}, "S1")
    c.end_waits_when("S1", _dialogs)
    return c, v


def test_a_dialog_ends_a_lifecycle_wait_that_began_before_it():
    c, v = _page()
    threading.Timer(0.2, c.dispatch_event,
                    ("Page.dialogOpened", _dialog("S1")["params"], "S1")).start()
    began = time.monotonic()
    with pytest.raises(Interrupted):
        v.wait_for_state("F1", "load", timeout=3)
    assert time.monotonic() - began < 2


def test_a_dialog_from_before_the_wait_does_not_end_it():
    c, v = _page()
    c.dispatch_event("Page.dialogOpened", _dialog("S1")["params"], "S1")
    with pytest.raises(TimeoutError) as caught:
        v.wait_for_state("F1", "load", timeout=0.3)
    assert not isinstance(caught.value, Interrupted)


def test_a_dialog_from_before_the_mark_does_not_end_the_wait():
    c, v = _page()
    c.dispatch_event("Page.dialogOpened", _dialog("S1")["params"], "S1")
    since = v.mark("F1")
    with pytest.raises(TimeoutError) as caught:
        v.wait_for_new_navigation("F1", since, timeout=0.3)
    assert not isinstance(caught.value, Interrupted)


def test_a_dialog_after_the_mark_ends_the_wait_for_the_new_document_too():
    # The navigation has started by the time the wait looks: the wait for its
    # document measures from the mark, not from when it began.
    c, v = _page()
    since = v.mark("F1")
    c.dispatch_event("Page.dialogOpened", _dialog("S1")["params"], "S1")
    c.dispatch_event("Page.navigationStarted",
                     {"frameId": "F1", "navigationId": "N2"}, "S1")
    with pytest.raises(Interrupted):
        v.wait_for_new_navigation("F1", since, timeout=0.5)


def test_a_dialog_after_the_mark_ends_the_wait_for_the_next_navigation():
    c, v = _page()
    since = v.mark("F1")
    c.dispatch_event("Page.dialogOpened", _dialog("S1")["params"], "S1")
    began = time.monotonic()
    with pytest.raises(Interrupted):
        v.wait_for_new_navigation("F1", since, timeout=3)
    assert time.monotonic() - began < 1
