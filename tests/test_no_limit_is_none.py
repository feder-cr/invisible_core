"""`timeout=None` is no limit, in every wait, through one `Deadline` (B278).

⛔ THE KNOWN-BAD INPUT: Playwright's `timeout=0` means no limit, and every wait
here turned its timeout into `time.monotonic() + timeout`, which cannot say
that, so the wrapper sent 30 s instead (`or 30000`). A wait that is told None
must outlast any finite bound: the clock below jumps 100 s on every reading,
so a wait with any number would have expired after a few turns.
"""
from __future__ import annotations

import itertools

import pytest

from invisible_core.juggler import PageActs, deadline as deadline_module
from invisible_core.juggler.actions import Actions, ElementNotActionable
from invisible_core.juggler.deadline import Deadline


class _Clock:
    """`time.monotonic` that jumps 100 s on every reading."""

    def __init__(self):
        self._ticks = itertools.count(0, 100.0)

    def monotonic(self):
        return next(self._ticks)


@pytest.fixture
def jumping_clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(deadline_module.time, "monotonic", clock.monotonic)
    return clock


@pytest.mark.unit
def test_a_deadline_of_none_never_passes(jumping_clock):
    d = Deadline(None)
    assert not d.passed()
    assert d.left() is None
    assert d.slice(0.05) == 0.05
    assert not d.passed()


@pytest.mark.unit
def test_a_deadline_of_seconds_passes_and_slices_what_is_left(jumping_clock):
    d = Deadline(150.0)        # made at 0, so it ends at 150
    assert not d.passed()      # read at 100
    assert d.passed()          # read at 200
    assert d.left() == 0.0     # never below the floor
    assert d.slice(0.05) == 0.0


class _Injected:
    """A selector that matches on the twentieth query."""

    def __init__(self):
        self.queries = 0

    def query_selector(self, frame, selector, strict=False):
        self.queries += 1
        return "node" if self.queries >= 20 else None

    def element_state(self, frame, element, state):
        return True

    def dispose(self, frame, element):
        pass


class _Lifecycle:
    main_frame = "frame-1"


def _actions(injected):
    return Actions(object(), "s", _Lifecycle(), injected, acts=PageActs(1))


@pytest.mark.unit
def test_wait_for_selector_without_a_limit_outlasts_any_number(jumping_clock, monkeypatch):
    monkeypatch.setattr("invisible_core.juggler.actions.time.sleep", lambda s: None)
    injected = _Injected()
    assert _actions(injected).wait_for_selector("#late", state="attached",
                                                timeout=None) == "node"
    assert injected.queries == 20


@pytest.mark.unit
def test_wait_for_selector_with_a_limit_still_gives_up(jumping_clock, monkeypatch):
    monkeypatch.setattr("invisible_core.juggler.actions.time.sleep", lambda s: None)
    with pytest.raises(ElementNotActionable):
        _actions(_Injected()).wait_for_selector("#late", state="attached",
                                                timeout=30.0)


@pytest.mark.unit
def test_wait_for_state_without_a_limit_waits_for_the_event():
    """The condition wait takes None as "until notified": the commit that
    arrives later on the reader's thread is what ends it."""
    import threading
    import time as real_time

    from invisible_core.juggler.connection import EventListeners
    from invisible_core.juggler.lifecycle import Lifecycle

    class _Connection(EventListeners):
        def send(self, method, params=None, session=None, timeout=30):
            return None

    c = _Connection()
    life = Lifecycle(c, "S1")
    c.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")

    def commit_later():
        real_time.sleep(0.5)
        c.dispatch_event("Page.navigationCommitted",
                         {"frameId": "F1", "navigationId": "B",
                          "url": "http://b/", "name": ""}, "S1")

    threading.Thread(target=commit_later, daemon=True).start()
    started = real_time.monotonic()
    life.wait_for_state("F1", "commit", navigation="B", timeout=None)
    assert real_time.monotonic() - started >= 0.4
    assert life.frames["F1"].url == "http://b/"
