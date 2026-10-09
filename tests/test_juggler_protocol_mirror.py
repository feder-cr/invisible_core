"""The protocol mirror (`invisible_core.juggler.protocol`), without a browser.

The mirror is GENERATED from the `Protocol.js` inside the shipped engine
(`python -m invisible_core.juggler._gen_protocol`), and the engine enforces it
as a CLOSED WORLD: an undeclared command or field is not ignored, it is
REJECTED, at runtime. These tests read the mirror and the client that writes
to it. What ties the mirror to the engine is `--check` against the pinned
binary, run where the binary is - the Playwright wrapper's e2e job - and so are
the tests that talk to a real browser over the pipe.

Until 38.34.0 these lived three times, in each wrapper's own copy of the
client; the selenium and puppeteer copies were a command behind (decision D85).
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.protocol import COMMANDS, EVENTS

pytestmark = pytest.mark.unit


def test_the_generated_protocol_has_the_five_domains():
    """If the generator ingests a wrong Protocol.js, the count moves."""
    domains = {n.split(".")[0] for n in COMMANDS}
    assert domains == {"Browser", "Page", "Network", "Runtime", "Heap"}
    # ⛔ THESE TWO NUMBERS WERE 71 AND 34 WHILE THE PINNED ENGINE ALREADY
    # DECLARED 75 AND 35, and this test was green the whole time: it fixes
    # the count of the mirror, not the mirror's agreement with the engine, so
    # a mirror that stops being regenerated stays "right" forever. The four
    # commands it lacked (`Network.getResponseBody`, the three screencast
    # ones) and the one event were shipped by the engine and never mirrored.
    # What ties the mirror to the engine is `python -m invisible_core.juggler._gen_protocol --check`
    # against the pinned binary, run where the binary is - the e2e job.
    # 77 since the engine stopped building input events: the one command that
    # did (`Page.dispatchTrustedInputEvents`) is gone, and `Page.selectOptions`
    # and `Page.setUserInput` commit the value through Firefox's own path.
    # 78 since firefox-37 (`Browser.setServiceWorkersBlocked`). The selenium
    # and puppeteer copies of this mirror were a command behind the wrapper's
    # until all three came from the core.
    assert len(COMMANDS) == 78, "commands: %d" % len(COMMANDS)
    assert len(EVENTS) == 35, "events: %d" % len(EVENTS)


def test_the_commands_the_client_will_use_are_declared():
    """The browser enforces the schema as a CLOSED WORLD: an undeclared
    command does not degrade, it REJECTS. These are the ones on the
    minimum path."""
    for name in ("Browser.enable", "Browser.createBrowserContext",
                 "Browser.newPage", "Page.navigate", "Runtime.evaluate",
                 "Browser.setServiceWorkersBlocked",
                 # Asked after every click and hover since [B217]: an engine
                 # without it refuses the question, and the mirror must say so
                 # before a browser does.
                 "Page.pointerLanded",
                 # How a picked value, an option and a file reach a control:
                 # through Firefox's own user paths, which fire the events.
                 "Page.setUserInput", "Page.selectOptions",
                 "Page.setFileInputFiles"):
        assert name in COMMANDS, name
    # The engine no longer builds `input`/`change` itself: a client still
    # asking for them would be refused at the first `select_option`.
    assert "Page.dispatchTrustedInputEvents" not in COMMANDS


def test_every_protocol_name_this_package_writes_is_declared():
    """⛔ A COMMAND THE ENGINE NO LONGER HAS IS FOUND HERE, NOT IN A BROWSER.

    The mirror is regenerated from the shipped engine; the code that calls it
    is not. When `Page.dispatchTrustedInputEvents` left the engine, the mirror
    lost it and `actions.py` still sent it: nothing in the default selection
    noticed, and the first `select_option` against the new engine failed. So
    every `"Domain.name"` literal in the package must be a command or an event
    of the mirror. The known-bad input: put that old name back in a `send`.
    """
    import pathlib
    import re

    import invisible_core.juggler as juggler

    pattern = re.compile(
        r"""["']((?:Browser|Page|Network|Runtime|Heap)\.[a-z]\w*)["']""")
    root = pathlib.Path(juggler.__file__).parent
    unknown = {}
    for source in sorted(root.rglob("*.py")):
        if source.name in ("protocol.py", "_gen_protocol.py"):
            continue
        for name in pattern.findall(source.read_text(encoding="utf-8")):
            if name not in COMMANDS and name not in EVENTS:
                unknown.setdefault(name, []).append(source.name)
    assert not unknown, "not in the protocol mirror: %r" % unknown


def test_every_type_uses_only_the_eight_known_combinators():
    """A new combinator in Protocol.js must make the generator REJECT,
    not slip through unnoticed.

    ⛔ The set below holds NINE names for eight combinators: `Object` is
    not a `t.` combinator, it is the structural kind the generator emits
    for a brace literal. The count in the function name is the number of
    things `PrimitiveTypes.js` declares and we accept, which is what a
    reader of this test cares about.
    """
    known = {"String", "Number", "Boolean", "Any", "Enum",
             "Nullable", "Optional", "Array", "Object"}
    seen: set = set()
    visited = 0

    def walk(t):
        nonlocal visited
        if not isinstance(t, dict):
            return
        visited += 1
        seen.add(t.get("k"))
        if "of" in t:
            walk(t["of"])
        for v in (t.get("fields") or {}).values():
            walk(v)

    for spec in COMMANDS.values():
        walk(spec.get("params"))
        walk(spec.get("returns"))
    for spec in EVENTS.values():
        walk(spec)
    # ⛔ THE COVERAGE ASSERTION COMES FIRST, and it exists because this
    # test nearly went blind in silence. The walk recurses through the keys
    # `of` and `fields`; when those two were renamed from `di` and `campi`,
    # `walk` stopped descending and `seen` collapsed to the handful of
    # top-level kinds - while the assertion below still PASSED, because a
    # smaller set is still a subset. A gate that checks less and says the
    # same thing is worse than one that fails.
    assert visited > 400, (
        "the walk only visited %d type nodes: it is not descending any "
        "more, so the assertion below proves almost nothing" % visited)
    assert seen <= known, "unexpected combinators: %s" % (seen - known)
