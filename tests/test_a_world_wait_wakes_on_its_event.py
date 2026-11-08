"""A wait for a frame's world wakes on the event that makes the world.

`context_id` and `caller_context` waited for a frame's world by sleeping 10 ms
in turns until the creation event had filled the map, and every client that
asks for a world while a page is loading paid for it: a CDP server built on the
core read the wait as a page that does not answer. Now the creation event
notifies a condition the wait sleeps on, so the wait ends when the event comes
and sleeps in no turns.
"""
from __future__ import annotations

import threading

import pytest

from invisible_core.juggler import injected as injected_module
from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.injected import (
    CALLER_WORLD, UTILITY_WORLD, InjectedScript)

pytestmark = pytest.mark.unit

S = "S1"
F = "frame-1"


class _Engine(EventListeners):
    def send(self, method, params=None, session=None, timeout=None):
        return {}

    def world(self, context, name):
        self.dispatch_event("Runtime.executionContextCreated",
                            {"executionContextId": context,
                             "auxData": {"frameId": F, "name": name}}, S)


@pytest.fixture
def no_turns(monkeypatch):
    def refuse(seconds):
        raise AssertionError("the wait slept a turn of %s s" % seconds)
    monkeypatch.setattr(injected_module, "time",
                        type("time", (), {"sleep": staticmethod(refuse)}),
                        raising=False)


@pytest.mark.parametrize("world, wait", [
    (UTILITY_WORLD, "context_id"),
    (CALLER_WORLD, "caller_context"),
])
def test_the_wait_ends_on_the_creation_event(no_turns, world, wait):
    engine = _Engine()
    inj = InjectedScript(engine, S)
    got = {}
    waiter = threading.Thread(
        target=lambda: got.setdefault("context", getattr(inj, wait)(F, timeout=5.0)))
    waiter.start()
    waiter.join(0.05)
    assert waiter.is_alive(), "the wait ended before the world existed"
    engine.world("c7", world)
    waiter.join(2.0)
    assert not waiter.is_alive()
    assert got["context"] == "c7"


def test_a_world_that_never_comes_still_times_out(no_turns):
    inj = InjectedScript(_Engine(), S)
    with pytest.raises(TimeoutError, match="has not been born"):
        inj.context_id(F, timeout=0.05)
