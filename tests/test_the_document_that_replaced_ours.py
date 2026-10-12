"""The first document committed after the browser took our `goto` decides it.

THE KNOWN-BAD INPUT, recorded on firefox-41 on 2026-10-12 with
`C:/tmp/b230/back_to_back_probe.py`: goto, read, goto again at once, on a page
that calls `location.replace('/landed')` 50 ms after `load`. Two and three rounds
in 80 went like this (round 33, times in seconds):

    0.000 navigationStarted   nav-12884901905   the previous page's redirect
    0.000 SEND Page.navigate
    0.016 REPLY               nav-96            ours
    0.032 navigationStarted   nav-96
    0.032 frameAttached       mainframe-11      re-announced, a new process
    0.032 navigationStarted   nav-12884901905   the redirect, re-announced
    0.063 navigationCommitted nav-12884901905   http://127.0.0.1:53642/landed
    0.063 eventFired          DOMContentLoaded, load

and nothing more about nav-96: no commit, and no abort, which the engine's
`FrameTree.onStateChange` drops for the main frame. The rule then was to
follow a navigation first seen after ours; the redirect had been seen first,
so `goto` waited for a document that never came, 8 s here and 45 s in the MCP
server. Upstream Playwright's `goto` raises "interrupted by another navigation"
on the first other document committed after the command's reply.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from invisible_core.juggler.connection import Connection, EventListeners
from invisible_core.juggler.lifecycle import Lifecycle, NavigationInterrupted

pytestmark = pytest.mark.unit

S = "S1"
F = "mainframe-11"
OURS = "nav-96"
REDIRECT = "nav-12884901905"
LANDED = "http://127.0.0.1:53642/landed"


class _Engine(EventListeners):
    """Answers `Page.navigate` with OURS. `before` is delivered while the
    command is being handled, so ahead of the reply; `after` from another
    thread once the reply is out, the way the browser sends them."""

    def __init__(self, before=(), after=()):
        super().__init__()
        self.before = before
        self.after = after

    def emit(self, events):
        for method, params in events:
            self.dispatch_event(method, params, S)

    def send(self, method, params=None, session=None, timeout=None):
        assert method == "Page.navigate", method
        self.emit(self.before)
        threading.Timer(0.05, self.emit, (self.after,)).start()
        return {"navigationId": OURS}


def _started(nav):
    return ("Page.navigationStarted", {"frameId": F, "navigationId": nav})


def _committed(nav, url):
    return ("Page.navigationCommitted", {"frameId": F, "navigationId": nav,
                                         "url": url})


def _loaded():
    return [("Page.eventFired", {"frameId": F, "name": "DOMContentLoaded"}),
            ("Page.eventFired", {"frameId": F, "name": "load"})]


def _previous_page(engine):
    """The page we leave, loaded, with its own redirect already started."""
    life = Lifecycle(engine, S)
    engine.emit([("Page.frameAttached", {"frameId": F}),
                 _started("nav-94"), _committed("nav-94", "http://127.0.0.1/r50"),
                 *_loaded(), _started(REDIRECT)])
    return life


def test_the_recorded_round_is_interrupted_at_once_and_names_the_page():
    engine = _Engine(after=[_started(OURS), ("Page.frameAttached", {"frameId": F}),
                            _started(REDIRECT), _committed(REDIRECT, LANDED),
                            *_loaded()])
    life = _previous_page(engine)
    began = time.monotonic()
    with pytest.raises(NavigationInterrupted) as e:
        life.goto("http://127.0.0.1/r50?i=33", frame_id=F,
                  until="domcontentloaded", timeout=5)
    assert time.monotonic() - began < 2, "it waited for a document that never came"
    # Upstream Playwright's sentence, which callers match on.
    assert str(e.value) == ('Navigation to "http://127.0.0.1/r50?i=33" is '
                            'interrupted by another navigation to "%s"' % LANDED)


def test_ours_never_reported_started_is_still_decided_by_the_reply():
    """The browser may not even report ours started before the redirect
    commits in its place. The line is the reply, which ours always has: a rule
    anchored on ours' own start would have no line here, and wait for the
    timeout."""
    engine = _Engine(after=[("Page.frameAttached", {"frameId": F}),
                            _started(REDIRECT), _committed(REDIRECT, LANDED),
                            *_loaded()])
    life = _previous_page(engine)
    began = time.monotonic()
    with pytest.raises(NavigationInterrupted):
        life.goto("http://127.0.0.1/r50?i=36", frame_id=F, until="load",
                  timeout=5)
    assert time.monotonic() - began < 2


def test_a_document_announced_again_without_an_id_is_not_a_new_one():
    """A new PageAgent announces the document its frame already holds, with no
    navigation id (its constructor, when the frame has a url). That is not a
    document committed in ours' place."""
    engine = _Engine(after=[_started(OURS), ("Page.frameAttached", {"frameId": F}),
                            ("Page.navigationCommitted",
                             {"frameId": F, "url": "http://127.0.0.1/r50"}),
                            _committed(OURS, "http://127.0.0.1/r50?i=37"),
                            *_loaded()])
    life = _previous_page(engine)
    out = life.goto("http://127.0.0.1/r50?i=37", frame_id=F, until="load",
                    timeout=5)
    assert out["url"] == "http://127.0.0.1/r50?i=37"


