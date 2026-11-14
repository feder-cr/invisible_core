"""No thread is started on the connection's reader thread.

`Thread.start` does not return until the new thread is running. On the reader that wait is the reader's own delay: measured on Windows with
four browsers at once, it was 75% of the reader's lag, and it made `networkidle` miss 7 times in 400; with the start no longer waiting, 0 in 400.
The lifecycle used to start a `threading.Timer` from the reader, in `_settle`, for the silence that ends `networkidle`. Now ONE thread per
connection serves the deadlines of every page (`DeadlineClock`), made before the reader exists.

NO BROWSER HERE: a real `Connection` over two OS pipes, the way the engine talks, and a `Lifecycle` on it. `Thread.start` is replaced by a
recorder that notes the thread it was called from; the events are written into the pipe so that they travel the reader's own path
(`_read_loop` -> `_deliver` -> `dispatch_event` -> the listeners).

Known-bad input: `_settle` starting a `threading.Timer` -> one start from the reader at `Page.eventFired(load)`.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from invisible_core.juggler.connection import Connection, EventListeners
from invisible_core.juggler.lifecycle import IDLE_QUIET, Lifecycle

pytestmark = pytest.mark.unit

S = "S1"


class _Engine:
    """The browser's end of a real `Connection`: what it writes comes out of the reader."""

    def __init__(self):
        to_us_r, self._to_us_w = os.pipe()
        from_us_r, from_us_w = os.pipe()
        self._from_us_r = from_us_r
        self.connection = Connection(from_us_w, to_us_r)

    def send(self, method, params):
        os.write(self._to_us_w, json.dumps({"method": method, "params": params, "sessionId": S}).encode() + b"\0")

    def close(self):
        # Every end released before the test returns, the reader's included: a read end closed later by a reader still running
        # would free a descriptor number under the next test of this process (test_a_connection_releases_its_pipe_once reuses
        # the numbers it just freed, and failed in the full suite when this closed with timeout=0 and left the reader behind).
        try:
            os.close(self._to_us_w)          # the engine's end: the reader sees the end of its pipe and releases its read end
        except OSError:
            pass
        self.connection.close(timeout=5)
        self.connection._reader.join(5)
        assert not self.connection._reader.is_alive(), "the reader did not end"
        try:
            os.close(self._from_us_r)
        except OSError:
            pass


def _wait(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.002)
    return False


@pytest.fixture
def starts(monkeypatch):
    """Every `Thread.start` from now on, as (the name of the thread that called it, the name of the thread started)."""
    calls = []
    original = threading.Thread.start

    def recording(self):
        calls.append((threading.current_thread().name, self.name))
        return original(self)
    monkeypatch.setattr(threading.Thread, "start", recording)
    return calls


def test_a_load_on_the_reader_starts_no_thread_and_networkidle_still_comes(starts):
    engine = _Engine()
    try:
        conn = engine.connection
        reader = conn._reader.name
        life = Lifecycle(conn, S)
        del starts[:]       # whatever building the page started, it was not the reader
        engine.send("Page.frameAttached", {"frameId": "F1"})
        engine.send("Page.eventFired", {"frameId": "F1", "name": "load"})
        assert _wait(lambda: "load" in life.frames.get("F1", type("", (), {"states": ()})).states)
        began = time.monotonic()
        life.wait_for_state("F1", "networkidle", timeout=IDLE_QUIET * 6)
        # the same quiet as before: not earlier than IDLE_QUIET after the last event
        assert time.monotonic() - began > IDLE_QUIET * 0.5
        assert [c for c in starts if c[0] == reader] == [], "the reader started %s" % [c for c in starts if c[0] == reader]
        assert conn.handler_errors == []
    finally:
        engine.close()


def test_a_busy_page_is_settled_by_the_same_thread_for_every_page():
    """Two pages of one connection, one thread of deadlines: networkidle comes for each, and no thread is made per page."""
    engine = _Engine()
    try:
        conn = engine.connection
        one, two = Lifecycle(conn, S), Lifecycle(conn, "S2")
        assert one._clock is two._clock is conn.deadlines()
        for session in (S, "S2"):
            os.write(engine._to_us_w, json.dumps({"method": "Page.frameAttached", "params": {"frameId": "F-" + session},
                                                  "sessionId": session}).encode() + b"\0")
            os.write(engine._to_us_w, json.dumps({"method": "Page.eventFired", "params": {"frameId": "F-" + session, "name": "load"},
                                                  "sessionId": session}).encode() + b"\0")
        one.wait_for_state("F-" + S, "networkidle", timeout=IDLE_QUIET * 6)
        two.wait_for_state("F-S2", "networkidle", timeout=IDLE_QUIET * 6)
    finally:
        engine.close()


def test_a_detached_page_has_no_check_left_and_the_clock_ends_with_the_connection():
    engine = _Engine()
    conn = engine.connection
    life = Lifecycle(conn, S)
    engine.send("Page.frameAttached", {"frameId": "F1"})
    engine.send("Page.eventFired", {"frameId": "F1", "name": "load"})
    assert _wait(lambda: life._settle_pending)
    life.detach()
    assert not life._settle_pending
    assert life not in conn.deadlines()._due
    thread = conn.deadlines()._thread
    conn.announce_closed()
    thread.join(5)
    assert not thread.is_alive(), "the thread of deadlines outlived its connection"
    engine.close()


