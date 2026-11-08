"""Two init script changes at once leave the engine with both.

`Page.setInitScripts` replaces the engine's list, and the core changed its list
and sent it with nothing held: a change made while another was being sent could
reach the engine first, and the other's older list, arriving after it, won. A
CDP server built on the core held a lock of its own around every change. Now the
core holds one around the change and the send.
"""
from __future__ import annotations

import threading

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.injected import InjectedScript

pytestmark = pytest.mark.unit

S = "S1"


class _Engine(EventListeners):
    """The list the engine holds is the last one whose send finished."""

    def __init__(self):
        super().__init__()
        self.held = None
        self.during_first_send = None

    def send(self, method, params=None, session=None, timeout=None):
        if method == "Page.setInitScripts":
            hook, self.during_first_send = self.during_first_send, None
            if hook is not None:
                hook()
            self.held = [s["script"] for s in params["scripts"] if s["script"]]
        return {}


def test_a_change_made_while_another_is_sent_is_not_lost():
    engine = _Engine()
    inj = InjectedScript(engine, S)
    second = threading.Thread(target=lambda: inj.add_init_script("b"))

    def meanwhile():
        second.start()
        second.join(0.2)

    engine.during_first_send = meanwhile
    inj.add_init_script("a")
    second.join(2.0)
    assert engine.held == ["a", "b"]