def test_a_wait_without_a_reply_is_decided_from_the_first_start_whenever_it_looks():
    """A history wait has no reply to anchor on, so its line is where the
    navigation was FIRST reported started. Moving it to a later re-start would
    make the answer depend on when the waiter happened to look: in between,
    the other document had already decided it."""
    engine = _Engine()
    life = Lifecycle(engine, S)
    engine.emit([("Page.frameAttached", {"frameId": F}), _started("nav-7"),
                 _committed("nav-12884901950", LANDED),
                 ("Page.frameAttached", {"frameId": F}), _started("nav-7"),
                 _committed("nav-7", "http://127.0.0.1/back"), *_loaded()])
    with pytest.raises(NavigationInterrupted) as e:
        life.wait_for_state(F, "load", navigation="nav-7", timeout=2)
    # No address was asked for, so the navigation is named by its id.
    assert str(e.value) == ('Navigation nav-7 is interrupted by another '
                            'navigation to "%s"' % LANDED)


def test_ours_still_wins_when_it_is_the_document_that_commits():
    """The same start, but the browser lets ours through: the redirect being
    known from before must not turn ours into an interruption."""
    engine = _Engine(after=[_started(OURS), ("Page.frameAttached", {"frameId": F}),
                            _started(OURS), _committed(OURS, "http://127.0.0.1/r50?i=34"),
                            *_loaded()])
    life = _previous_page(engine)
    out = life.goto("http://127.0.0.1/r50?i=34", frame_id=F,
                    until="domcontentloaded", timeout=5)
    assert out == {"navigationId": OURS, "url": "http://127.0.0.1/r50?i=34"}


def test_a_document_committed_before_the_reply_is_the_past():
    """The redirect commits while the browser is still handling our command,
    ahead of the reply on the pipe: the page we are leaving moved on before
    ours began, and ours then commits. Deciding on what arrived after the
    command was SENT, rather than after its reply, raises an interruption here
    for a goto that succeeds."""
    # The commit is the LAST event before the reply: the line is strict.
    engine = _Engine(before=[_committed(REDIRECT, LANDED)],
                     after=[_started(OURS), _committed(OURS, "http://127.0.0.1/r50?i=35"),
                            *_loaded()])
    life = _previous_page(engine)
    out = life.goto("http://127.0.0.1/r50?i=35", frame_id=F, until="load",
                    timeout=5)
    assert out["url"] == "http://127.0.0.1/r50?i=35"


def test_a_later_document_after_ours_still_closes_the_wait():
    """[B228] is unchanged: ours commits first, the page replaces itself, and
    the wait ends on the replacement's load."""
    engine = _Engine(after=[_started(OURS), _committed(OURS, "http://127.0.0.1/a"),
                            _started("nav-12884901999"),
                            _committed("nav-12884901999", LANDED), *_loaded()])
    life = _previous_page(engine)
    out = life.goto("http://127.0.0.1/a", frame_id=F, until="load", timeout=5)
    assert out["url"] == LANDED


def test_the_reply_is_placed_by_the_reader_not_by_the_caller(monkeypatch):
    """The place `goto` decides on is the reply's position among the events,
    read on the reader thread. Read by the caller once it wakes, it includes
    whatever the reader delivered meanwhile. Here the caller is made to wake
    only after the reader has gone on into the event that follows the reply,
    so a caller-side read gives 2 where the reply came after 1."""
    import invisible_core.juggler.connection as connection

    to_r, to_w = os.pipe()
    from_r, from_w = os.pipe()
    c = Connection(to_w, from_r)
    went_on, released = threading.Event(), threading.Event()
    seen = []

    def listener(method, params, session):
        seen.append(method)
        if method == "Page.after":
            went_on.set()
            released.wait(5)

    c.add_listener(listener)
    stream = [{"method": "Page.before", "params": {}},
              {"id": 1, "result": {"navigationId": "N"}},
              {"method": "Page.after", "params": {}}]
    writer = threading.Timer(0.1, os.write, (from_w, b"".join(
        json.dumps(m).encode() + b"\x00" for m in stream)))
    writer.start()

    class _LateCaller(threading.Event):
        def wait(self, timeout=None):
            arrived = super().wait(timeout)
            went_on.wait(5)
            return arrived

    # After the reader and the writer exist: only the command's own wait is late.
    monkeypatch.setattr(connection.threading, "Event", _LateCaller)
    try:
        result, place = c.send_placed("Page.navigate", {}, timeout=5)
    finally:
        monkeypatch.undo()
        released.set()
    assert result == {"navigationId": "N"}
    assert place == 1, place
    assert seen == ["Page.before", "Page.after"]