def test_two_pages_built_at_once_share_one_clock_thread(starts):
    """Two pages born together on two threads (the client's `new_page` and an adoption): ONE `juggler-deadlines` thread is started
    for the connection, made under its lock."""
    engine = _Engine()
    try:
        go = threading.Barrier(2)
        made = []

        def build(session):
            go.wait()
            made.append(Lifecycle(engine.connection, session))
        threads = [threading.Thread(target=build, args=(s,)) for s in ("S1", "S2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        assert len(made) == 2
        assert made[0]._clock is made[1]._clock
        assert [c for c in starts if c[1] == "juggler-deadlines"] != [] and             len([c for c in starts if c[1] == "juggler-deadlines"]) == 1, starts
    finally:
        engine.close()


def test_a_page_built_on_the_reader_works_and_no_thread_is_started_there(starts):
    """The pattern the wrapper's tests use on purpose: a listener on `Browser.attachedToTarget`, ON the reader, builds the page's
    `Lifecycle` so that its listeners exist before the session's next event is delivered. It must work (the page is registered, its
    networkidle silence is scheduled and comes) and start no thread on the reader.

    Known-bad input: a clock made lazily by the first `Lifecycle` (and refused on the reader) -> the page is never registered and
    `handler_errors` holds the refusal (the failure of invisible_playwright's release 0.52 e2e, `KeyError: <targetId>`)."""
    engine = _Engine()
    try:
        conn = engine.connection
        reader = conn._reader.name
        sessions = {}

        def on_event(method, params, session):
            if method == "Browser.attachedToTarget":
                sessions[params["targetInfo"]["targetId"]] = Lifecycle(conn, params["sessionId"])
        conn.add_listener(on_event)
        os.write(engine._to_us_w, json.dumps({"method": "Browser.attachedToTarget", "sessionId": None,
                                              "params": {"sessionId": "S7", "targetInfo": {"targetId": "T7", "type": "page"}}}).encode() + b"\0")
        assert _wait(lambda: "T7" in sessions or conn.handler_errors), "the event never reached the listener"
        assert conn.handler_errors == [], conn.handler_errors
        life = sessions["T7"]
        # the page works: the events of its session, delivered right after, reach the lifecycle and networkidle comes
        os.write(engine._to_us_w, json.dumps({"method": "Page.frameAttached", "params": {"frameId": "F7"}, "sessionId": "S7"}).encode() + b"\0")
        os.write(engine._to_us_w, json.dumps({"method": "Page.eventFired", "params": {"frameId": "F7", "name": "load"},
                                              "sessionId": "S7"}).encode() + b"\0")
        assert _wait(lambda: life._settle_pending), "the silence was not scheduled on the clock"
        life.wait_for_state("F7", "networkidle", timeout=IDLE_QUIET * 6)
        assert [c for c in starts if c[0] == reader] == [], starts
        assert conn.handler_errors == []
    finally:
        engine.close()


def test_the_clock_exists_before_the_reader_delivers_its_first_message(starts):
    """The clock is made and started by `Connection.__init__` before the reader thread: the first listener call already finds it, and
    the one thread start of the clock was not made from the reader."""
    to_us_r, to_us_w = os.pipe()
    from_us_r, from_us_w = os.pipe()
    seen = []
    conn = None
    try:
        conn = Connection(from_us_w, to_us_r)
        conn.add_listener(lambda method, params, session: seen.append(conn._deadlines))
        os.write(to_us_w, json.dumps({"method": "Page.frameAttached", "params": {"frameId": "F"}, "sessionId": S}).encode() + b"\0")
        assert _wait(lambda: seen), "the event never reached the listener"
        assert seen[0] is not None and seen[0]._thread.is_alive()
        assert seen[0] is conn.deadlines()
        started = [c for c in starts if c[1] == "juggler-deadlines"]
        assert len(started) == 1 and started[0][0] != conn._reader.name, starts
    finally:
        os.close(to_us_w)
        if conn is not None:
            conn.close(timeout=5)
            conn._reader.join(5)
        for fd in (from_us_r,):
            try:
                os.close(fd)
            except OSError:
                pass


def test_close_leaves_no_deadline_thread_alive_and_a_stand_in_makes_none(starts):
    """`close` stops and joins the connection's clock; a stand-in connection (no reader, no page) starts no thread at all."""
    engine = _Engine()
    conn = engine.connection
    clock = conn.deadlines()
    assert clock._thread.is_alive()
    conn.close(timeout=5)
    assert not clock._thread.is_alive(), "close returned with the thread of deadlines still alive"
    engine.close()
    assert [t for t in threading.enumerate() if t.name == "juggler-deadlines" and t is clock._thread] == []
    del starts[:]
    EventListeners()
    assert starts == [], "a connection without a reader started a thread"
