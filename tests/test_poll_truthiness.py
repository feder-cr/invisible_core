"""A wait ends when the caller's predicate is truthy AS JAVASCRIPT JUDGES IT [B323].

`wait_for_function("() => document.querySelector('#campo')")` never ended on
firefox-48 although the element existed: the predicate ran by value and Python
judged the serialized value, and a node serializes to nothing truthy. Found by
the fourth wave of the workbench's `tests/gates/observable_crossings.py` (96
turns, none truthy). `js_truthy` judges the engine's RemoteObject instead, and
`poll_in_main` is the one place that knows what a turn of a wait is.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.injected import InjectedScript, js_truthy

pytestmark = pytest.mark.unit

S = "S1"
F = "frame-1"


@pytest.mark.parametrize("remote, truthy", [
    # objects and symbols carry an objectId (juggler/content/Runtime.js
    # `_createRemoteObject`): truthy, except document.all, whose typeof is
    # 'undefined'
    ({"objectId": "1", "type": "object", "subtype": "node"}, True),
    ({"objectId": "2", "type": "object", "subtype": "array"}, True),
    ({"objectId": "3", "type": "function"}, True),
    ({"objectId": "4", "type": "symbol"}, True),
    ({"objectId": "5", "type": "undefined"}, False),
    # the four numbers JSON cannot carry
    ({"unserializableValue": "NaN"}, False),
    ({"unserializableValue": "-0"}, False),
    ({"unserializableValue": "Infinity"}, True),
    ({"unserializableValue": "-Infinity"}, True),
    # plain values; undefined arrives with no `value` at all
    ({}, False),
    ({"value": None}, False),
    ({"value": False}, False),
    ({"value": True}, True),
    ({"value": 0}, False),
    ({"value": 0.5}, True),
    ({"value": -3}, True),
    ({"value": ""}, False),
    ({"value": "0"}, True),
])
def test_js_truthy_is_javascripts_to_boolean(remote, truthy):
    assert js_truthy(remote) is truthy


class _Engine(EventListeners):
    """`Runtime.evaluate` answers the RemoteObject the test sets; every
    command is recorded, so a test can count runs and releases."""

    def __init__(self, remote):
        super().__init__()
        self.remote = remote
        self.sent = []

    def main_world(self, context):
        self.dispatch_event("Runtime.executionContextCreated",
                            {"executionContextId": context,
                             "auxData": {"frameId": F, "name": ""}}, S)

    def send(self, method, params=None, session=None, timeout=None):
        params = params or {}
        self.sent.append((method, dict(params)))
        if method == "Runtime.evaluate":
            return {"result": dict(self.remote)}
        return {}

    def post(self, method, params=None, session=None):
        self.sent.append((method, dict(params or {})))


def _poll(remote):
    engine = _Engine(remote)
    inj = InjectedScript(engine, S)
    engine.main_world("main-1")
    return engine, inj.poll_in_main(F, "() => 1", timeout=2)


def test_a_node_ends_the_wait_and_is_the_handle():
    remote = {"objectId": "node-7", "type": "object", "subtype": "node"}
    engine, got = _poll(remote)
    assert got == remote
    runs = [p for m, p in engine.sent if m == "Runtime.evaluate"]
    # ONE run of the caller's code per turn, by reference, in the main world
    assert len(runs) == 1
    assert runs[0]["returnByValue"] is False
    assert runs[0]["executionContextId"] == "main-1"
    assert not [m for m, _ in engine.sent if m == "Runtime.disposeObject"]


@pytest.mark.parametrize("remote", [{"value": None}, {"value": 0}, {},
                                    {"unserializableValue": "NaN"}])
def test_a_falsy_value_is_another_turn(remote):
    engine, got = _poll(remote)
    assert got is None
    assert not [m for m, _ in engine.sent if m == "Runtime.disposeObject"]


def test_a_falsy_object_is_released_in_the_world_it_lives_in():
    engine, got = _poll({"objectId": "all-1", "type": "undefined"})
    assert got is None
    released = [p for m, p in engine.sent if m == "Runtime.disposeObject"]
    assert released == [{"executionContextId": "main-1", "objectId": "all-1"}]
