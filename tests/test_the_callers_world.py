"""The caller's code runs in the caller's world, which the core declares [B322].

Until firefox-49 `page.evaluate` and the other ways of running the caller's
code ran in the page's own realm, so a call to a standard API went through
whatever the page had put in its place: a Cloudflare challenge wraps
`Document.prototype.querySelector` in a Proxy, and one lookup of its token
field from an evaluate got the clearance refused, 11 pairs out of 11. The core
now declares a 'caller' world next to the utility one, and the caller's code
goes there and nowhere else. What that world resolves is the engine's (hybrid
Xrays) and is measured in a browser; these tests pin the core's half: the
declaration, and that nothing of the caller's runs in the page's own context.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.injected import (
    CALLER_WORLD, UTILITY_WORLD, InjectedScript)

pytestmark = pytest.mark.unit

S = "S1"
F = "frame-1"


class _Engine(EventListeners):
    def __init__(self):
        super().__init__()
        self.sent = []

    def world(self, context, name):
        self.dispatch_event("Runtime.executionContextCreated",
                            {"executionContextId": context,
                             "auxData": {"frameId": F, "name": name}}, S)

    def send(self, method, params=None, session=None, timeout=None):
        self.sent.append((method, dict(params or {})))
        if method == "Page.setInitScripts":
            return {}
        return {"result": {"value": "ran in %s" % (params or {}).get("executionContextId")}}


def test_install_declares_the_callers_world_beside_the_utility_world():
    engine = _Engine()
    inj = InjectedScript(engine, S)
    inj.install()
    inj.add_init_script("window.stub = 1")
    scripts = [p["scripts"] for m, p in engine.sent if m == "Page.setInitScripts"][-1]
    assert scripts == [
        {"script": "", "worldName": UTILITY_WORLD},
        {"script": "", "worldName": CALLER_WORLD, "worldKind": "caller"},
        # the caller's init scripts stay in the page's own world: the page
        # must see what they define
        {"script": "window.stub = 1"},
    ]


def test_the_callers_code_never_runs_in_the_pages_own_context():
    engine = _Engine()
    inj = InjectedScript(engine, S)
    engine.world("page-ctx", "")
    engine.world("caller-ctx", CALLER_WORLD)
    engine.world("utility-ctx", UTILITY_WORLD)
    assert inj.evaluate_in_caller(F, "1 + 1") == "ran in caller-ctx"
    inj.evaluate_in_caller_remote(F, "document.body")
    inj.poll_in_caller(F, "() => 1")
    targets = {p.get("executionContextId") for m, p in engine.sent
               if m.startswith("Runtime.")}
    assert targets == {"caller-ctx"}


def test_without_the_callers_world_the_caller_waits_and_is_told_why():
    engine = _Engine()
    inj = InjectedScript(engine, S)
    engine.world("page-ctx", "")
    with pytest.raises(TimeoutError, match="caller's world never appeared"):
        inj.caller_context(F, timeout=0.2)
