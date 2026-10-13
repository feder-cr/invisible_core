"""No protocol command in the action, world and keyboard modules carries a
fixed bound of its own.

Until 41.38.0 nineteen calls in `actions.py`, `injected.py` and `keyboard.py`
waited 5, 10 or 30 s for the engine's answer and then failed, a bound neither
Playwright nor Marionette nor pyppeteer has: an action with a 30 s timeout on
a loaded host failed in 10, and every key of a long text could. A command now
waits for its answer, which a closed pipe still ends at once, or for what is
left of the deadline of the action it belongs to (`_center_point` inside
`_retry`); a command whose answer nobody needs (`Runtime.disposeObject`) is
posted and not waited for at all.

The gate is on the class: a NUMBER given as `timeout=` to any call in these
three modules. The method parameters that default to 30 s are the caller's
deadline, declared in a signature, and are not calls.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from invisible_core.juggler.actions import Actions
from invisible_core.juggler.deadline import Deadline

pytestmark = pytest.mark.unit

_MODULES = ("actions.py", "injected.py", "keyboard.py")
_HERE = Path(__file__).resolve().parents[1] / "src" / "invisible_core" / "juggler"


def test_no_call_in_these_modules_gives_a_number_as_its_timeout():
    found = []
    for name in _MODULES:
        tree = ast.parse((_HERE / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "timeout" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, (int, float)) \
                        and not isinstance(kw.value.value, bool):
                    found.append("%s line %d: timeout=%r"
                                 % (name, node.lineno, kw.value.value))
    assert not found, ("a protocol call with a bound of its own:\n  "
                       + "\n  ".join(found))


class _Connection:
    def __init__(self):
        self.timeouts = []

    def send(self, method, params=None, session=None, timeout=30.0):
        self.timeouts.append(timeout)
        return {"quads": [{"p1": {"x": 0, "y": 0}, "p2": {"x": 10, "y": 0},
                           "p3": {"x": 10, "y": 10}, "p4": {"x": 0, "y": 10}}]}


def _actions():
    actions = Actions.__new__(Actions)
    actions.c = _Connection()
    actions.session = "s"
    return actions


def test_a_quad_read_waits_for_what_is_left_of_the_actions_deadline():
    actions = _actions()
    actions._center_point("F1", "E1", deadline=Deadline(20.0))
    (bound,) = actions.c.timeouts
    assert 19.0 < bound <= 20.0


def test_a_quad_read_without_a_deadline_has_no_bound_of_its_own():
    actions = _actions()
    assert actions._center_point("F1", "E1") == (5.0, 5.0)
    assert actions.c.timeouts == [None]
