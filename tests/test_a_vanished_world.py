"""A command aimed at an execution context that no longer exists.

⛔ THE KNOWN-BAD INPUT, measured on firefox-41 on 2026-10-12
(`C:/tmp/b230/reattach_events.py`): `goto(..., "domcontentloaded")` on a page
that replaces itself, then `locator("body").count()` at once. 5 in 5 when the
page calls `location.replace` from its `<head>`, 5 in 5 at 0 ms after `load`,
with the engine's raw text reaching the caller:

    Runtime.callFunction: ... Failed to find execution context with id = id-35

The id was read, the frame navigated, and the engine refused the command BEFORE
running it (`Runtime.js`, `findExecutionContext`). Nothing recognised that
answer. Now it is `ContextDestroyed`; a command of values only is made again in
the frame's current world, within the caller's time; one carrying an element
is not, because the element went with its world; and the action loop starts
over on it only while it has not acted.
"""
from __future__ import annotations

import threading

import pytest

from invisible_core.juggler import PageActs
from invisible_core.juggler.actions import Actions
from invisible_core.juggler.connection import EventListeners, ProtocolError
from invisible_core.juggler.injected import (
    UTILITY_WORLD, ContextDestroyed, InjectedScript)

pytestmark = pytest.mark.unit

S = "S1"
F = "frame-1"


class _Engine(EventListeners):
    """Runtime commands answered per context. A context in `dead` is refused
    with Juggler's own text; `on_refusal` runs once, on the first refusal (the
    navigation's new world being born)."""

    def __init__(self):
        super().__init__()
        self.dead = set()
        self.on_refusal = None
        self.sent = []

    def world(self, context, name=UTILITY_WORLD):
        self.dispatch_event("Runtime.executionContextCreated",
                            {"executionContextId": context,
                             "auxData": {"frameId": F, "name": name}}, S)

    def send(self, method, params=None, session=None, timeout=None):
        params = params or {}
        context = params.get("executionContextId")
        self.sent.append((method, context))
        if method == "Page.setInitScripts":
            return {}
        if context in self.dead:
            if self.on_refusal is not None:
                hook, self.on_refusal = self.on_refusal, None
                hook()
            raise ProtocolError(
                '%s: error in channel "content::1/2/3": exception while running '
                'method "%s" in namespace "page": Failed to find execution '
                'context with id = %s' % (method, method.split(".")[1], context))
        if method == "Runtime.evaluate" and not params.get("returnByValue"):
            return {"result": {"objectId": "script@" + context}}
        return {"result": {"value": "ran in " + context}}


def _injected():
    engine = _Engine()
    inj = InjectedScript(engine, S)
    engine.world("c1")
    return engine, inj


def test_a_call_of_values_is_made_in_the_frames_new_world():
    engine, inj = _injected()
    assert inj.call(F, "(i, s) => 1", "body") == "ran in c1"
    engine.dead.add("c1")
    engine.on_refusal = lambda: engine.world("c2")
    assert inj.call(F, "(i, s) => 1", "body") == "ran in c2"
    assert ("Runtime.callFunction", "c1") in engine.sent


def test_the_dead_world_is_forgotten_before_its_destruction_is_announced():
    """The refusal can arrive before `executionContextDestroyed`: the new
    world is born a moment later, and until then the call waits for it rather
    than being sent to the dead id again."""
    engine, inj = _injected()
    inj.call(F, "(i) => 1")
    engine.dead.add("c1")
    engine.on_refusal = lambda: threading.Timer(0.3, engine.world, ("c2",)).start()
    engine.sent.clear()
    assert inj.evaluate(F, "1") == "ran in c2"
    assert engine.sent.count(("Runtime.evaluate", "c1")) == 1


def test_a_call_carrying_an_element_is_not_made_again():
    engine, inj = _injected()
    inj.call(F, "(i) => 1")
    engine.dead.add("c1")
    engine.on_refusal = lambda: engine.world("c2")
    with pytest.raises(ContextDestroyed, match="Execution context was destroyed"):
        inj.call(F, "(i, el) => el.textContent", {"objectId": "element@c1"})
    assert not any(c == "c2" for m, c in engine.sent if m == "Runtime.callFunction")


def test_an_explicit_context_that_is_gone_is_typed():
    engine, inj = _injected()
    engine.dead.add("c1")
    with pytest.raises(ContextDestroyed):
        inj.call_raw("c1", "() => 1", [])


def test_a_world_that_never_comes_back_ends_at_the_callers_time():
    engine, inj = _injected()
    engine.dead.add("c1")
    with pytest.raises(TimeoutError):
        inj.evaluate(F, "1", timeout=0.5)


class _Resolver:
    """The injected half of an action: `element_states` refuses once, as a
    navigation under the resolution would."""

    def __init__(self, refuse_states=0):
        self.refuse_states = refuse_states

    def query_selector(self, frame, selector, strict=False):
        return "element"

    def element_states(self, frame, element, states):
        if self.refuse_states:
            self.refuse_states -= 1
            raise ContextDestroyed("Execution context was destroyed")
        return {"ok": True}

    def dispose(self, frame, element):
        pass


class _Lifecycle:
    main_frame = F


def _actions(resolver):
    return Actions(object(), S, _Lifecycle(), resolver, acts=PageActs(1))


def test_the_action_loop_starts_over_when_the_world_goes_during_resolution():
    acted = []
    actions = _actions(_Resolver(refuse_states=1))
    actions._retry("#b", lambda f, el, p: acted.append(el) or "done",
                   states=["visible"], needs_point=False, timeout=5)
    assert acted == ["element"]


def test_the_action_loop_does_not_act_twice_when_the_world_goes_after_acting():
    """`check` reads the box after its click; a click that submits a form
    destroys that world. Starting over there would click on the next page."""
    acted = []

    def run(f, element, point):
        acted.append(element)
        raise ContextDestroyed("Execution context was destroyed")

    actions = _actions(_Resolver())
    with pytest.raises(ContextDestroyed):
        actions._retry("#b", run, states=["visible"], needs_point=False,
                       timeout=5)
    assert acted == ["element"]
