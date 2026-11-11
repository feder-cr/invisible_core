"""A wait for a frame's world ends the moment the frame is detached, and answers "Frame was detached".

A click on an iframe that its page removes a moment later reads the frame's world, finds it destroyed
(`ContextDestroyed`) and asks for the frame's next world. The next world of a frame that left never comes, so the wait
slept its whole bound (10 s) and ended in `TimeoutError: the utility world (__ctx_aux__) has not been born for frame
subframe-N ... Worlds seen for that frame: none`, with the frame's `Page.frameDetached` already delivered. Measured on
the Fission strategy 1 stress test (an iframe made and removed 50 times while the client clicks and evaluates): 237
of 396 calls ended so, each after 10 s, where Playwright answers at once "Frame was detached".
Now the wait knows the frame is gone: it ends on the detach event if it is waiting, and does not begin if the
detach came first.
"""
from __future__ import annotations

import threading
import time

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.injected import (
    CALLER_WORLD, UTILITY_WORLD, ContextDestroyed, FrameDetached, InjectedScript)

pytestmark = pytest.mark.unit

S = "S1"
F = "subframe-7"
OTHER = "subframe-8"

WAITS = [("context_id", UTILITY_WORLD), ("caller_context", CALLER_WORLD)]


class _Engine(EventListeners):
    def send(self, method, params=None, session=None, timeout=None):
        return {}

    def attach(self, frame, parent="mainframe-1"):
        self.dispatch_event("Page.frameAttached", {"frameId": frame, "parentFrameId": parent}, S)

    def detach(self, frame):
        self.dispatch_event("Page.frameDetached", {"frameId": frame}, S)

    def world(self, frame, context, name):
        self.dispatch_event("Runtime.executionContextCreated",
                            {"executionContextId": context, "auxData": {"frameId": frame, "name": name}}, S)


@pytest.mark.parametrize("wait, world", WAITS)
def test_a_wait_that_is_sleeping_ends_when_the_frame_detaches(wait, world):
    engine = _Engine()
    inj = InjectedScript(engine, S)
    engine.attach(F)
    outcome = {}

    def waiting():
        started = time.monotonic()
        try:
            getattr(inj, wait)(F, timeout=30.0)
        except BaseException as exc:   # noqa: BLE001 - the test reads what came out
            outcome["error"] = exc
        outcome["took"] = time.monotonic() - started

    waiter = threading.Thread(target=waiting)
    waiter.start()
    waiter.join(0.1)
    assert waiter.is_alive(), "the wait ended before anything happened to the frame"
    engine.detach(F)
    waiter.join(5.0)
    assert not waiter.is_alive(), "the wait for the world of a detached frame is still sleeping"
    assert isinstance(outcome["error"], FrameDetached), outcome["error"]
    assert "Frame was detached" in str(outcome["error"])
    assert outcome["took"] < 5.0


@pytest.mark.parametrize("wait, world", WAITS)
def test_a_wait_that_begins_after_the_detach_does_not_sleep(wait, world):
    engine = _Engine()
    inj = InjectedScript(engine, S)
    engine.attach(F)
    engine.world(F, "c1", world)
    engine.detach(F)
    started = time.monotonic()
    with pytest.raises(FrameDetached, match="Frame was detached"):
        getattr(inj, wait)(F, timeout=30.0)
    assert time.monotonic() - started < 1.0


def test_the_retry_after_a_destroyed_world_stops_at_the_detach():
    """The click's own path: the world it was aimed at is destroyed, `in_current_world` asks again for the next one,
    and the frame is gone. `FrameDetached` is not `ContextDestroyed`: it is final, it is not asked again."""
    assert not issubclass(FrameDetached, ContextDestroyed)
    engine = _Engine()
    inj = InjectedScript(engine, S)
    engine.attach(F)
    engine.world(F, "c1", UTILITY_WORLD)
    calls = []

    def attempt(deadline):
        calls.append(1)
        context = inj.context_id(F, timeout=deadline.left())
        # the engine destroys the world and detaches the frame while the command is on its way
        engine.dispatch_event("Runtime.executionContextDestroyed", {"executionContextId": context}, S)
        engine.detach(F)
        raise ContextDestroyed("Execution context was destroyed")

    started = time.monotonic()
    with pytest.raises(FrameDetached, match="Frame was detached"):
        inj.in_current_world(attempt, timeout=30.0)
    assert time.monotonic() - started < 1.0
    assert len(calls) == 2


def test_another_frame_is_not_affected():
    engine = _Engine()
    inj = InjectedScript(engine, S)
    engine.attach(F)
    engine.attach(OTHER)
    engine.world(OTHER, "c9", UTILITY_WORLD)
    engine.detach(F)
    assert inj.context_id(OTHER, timeout=1.0) == "c9"


def test_a_frame_announced_again_under_the_same_id_is_a_frame_again():
    """A frame whose process crashed and navigated anew comes back under its id: its worlds are waited for."""
    engine = _Engine()
    inj = InjectedScript(engine, S)
    engine.attach(F)
    engine.detach(F)
    engine.attach(F)
    engine.world(F, "c2", UTILITY_WORLD)
    assert inj.context_id(F, timeout=1.0) == "c2"


def test_a_world_that_never_comes_in_a_live_frame_still_times_out():
    engine = _Engine()
    inj = InjectedScript(engine, S)
    engine.attach(F)
    with pytest.raises(TimeoutError, match="has not been born"):
        inj.context_id(F, timeout=0.05)
