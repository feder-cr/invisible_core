"""A page that moves to another content process.

⛔ THE KNOWN-BAD INPUT, measured on firefox-52 on 2026-10-24: a page served
with Cross-Origin-Opener-Policy and Cross-Origin-Embedder-Policy (the shape of
a challenge page) runs in a process of its own, and the page it navigates to
does not. A locator on the first page, then any locator on the second:

    Runtime.callFunction: ... Cannot find object with id = id-10

on every call, for good, while `evaluate()` kept working. The engine counted
its ids per content process, so the second process named its utility world
id-8 again and the InjectedScript cached for the first one's id-8 matched it.
From firefox-53 the ids are unique for the session (juggler Runtime.js); what
the client owes is to drop every world when told they are all gone
(`Runtime.executionContextsCleared`), which it did not do.

The fake engine below behaves as firefox-53 does: every process draws its own
ids, and a context or object of a process that is gone is unknown.
"""
from __future__ import annotations

import itertools

import pytest

from invisible_core.juggler.connection import EventListeners, ProtocolError
from invisible_core.juggler.injected import UTILITY_WORLD, InjectedScript

pytestmark = pytest.mark.unit

S = "S1"
F = "frame-1"
IFRAME = "frame-2"


class _Engine(EventListeners):
    def __init__(self):
        super().__init__()
        self._processes = itertools.count()
        self.builds = 0
        self.sent = []
        #: Runs once, when the next command reaches the engine: the page moves
        #: while that command is on its way.
        self.on_next_command = None
        self._enter()

    def _enter(self):
        self.process = "p%d" % next(self._processes)
        self._ids = itertools.count(1)
        self.live = set()

    def _id(self):
        return "id-%s-%d" % (self.process, next(self._ids))

    def world(self, frame=F):
        context = self._id()
        self.live.add(context)
        self.dispatch_event("Runtime.executionContextCreated",
                            {"executionContextId": context,
                             "auxData": {"frameId": frame, "name": UTILITY_WORLD}}, S)
        return context

    def change_process(self, *, announce=True):
        self._enter()
        self.dispatch_event("Runtime.executionContextsCleared", {}, S)
        if announce:
            self.world()

    def send(self, method, params=None, session=None, timeout=None):
        params = params or {}
        context = params.get("executionContextId")
        self.sent.append((method, context))
        if method == "Page.setInitScripts":
            return {}
        if self.on_next_command is not None:
            hook, self.on_next_command = self.on_next_command, None
            hook()
        if context not in self.live:
            raise ProtocolError(
                '%s: error in channel "content::1/2/3": exception while running '
                'method "%s" in namespace "page": Failed to find execution '
                'context with id = %s' % (method, method.split(".")[1], context))
        if method == "Runtime.evaluate" and not params.get("returnByValue"):
            self.builds += 1
            oid = self._id()
            self.live.add(oid)
            return {"result": {"objectId": oid}}
        for arg in params.get("args", []):
            if "objectId" in arg and arg["objectId"] not in self.live:
                raise ProtocolError("Cannot find object with id = %s" % arg["objectId"])
        return {"result": {"value": "ran in " + self.process}}


def _injected():
    engine = _Engine()
    inj = InjectedScript(engine, S)
    engine.world()
    return engine, inj


def test_the_script_is_built_again_in_the_new_process():
    engine, inj = _injected()
    assert inj.call(F, "(i) => 1") == "ran in p0"
    engine.change_process()
    assert inj.call(F, "(i) => 1") == "ran in p1"
    assert engine.builds == 2


def test_cleared_drops_every_world_of_the_page():
    """Until the new process announces its worlds there is none to aim at, and
    the worlds of a frame that does not come back do not stay behind."""
    engine, inj = _injected()
    engine.world(IFRAME)
    inj.call(F, "(i) => 1")
    engine.change_process(announce=False)
    assert inj.contexts == {}
    with pytest.raises(TimeoutError):
        inj.evaluate(F, "1", timeout=0.3)


def test_a_call_sent_to_a_world_that_left_with_its_process_is_made_in_the_new_one():
    """The id was read, then the page moved: the engine does not know it, and
    this package's read is answered by the new process."""
    engine, inj = _injected()
    inj.call(F, "(i) => 1")
    old = inj.contexts[(F, UTILITY_WORLD)]
    engine.on_next_command = engine.change_process
    engine.sent.clear()
    assert inj.call(F, "(i) => 1") == "ran in p1"
    assert engine.sent[0] == ("Runtime.callFunction", old)
