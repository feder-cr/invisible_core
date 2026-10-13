"""The injected script, called from Python.

⛔ THIS MODULE REIMPLEMENTS NOTHING, and the reason is that it could not:
selector engines and actionability work on the DOM, and Python is not in
the page. JavaScript stays JavaScript; this file only handles loading it
into the right world and calling it.

THE TWO WORLDS, which are the reason for this entire file. Firefox gives
automation a separate context that sees the same DOM **through an Xray**:
inside it `addEventListener`, `textContent` and `querySelector` are the
NATIVE ones, not the ones the site may have replaced. In the MAIN world
everything belongs to the site, and every read is accountable to whoever
wrapped the accessor. On 2026-08-27 two spots that used the wrong world
produced 13 listeners plus one `textContent` read for every
`bounding_box()`: see `31-client-fork.md` §3.9 and §3.10.

**Rule: this file ALWAYS works in the utility world.**

HOW IT IS CREATED. There is no "create a world" command: you send
`Page.setInitScripts` with an **empty** script and a `worldName`, and
Juggler announces the context with `Runtime.executionContextCreated`,
whose `auxData.name` carries that name. ⛔ The name is `__ctx_aux__` and
**is not the upstream one**: our fork renamed it because it used to
travel on the page's window (`31-client-fork.md` §3.5). Changing it here
without changing it there breaks the hookup silently.
"""
from __future__ import annotations

import json
import pathlib
import time
from typing import Any, Optional

from .connection import ProtocolError, ReplyTimeout
from .deadline import Deadline

#: ⛔ Must stay equal to the driver's `UTILITY_WORLD_NAME` and to the name
#: that `31-client-fork.md` §3.5 declares. This is not a cosmetic detail:
#: it is the key that identifies the right context in
#: `executionContextCreated`.
UTILITY_WORLD = "__ctx_aux__"

_SOURCE = pathlib.Path(__file__).with_name("injected.js")


class EvaluationError(RuntimeError):
    """The JavaScript raised. Carries the PAGE's text and stack."""


class ContextDestroyed(RuntimeError):
    """The execution context a command was aimed at went away before the
    command answered: the frame navigated, or its document was replaced.

    ⛔ NOT AN `EvaluationError`, which says the page's JavaScript raised, and
    the clients translate the two differently: a selector query that raised is
    an invalid selector to Selenium and Puppeteer, a vanished world is a stale
    element or a navigation.

    ⛔ AND IT DOES NOT SAY WHETHER THE COMMAND RAN. The engine answers it in
    two shapes. `Failed to find execution context with id` is its check before
    running anything (`Runtime.js`, `findExecutionContext`), but that is ALSO
    the answer to a command that ran: Juggler's channel sends every message
    still waiting for its answer again to the document that replaces the one it
    went to (`SimpleChannel._markAsReady`), and the new document refuses an id
    it does not have. Measured on firefox-41: a call waiting on a promise when
    its page navigated came back that way after 1.03 s. `Execution context was
    destroyed!` is the other shape, a call waiting on a promise whose context
    the engine destroyed under it (`Runtime.destroyExecutionContext`). Until
    41.38.0 this docstring said a refused command never ran, and the clients
    sent the caller's own code again on that word: a script that awaited across
    a navigation ran twice ([B285]). Every original answers both shapes the
    same way and none sends the code again: Playwright raises "Execution context
    was destroyed, most likely because of a navigation", pyppeteer raises
    `NetworkError`, Marionette returns null for `executeScript`.

    The text is Playwright's, and so is the reason for a type: until 41.36.0
    this arrived as the engine's raw text, which nothing above recognised, and a
    read right after a `goto` on a page that replaces itself failed with it (5
    in 5 on a local page that calls `location.replace` from its `<head>`).
    """


#: The engine's two answers for a context that went away under a command: the
#: refusal of an id it does not have, and the rejection of a call that was
#: waiting on a promise when its context was destroyed.
_CONTEXT_GONE = ("Failed to find execution context with id",
                 "Execution context was destroyed!")

#: The element reads, each a function of `(injected, el, ...args)`, in ONE
#: table: a read on a handle (`read`) and a read on a selector (`read_match`)
#: run the same text, so the two cannot drift.
#:
#: ⛔ Every read goes through the UTILITY world, hence through the Xray. The
#: same lines run in the MAIN world would be accountable to a site that
#: wrapped the accessor, the defect measured in §3.9 and §3.10 of
#: `31-client-fork.md`.
_READS = {
    "textContent": "(injected, el) => el.textContent || ''",
    "innerText": "(injected, el) => el.innerText",
    "innerHTML": "(injected, el) => el.innerHTML",
    # ⛔ Only valid on input, textarea and select: on a div it would silently
    # return `undefined`, so it REFUSES instead.
    "inputValue": (
        "(injected, el) => {"
        "  const e = injected.retarget(el, 'follow-label');"
        "  const n = e && e.nodeName.toLowerCase();"
        "  if (n !== 'input' && n !== 'textarea' && n !== 'select')"
        "    throw new Error('Node is not an <input>, <textarea> or <select> element');"
        "  return e.value; }"),
    # ⛔ An ABSENT attribute must come back as `None`, not as the empty string:
    # an empty value and an absence are two different things.
    "attribute": "(injected, el, n) => el.getAttribute(n)",
    "state": "(injected, el, s) => injected.elementState(el, s)",
    # ⛔ In the utility world like every other read, which matters more here
    # than anywhere: it walks the whole subtree and reads a role, a name and
    # a description off every node.
    "ariaSnapshot": "(injected, el, o) => injected.ariaSnapshot(el, o)",
}


class InjectedScript:
    """One `InjectedScript` per frame, in the utility world."""

    def __init__(self, connection, session: str):
        self.c = connection
        self.session = session
        #: (frameId, worldName) -> executionContextId
        self.contexts: dict = {}
        #: frameId -> (executionContextId it was built in, objectId)
        #:
        #: ⛔ THE CONTEXT IS PART OF THE KEY, AND IT IS NOT BOOKKEEPING: it is
        #: what makes a stale handle impossible to use instead of merely
        #: unlikely. An objectId means nothing on its own - the engine looks it
        #: up in the worlds the frame has NOW - so a handle is only the handle
        #: for as long as the world it was minted in is still the frame's
        #: utility world. Storing the pair lets `handle()` ask that question
        #: directly; storing the objectId alone left it to be inferred from the
        #: destruction events arriving complete and in order, and they do not.
        #:
        #: Measured 2026-09-16 on a back forward cache restore: the engine
        #: destroyed contexts id-16 and id-17, which this client had never been
        #: told about - it believed the frame's worlds were id-14 and id-15 -
        #: so no entry matched, nothing was dropped, and the next locator sent
        #: an objectId minted in id-15 to a frame whose worlds were now id-20
        #: and id-21. `Cannot find object with id`, while `evaluate()` kept
        #: working because it resolves the context afresh every time.
        self._handle: dict = {}
        #: The caller's own init scripts, in the order they were added. Kept
        #: here because this object is the one that owns the engine's init
        #: script list - see `_push_scripts`.
        self._init_scripts: list = []
        # ⛔ Registered on the connection's list, never chained onto whoever
        # was there before: see the note in `Connection._listeners`.
        connection.add_listener(self._route)

    def _route(self, method: str, params: dict,
               event_session: Optional[str]) -> None:
        if event_session == self.session:
            self._on_event(method, params)

    def detach(self) -> None:
        """Stop listening. Called when the page this belongs to goes away."""
        self.c.remove_listener(self._route)

    def _on_event(self, method: str, p: dict) -> None:
        if method == "Runtime.executionContextCreated":
            aux = p.get("auxData") or {}
            self.contexts[(aux.get("frameId"), aux.get("name") or "")] = \
                p["executionContextId"]
        elif method == "Runtime.executionContextDestroyed":
            dead = p["executionContextId"]
            for k in [k for k, v in self.contexts.items() if v == dead]:
                self.contexts.pop(k, None)
            # ⛔ NOTHING IS DISCARDED HERE ANY MORE, and that is the fix rather
            # than an omission. This used to drop a frame's handle when the
            # frame's utility world had just left `contexts`, which is right
            # only while every destruction is announced with an id this client
            # already knows. On a back forward cache restore it is not: the
            # engine destroys the worlds of the document it is leaving, and
            # those can be worlds this client was never told were created, so
            # no entry matched and the handle survived its world.
            #
            # `handle()` now asks the question directly instead, which cannot
            # be wrong: a handle is usable exactly while the context it was
            # minted in is still the frame's utility context. Whether an event
            # arrived stops mattering.

    # ── the world ───────────────────────────────────────────────────────────
    def install(self) -> None:
        """Creates the utility world. An EMPTY script is enough: what
        matters is the `worldName`, which is the only thing that makes
        the context come into being."""
        self._push_scripts()

    def _push_scripts(self) -> None:
        """Send the WHOLE list, because the command replaces it.

        ⛔ ONE PLACE OWNS THIS LIST. `Page.setInitScripts` takes the complete
        set and replaces whatever was there, so a second caller that sends only
        its own script deletes the utility world - and the utility world is
        what every selector, every actionability check and every evaluation in
        this package runs in. The failure would not look like a lost init
        script: it would look like the browser having no DOM.
        """
        scripts = [{"script": "", "worldName": UTILITY_WORLD}]
        scripts += [{"script": s} for s in self._init_scripts]
        self.c.send("Page.setInitScripts", {"scripts": scripts},
                    session=self.session)

    def add_init_script(self, source: str) -> None:
        """A script the caller wants run before anything on every document.

        ⛔ APPENDED, never replacing. Playwright's `add_init_script` is
        cumulative by contract - callers add a stub, then another - and the
        engine's command is not, so the accumulation has to live here.

        ⛔ IN THE MAIN WORLD, with no `worldName`. The point of a caller's
        init script is to be seen BY THE PAGE; putting it in the utility world
        would run it behind the Xray, where the page cannot see anything it
        defines, and the call would succeed while doing nothing observable.
        """
        self._init_scripts.append(source)
        self._push_scripts()

    def remove_init_script(self, source: str) -> None:
        """Undo one. Removes ONE occurrence, not every copy.

        ⛔ The same source added twice is two scripts by Playwright's
        contract, and disposing one handle must leave the other alive.
        """
        if source in self._init_scripts:
            self._init_scripts.remove(source)
            self._push_scripts()

    def _send_in(self, method: str, params: dict, *,
                 timeout: Optional[float]) -> Any:
        """Every command aimed at ONE execution context goes through here.

        ⛔ ONE PLACE KNOWS WHAT A VANISHED CONTEXT LOOKS LIKE. The id was read
        a moment ago and the frame may have navigated since; the engine then
        answers in one of the two shapes of `_CONTEXT_GONE`. Either becomes
        `ContextDestroyed`, and the dead id leaves the registry at once: its
        `executionContextDestroyed` can arrive after this answer, and until it
        does the registry would hand the same dead id to the next caller.
        Whether the command ran is NOT known here (see `ContextDestroyed`).
        """
        try:
            return self.c.send(method, params, session=self.session,
                               timeout=timeout)
        except ReplyTimeout:
            raise
        except ProtocolError as exc:
            if not any(shape in str(exc) for shape in _CONTEXT_GONE):
                raise
            context = params.get("executionContextId")
            self._forget_context(context)
            raise ContextDestroyed(
                "Execution context was destroyed, most likely because of a "
                "navigation (%s, context %s)" % (method, context)) from exc

    def _forget_context(self, context: Any) -> None:
        for k in [k for k, v in self.contexts.items() if v == context]:
            self.contexts.pop(k, None)
        for f in [f for f, (c, _) in self._handle.items() if c == context]:
            self._handle.pop(f, None)

    def in_current_world(self, attempt, *, timeout: Optional[float]) -> Any:
        """Run `attempt(deadline)`, and again in the frame's CURRENT world if
        the one it was aimed at has gone, until it answers or the caller's
        time is up.

        ⛔ THE ONE COPY OF THIS RULE. Until 41.37.0 each client carried its own
        loop (`_bridge.across_navigation` in invisible_selenium and in
        invisible_puppeteer, the same twenty lines); a client's `attempt`
        reads the world itself (`main_context`, `context_id`), so a turn after
        a refusal waits for the frame's next world, and decides what is final
        by raising anything but `ContextDestroyed`.

        ⛔ ONLY FOR A COMMAND THAT IS HARMLESS TO MAKE TWICE: this package's own
        reads, which carry nothing but values. `ContextDestroyed` does not say
        whether the command ran (a command waiting on a promise when its page
        navigated is refused by the new document in the same words as one that
        never ran), so what is made again here must be a command for which
        running twice is the same as running once. NEVER THE CALLER'S CODE: a
        script with an effect ran twice that way until 41.38.0 ([B285]), and no
        original sends it again. A command carrying an element's objectId is
        not made again either: the element belonged to the world that is gone,
        and `ContextDestroyed` is the true answer for it. Each turn waits for
        the frame's next world, which `_send_in` made necessary by forgetting
        the dead one.
        """
        deadline = Deadline(timeout)
        while True:
            try:
                return attempt(deadline)
            except ContextDestroyed:
                if deadline.passed():
                    raise

    def context_id(self, frame_id: str, *, timeout: Optional[float] = 10.0) -> str:
        key = (frame_id, UTILITY_WORLD)
        deadline = Deadline(timeout)
        while key not in self.contexts:
            if deadline.passed():
                worlds = sorted(n for f, n in self.contexts if f == frame_id)
                raise TimeoutError(
                    "the utility world (%s) has not been born for frame "
                    "%s in %.0fs. Worlds seen for that frame: %s. Did "
                    "you call install()?" % (UTILITY_WORLD, frame_id,
                                             timeout, worlds or "none"))
            time.sleep(0.01)
        return self.contexts[key]

    # ── evaluation ──────────────────────────────────────────────────────────
    def _result(self, response: dict) -> dict:
        """⛔ An exception from the page does NOT arrive as a protocol
        error: it arrives as an `exceptionDetails` field inside a
        SUCCESSFUL response. Whoever only looks at the return code reads
        `None` and moves on."""
        exc = (response or {}).get("exceptionDetails")
        if exc:
            raise EvaluationError(
                "%s%s" % (exc.get("text") or exc.get("value") or "exception",
                          ("\n" + exc["stack"]) if exc.get("stack") else ""))
        return (response or {}).get("result") or {}

    def evaluate(self, frame_id: str, expression: str, *,
                by_value: bool = True, timeout: Optional[float] = 30.0) -> Any:
        """An expression in the frame's utility world, made again in the
        frame's current world if the one it was aimed at has gone."""
        def attempt(deadline):
            return self._evaluate_in(
                self.context_id(frame_id, timeout=deadline.left()),
                expression, by_value=by_value, timeout=deadline.left())
        return self.in_current_world(attempt, timeout=timeout)

    def _evaluate_in(self, context: str, expression: str, *, by_value: bool,
                     timeout: Optional[float]) -> Any:
        r = self._result(self._send_in(
            "Runtime.evaluate",
            {"executionContextId": context,
             "expression": expression, "returnByValue": by_value},
            timeout=timeout))
        return r.get("value") if by_value else r.get("objectId")

    def call(self, frame_id: str, declaration: str, *arguments,
             by_value: bool = True, timeout: Optional[float] = 30.0,
             repeatable: bool = True) -> Any:
        """`declaration` is a JS declaration; the FIRST argument passed
        is always the InjectedScript, the way the driver does it.

        Made again in the frame's current world when the one it was aimed at
        has gone, unless an argument is an element's objectId: see
        `in_current_world`. The InjectedScript itself is not such an
        argument - each world has its own, and `handle()` gives the one of the
        world the call is sent to.

        ⛔ `repeatable=False` WHEN THE DECLARATION CARRIES THE CALLER'S CODE
        (`$$eval`): it is sent once, and a world that went under it is
        `ContextDestroyed`. Only the caller knows which kind of declaration it
        passes; this package's own reads are the default. [B285]"""
        values = [a if isinstance(a, dict) and ("objectId" in a or "value" in a)
                  else {"value": a} for a in arguments]

        def attempt(deadline):
            script = self.handle(frame_id)
            # The context the script was built in, not one read again: the
            # objectId means something only there.
            context = self._handle[frame_id][0]
            return self._result(self._send_in(
                "Runtime.callFunction",
                {"executionContextId": context,
                 "functionDeclaration": declaration,
                 "args": [{"objectId": script}] + values,
                 "returnByValue": by_value},
                timeout=deadline.left()))

        if not repeatable or any("objectId" in a for a in values):
            r = attempt(Deadline(timeout))
        else:
            r = self.in_current_world(attempt, timeout=timeout)
        return r.get("value") if by_value else r.get("objectId")

    def handle(self, frame_id: str) -> str:
        """The frame's InjectedScript, built once per world it lives in.

        ⛔ "Built only once" was the old sentence and it was the bug. Once per
        FRAME is wrong: the script is an object inside one execution context,
        and the frame outlives its contexts - every navigation replaces them,
        and so does a restore from the back forward cache. What this returns
        has to be the script of the world the frame has NOW.

        Asking `contexts` is the whole of the check, and it is decidable at any
        moment from state this client already keeps. The alternative, which is
        what stood here, was to keep the objectId and trust that some earlier
        event had removed it when it went stale; a restore is exactly where
        that trust is misplaced.
        """
        current = self.contexts.get((frame_id, UTILITY_WORLD))
        cached = self._handle.get(frame_id)
        if cached is not None and current is not None and cached[0] == current:
            return cached[1]
        options = {
            # ⛔ `isUnderTest` FALSE, always. If true, the InjectedScript
            # plants `window.builtins` and `window.__injectedScript` on
            # the page's window, ENUMERABLE: it is the tell that
            # `31-client-fork.md` §3.3 declares turned off, and turning
            # it back on from here would restore it.
            "isUnderTest": False,
            "sdkLanguage": "python",
            "testIdAttributeName": "data-testid",
            "stableRafCount": 1,
            "browserName": "firefox",
            "shouldPrependErrorPrefix": False,
            # ⛔ TRUE, and this is the line that keeps the 13 listeners
            # out: with false the constructor installs them on the
            # PAGE's addEventListener.
            "isUtilityWorld": True,
            "customEngines": [],
        }
        source = _SOURCE.read_text(encoding="utf-8")
        expression = ("(() => { const module = {};\n%s\n"
                      "return new (module.exports.InjectedScript())"
                      "(globalThis, %s); })();"
                      % (source, json.dumps(options)))
        # ⛔ THE CONTEXT IT IS STORED WITH IS THE ONE IT WAS BUILT IN. Until
        # 41.36.0 the context was read again after the build, and a frame that
        # navigated in between paired the objectId with a world it did not
        # exist in. Pairing it with the context the build was sent to is exact;
        # if that context is gone, the build raises `ContextDestroyed` and the
        # caller's `in_current_world` asks again.
        context = self.context_id(frame_id)
        oid = self._evaluate_in(context, expression, by_value=False,
                                timeout=None)
        if not oid:
            raise EvaluationError(
                "the InjectedScript did not return an object: "
                "is the source the right one?")
        self._handle[frame_id] = (context, oid)
        return oid

    # ── the little needed on top ────────────────────────────────────────────
    def query_selector(self, frame_id: str, selector: str,
                       *, strict: bool = False) -> Optional[str]:
        """The objectId of the first matching element, or None."""
        return self.call(
            frame_id,
            "(injected, sel, strict) => {"
            "  const p = injected.parseSelector(sel);"
            "  return injected.querySelector(p, document, strict) || null; }",
            selector, strict, by_value=False)

    def count(self, frame_id: str, selector: str) -> int:
        return self.call(
            frame_id,
            "(injected, sel) => injected.querySelectorAll("
            "injected.parseSelector(sel), document).length",
            selector)

    def evaluate_in_main(self, frame_id: str, expression: str, *,
                         by_value: bool = True, timeout: Optional[float] = 30.0,
                         repeatable: bool = False):
        """Evaluate in the PAGE's own world, deliberately and rarely.

        ⛔ THIS IS THE ONE PLACE THAT LEAVES THE XRAY, AND IT NEEDS A REASON
        EVERY TIME. Everything else in this module runs in the utility world
        precisely so a site cannot count our reads. Running here is visible to
        anything that has wrapped an accessor.

        The reason it exists at all is `set_content`, and it is not a
        preference: the utility world has an EXTENDED principal (a sandbox over
        the window, deliberately, so closed shadow roots are reachable), and
        Gecko requires `document.open()` to run under a principal EQUAL to the
        document's. From the utility world it answers `The operation is
        insecure` EVERY time - measured here on 2026-08-27, and the same defect
        was already fixed once inside the driver, where it shipped a broken
        `set_content` to users.

        ⛔ AND THE MAIN WORLD IS THE ONE WITH THE EMPTY NAME. `auxData.name` is
        absent for the page's own context, which is why the key is `""` and not
        a missing lookup.

        ⛔ SENT ONCE BY DEFAULT. What runs here is the caller's code
        (`page.evaluate`, `evaluate_handle`, `set_content`, a script tag): a
        world that went under it is `ContextDestroyed`, as Playwright raises
        "Execution context was destroyed, most likely because of a navigation".
        From 41.36.0 to 41.37.0 it was made again in the new world, and a script
        waiting on a promise when its page navigated ran twice ([B285]).
        `repeatable=True` is for a library's fixed read that has no effect (the
        MCP's snapshot), which the caller declares: made again in the frame's
        current world, as `call` makes this package's own reads.
        """
        def attempt(deadline):
            return self._evaluate_in(
                self.main_context(frame_id, timeout=deadline.left()),
                expression, by_value=by_value, timeout=deadline.left())
        if repeatable:
            return self.in_current_world(attempt, timeout=timeout)
        return attempt(Deadline(timeout))

    def query_selector_all(self, frame_id: str, selector: str) -> list:
        """Every match, as a list of objectIds the caller must dispose.

        ⛔ IT RETURNS HANDLES, AND EACH ONE HOLDS A DOM NODE ALIVE. A page with
        a thousand matches leaves a thousand nodes uncollectable until they are
        disposed, which is a leak that grows with the page and not with the
        code. The caller owns them; there is no scope here that could.
        """
        count = self.count(frame_id, selector)
        ids = []
        for index in range(count):
            object_id = self.call(
                frame_id,
                "(injected, sel, i) => injected.querySelectorAll("
                "injected.parseSelector(sel), document)[i]",
                selector, index, by_value=False)
            if object_id:
                ids.append(object_id)
        return ids

    def scroll_into_view(self, frame_id: str, element: str) -> bool:
        """Bring the element into the viewport, and say whether it moved.

        ⛔ IT WAS MISSING, AND THAT IS WHY A CLICK BELOW THE FOLD FAILED. The
        retry loop asked whether the element was visible, stable and enabled -
        all true for something 3000 px down the page - then computed its point
        from `getContentQuads`, which answers in main-frame coordinates. The
        event was dispatched at a y outside the viewport, landed on nothing,
        and the hit-target check reported `<html>` several hundred times before
        the deadline. Playwright scrolls as part of actionability; we did not.

        ⛔ `scrollIntoView`, called FROM THE UTILITY WORLD. Scrolling is
        page-visible by construction - a real user scrolls - so there is
        nothing to hide here; what the world buys is that the call goes to the
        NATIVE method through the Xray rather than to whatever the site may
        have replaced it with.

        ⛔ `block: "center"` rather than the default `"start"`: an element
        flush against the top edge is a point that a sticky header covers on a
        great many pages, and the interceptor would then correctly report that
        the event lands elsewhere.
        """
        return bool(self.call(
            frame_id,
            "(injected, el) => {"
            " const before = el.getBoundingClientRect().top;"
            " el.scrollIntoView({block: 'center', inline: 'center',"
            "                    behavior: 'instant'});"
            " return el.getBoundingClientRect().top !== before; }",
            {"objectId": element}))

    def content_origin(self, frame_id: str) -> dict:
        """Where this frame's content area starts, in SCREEN css pixels.

        ⛔ IT COMES FROM THE ENGINE, and that is the whole point. The hit
        point a caller has is in the MAIN frame's space, because that is what
        `Page.getContentQuads` answers and what `dispatchMouseEvent` wants. To
        check it inside a nested frame it has to be expressed in THAT frame's
        space, and the difference between the two origins is exactly the shift.
        Reading it from `mozInnerScreen*` keeps one source: this project already
        owns that value in C++ (`StealthDeclaredContentOrigin`), so a second
        arithmetic here would be the duplicate that rule 16 is about.
        """
        return self.evaluate(
            frame_id,
            "({x: window.mozInnerScreenX, y: window.mozInnerScreenY})")

    def check_hit_target(self, frame_id: str, element: str, point) -> str:
        """Does `point` still belong to `element`? `"done"`, or what is there.

        ⛔ A PURE READ, and it replaced an interceptor. The bundle used to
        install capture listeners on the page's window and validate each event
        as it ARRIVED, blocking the ones that landed elsewhere. That was the
        last thing this package put on the page, and the blocking was itself a
        tell: a real `mousedown` that disappears under `preventDefault` is not
        something an input stack produces.

        The caller runs this BEFORE the action and again AFTER, which turns a
        target that moved into a retry instead of into a blocked event.
        """
        return self.call(
            frame_id,
            "(injected, el, x, y) => injected.checkHitTarget(el, {x, y})",
            {"objectId": element}, float(point[0]), float(point[1]))

    def element_states(self, frame_id: str, element: str,
                       states: list) -> dict:
        """Asks the injected script whether the element is actionable.

        Returns `{"ok": True}` or `{"ok": False, "missing": "<state>"}`.
        The states are Playwright's: `visible`, `stable`, `enabled`,
        `editable`. ⛔ `stable` is ASYNCHRONOUS (it waits two frames), so
        the function is `async` and the value must be awaited: without
        `await` you would get a Promise and every element would come out
        actionable.
        """
        return self.call(
            frame_id,
            "async (injected, el, states) => {"
            "  const r = await injected.checkElementStates(el, states);"
            "  if (r === undefined) return { ok: true };"
            "  if (typeof r === 'string') return { ok: false, missing: r };"
            "  return { ok: false, missing: r.missingState }; }",
            {"objectId": element}, states)

    def read(self, frame_id: str, element: str, what: str, *args) -> Any:
        """One of `_READS` on an element handle."""
        self._check_read(what, args)
        value = self.call(frame_id, _READS[what], {"objectId": element}, *args)
        return self._state_answer(args, value) if what == "state" else value

    def read_match(self, frame_id: str, selector: str, what: str, *args,
                   strict: bool = False,
                   timeout: Optional[float] = 30.0) -> tuple:
        """One of `_READS` on the element `selector` matches NOW:
        `(True, value)`, or `(False, None)` when nothing matches.

        ⛔ FOUND AND READ IN ONE COMMAND, AND THAT IS THE POINT. Resolving to a
        handle and reading through it is two commands with a navigation able
        to fall between them, and then the handle belongs to a world that is
        gone: measured 2026-10-12, a read right after `goto` on a page that
        replaces itself failed that way 5 times in 5. One command carries only
        values, so a world that vanishes under it is not an error at all:
        `call` makes it again in the frame's current world. Waiting for the
        element to appear is the caller's loop, because only the caller knows
        whether it waits (`inner_text`) or answers now (`is_visible`).
        """
        self._check_read(what, args)
        r = self.call(
            frame_id,
            "(injected, sel, strict, ...args) => {"
            "  const el = injected.querySelector("
            "    injected.parseSelector(sel), document, strict);"
            "  if (!el) return { found: false };"
            "  return { found: true, value: (%s)(injected, el, ...args) }; }"
            % _READS[what],
            selector, bool(strict), *args, timeout=timeout)
        if not r or not r.get("found"):
            return False, None
        value = r.get("value")
        return True, (self._state_answer(args, value) if what == "state"
                      else value)

    def read_all(self, frame_id: str, selector: str, what: str, *args,
                 timeout: Optional[float] = 30.0) -> list:
        """One of `_READS` on every element `selector` matches NOW, in
        document order (`locator.all_inner_texts()`, `all_text_contents()`).

        ⛔ A READ OF THIS PACKAGE, NOT THE CALLER'S CODE, and that is why it
        exists. Playwright's client asks for these two through
        `evalOnSelectorAll` with a fixed function, the same door as the
        caller's `$$eval`, and since 41.38.0 that door sends what it carries
        once ([B285]). Found and read in one command of values, as
        `read_match`, it is made again in the frame's current world when the
        page moves on under it: the MCP's `read_text` reads this way right
        after a navigation, on pages that replace themselves ([B282]).
        """
        self._check_read(what, args)
        return self.call(
            frame_id,
            "(injected, sel, ...args) => injected.querySelectorAll("
            "  injected.parseSelector(sel), document)"
            "  .map(el => (%s)(injected, el, ...args))" % _READS[what],
            selector, *args, timeout=timeout) or []

    def _check_read(self, what: str, args: tuple) -> None:
        """A state outside the ones `elementState` knows is refused before
        anything is sent: it would answer a value that means nothing."""
        if what == "state" and args[0] not in self.KNOWN_STATES:
            raise ValueError("unknown state: %r (the real ones are %s)"
                             % (args[0], ", ".join(self.KNOWN_STATES)))

    def text_content(self, frame_id: str, element: str) -> str:
        return self.read(frame_id, element, "textContent")

    # ── the "DOM read" group from item 6 (§6.5), on handles; the texts are
    #    `_READS` ──────────────────────────────────────────────────────────

    #: The states that `injected.elementState` actually knows, read from
    #: its code and not assumed. Asking for one outside this set
    #: silently returns a value that means nothing.
    KNOWN_STATES = ("visible", "hidden", "enabled", "disabled", "editable",
                    "checked", "unchecked", "indeterminate")

    def element_state(self, frame_id: str, element: str, state: str) -> bool:
        """`is_visible`, `is_enabled`, `is_checked` and their siblings."""
        return self.read(frame_id, element, "state", state)

    def _state_answer(self, args: tuple, r: Any) -> bool:
        """What `injected.elementState` answered, as a boolean."""
        state = args[0]
        # ⛔ `elementState` does NOT return a boolean: it returns
        # `{matches, received}`, and on a detached node `received` is
        # `error:notconnected`. Reading it as a boolean would give
        # `True` for a non-empty dict, i.e. ALWAYS.
        if isinstance(r, dict):
            if isinstance(r.get("received"), str) and \
                    r["received"].startswith("error:"):
                raise EvaluationError(
                    "elementState(%s): %s" % (state, r["received"]))
            return bool(r.get("matches"))
        return bool(r)

    def inner_text(self, frame_id: str, element: str) -> str:
        return self.read(frame_id, element, "innerText")

    def inner_html(self, frame_id: str, element: str) -> str:
        return self.read(frame_id, element, "innerHTML")

    def input_value(self, frame_id: str, element: str) -> str:
        return self.read(frame_id, element, "inputValue")

    def get_attribute(self, frame_id: str, element: str, name: str):
        return self.read(frame_id, element, "attribute", name)

    def title(self, frame_id: str) -> str:
        return self.evaluate(frame_id, "document.title")

    def content(self, frame_id: str) -> str:
        """`page.content()`, INCLUDING shadow roots - open and closed.

        ⛔ THIS IS A DELIBERATE DIVERGENCE FROM UPSTREAM PLAYWRIGHT, and it is
        the whole point of owning the client. Upstream serialises with
        `documentElement.outerHTML`, which walks the light DOM only: every
        shadow root, open or closed, comes back as an empty host element. A
        `content()` that silently omits half the document is the defect, not
        the contract - and closed roots are exactly where the interesting parts
        of a hostile page live.

        **HOW, and every piece was measured on the shipped engine on
        2026-08-27:**

        - `Element.getHTML({serializableShadowRoots, shadowRoots})` exists here
          and serialises a root as `<template shadowrootmode="closed">`. It only
          serialises roots it is HANDED, or ones the page marked `serializable`,
          so the list has to be collected first.
        - Collecting them is possible because this runs in the UTILITY world,
          where `element.shadowRoot` answers for a closed root too. That is our
          C++ patch in `Element::GetShadowRootForBindings`, gated on the
          ExpandedPrincipal, so the PAGE still gets `null` and nothing about
          this is observable from content. Measured both ways in the same run.
        - The walk is recursive: a shadow root can contain hosts of its own,
          and stopping at depth one would produce a document that looks
          complete and is not.

        On the probe page: 508 characters against 433 for the `outerHTML` path,
        and the closed root's text is present in the first and absent in the
        second.

        ⛔ NO FALLBACK. If `getHTML` is missing the call raises, and that is
        correct: this package pins its own engine through the seal, so a
        missing `getHTML` means the engine is not the one we pinned, and
        quietly returning a document with holes in it would hide that.
        """
        return self.evaluate(
            frame_id,
            "(() => {"
            "  const roots = [];"
            "  const walk = (root) => {"
            "    for (const el of root.querySelectorAll('*')) {"
            "      const sr = el.shadowRoot;"
            "      if (sr) { roots.push(sr); walk(sr); }"
            "    }"
            "  };"
            "  walk(document);"
            "  const doctype = document.doctype"
            "    ? new XMLSerializer().serializeToString(document.doctype) : '';"
            "  if (!document.documentElement) return doctype;"
            "  return doctype + document.documentElement.getHTML("
            "    {serializableShadowRoots: true, shadowRoots: roots});"
            "})()")

    def bounding_box(self, frame_id: str, element: str):
        """`bounding_box`: x, y, width, height from the quads, or None.

        ⛔ Goes through `Page.getContentQuads` and NOT through the
        page's `getBoundingClientRect`: that getter belongs to the
        site, and reading it would be accountable.
        """
        r = self.c.send("Page.getContentQuads",
                        {"frameId": frame_id, "objectId": element},
                        session=self.session, timeout=None) or {}
        quads = r.get("quads") or []
        if not quads:
            return None
        points = [p for q in quads
                  for p in (q["p1"], q["p2"], q["p3"], q["p4"])]
        x1 = min(p["x"] for p in points)
        y1 = min(p["y"] for p in points)
        x2 = max(p["x"] for p in points)
        y2 = max(p["y"] for p in points)
        return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}

    def frame_of_context(self, context_id: str):
        """Which frame owns this execution context, or None.

        ⛔ The registry is keyed the other way round - (frame, world) to
        context - because that is the direction every other caller needs. This
        is the one caller that arrives holding a context id and nothing else:
        `Page.fileChooserOpened` names the context the input lives in and never
        names the frame, so without this inversion the element could only be
        assumed to be in the main frame, which is wrong the moment a form sits
        in an iframe.
        """
        for (frame_id, _world), value in list(self.contexts.items()):
            if value == context_id:
                return frame_id
        return None

    def json_value_in(self, frame_id: str, world: str, element: str):
        """The value of an objectId, asked in the world it belongs to.

        ⛔ An objectId is resolved inside the context it is given, so a handle
        born in the page's own world cannot be read from the utility one. This
        is the only reader that takes the world as an argument, because
        `waitForFunction` is the only place that hands back a main-world
        handle.
        """
        key = (frame_id, "" if world == "main" else UTILITY_WORLD)
        context = self.contexts.get(key)
        if context is None:
            return None
        r = self._result(self._send_in(
            "Runtime.callFunction",
            {"executionContextId": context,
             "functionDeclaration": "(v) => v",
             "args": [{"objectId": element}],
             "returnByValue": True},
            timeout=None))
        return r.get("value")

    def adopt(self, frame_id: str, element: Optional[str] = None, *,
              into: Optional[str] = None):
        """Move an objectId from the page's own world into the utility world.

        ⛔ AN objectId IS NOT PORTABLE BETWEEN WORLDS. Every other method here
        talks to the utility world, so a handle that arrived from the MAIN
        world - which is where `Page.fileChooserOpened` hands the input element
        from - cannot simply be passed to them: Juggler resolves an objectId
        inside the context it is given and answers that it does not exist.
        `Page.adoptNode` is the crossing, and it is the same one Playwright's
        own Firefox backend makes for this event.

        With no `element` the engine adopts the frame's OWNER - the `<iframe>`
        that holds `frame_id` - and it has to land in the world of the frame
        that element lives in, the parent, which is what `into` names. That is
        `frame.frame_element()`, and upstream's Firefox backend asks for it the
        same way.
        """
        params = {"frameId": frame_id,
                  "executionContextId": self.context_id(into or frame_id)}
        if element is not None:
            params["objectId"] = element
        answer = self._send_in("Page.adoptNode", params, timeout=None) or {}
        return ((answer.get("remoteObject") or {}).get("objectId")) or None

    def dispose(self, frame_id: str, element: str) -> None:
        """A retained objectId keeps a page DOM node alive.

        ⛔ `Runtime.disposeObject` ALSO wants the `executionContextId`:
        an objectId alone identifies nothing, because the same number
        can exist in two contexts.
        """
        try:
            self.c.post("Runtime.disposeObject",
                        {"executionContextId": self.context_id(frame_id),
                         "objectId": element},
                        session=self.session)
        except Exception:
            pass

    # ── the page's own world, for Selenium's execute_script ─────────────────
    #
    # ⛔ Every method below is a crossing into the MAIN world, which is exactly
    # what the rest of this module avoids. They exist because `execute_script`
    # is the caller asking to run code AS THE PAGE, the same reason
    # `evaluate_in_main` exists. What they take care of is that nothing ELSE
    # runs there: the arguments go in as protocol values and adopted nodes,
    # and the result is read back from the privileged side with
    # `Runtime.getObjectProperties`, which walks the object through the
    # Debugger and never calls a getter the page defined. (They lived only in
    # invisible_selenium's and invisible_puppeteer's copies until this module
    # moved into the core.)

    def main_context(self, frame_id: str, *, timeout: Optional[float] = 30.0) -> str:
        """The id of the page's own execution context for a frame.

        ⛔ THE MAIN WORLD IS THE ONE WITH THE EMPTY NAME: `auxData.name` is
        absent for the page's own context, which is why the key is `""`.
        """
        deadline = Deadline(timeout)
        key = (frame_id, "")
        while key not in self.contexts:
            if deadline.passed():
                raise TimeoutError(
                    "the main world never appeared for frame %s. Worlds seen: "
                    "%s" % (frame_id, sorted(n for f, n in self.contexts
                                             if f == frame_id)))
            time.sleep(0.01)
        return self.contexts[key]

    def evaluate_raw(self, context_id: str, expression: str, *,
                     by_value: bool = False,
                     timeout: Optional[float] = 30.0) -> dict:
        """`Runtime.evaluate` in a given context, returning the raw
        RemoteObject, as `call_raw` does for a function. Through `_send_in`
        like every command aimed at a context, so a context that has gone is
        `ContextDestroyed` and not the engine's raw text."""
        return self._result(self._send_in(
            "Runtime.evaluate",
            {"executionContextId": context_id, "expression": expression,
             "returnByValue": by_value},
            timeout=timeout))

    def call_raw(self, context_id: str, declaration: str, args: list, *,
                 by_value: bool = False, timeout: Optional[float] = 30.0) -> dict:
        """`Runtime.callFunction` in a given context, returning the raw
        RemoteObject (`value`, or `objectId` plus `type`/`subtype`).

        ⛔ A returned promise is AWAITED by the engine (`_awaitPromise` in
        Juggler's Runtime.js), which is what W3C `execute_script` specifies.
        """
        return self._result(self._send_in(
            "Runtime.callFunction",
            {"executionContextId": context_id,
             "functionDeclaration": declaration, "args": args,
             "returnByValue": by_value},
            timeout=timeout))

    def properties(self, context_id: str, object_id: str) -> list:
        """The enumerable properties of an object, own and inherited, each as
        `{"name", "value": RemoteObject}`. Read through the Debugger: no getter
        of the page runs, and an accessor comes back without a value."""
        r = self._send_in("Runtime.getObjectProperties",
                          {"executionContextId": context_id,
                           "objectId": object_id},
                          timeout=None) or {}
        return list(r.get("properties") or [])

    def by_value(self, context_id: str, object_id: str):
        """An object's JSON value, serialized by the engine's own sandbox.

        ⛔ `(v) => v` runs in the context the object lives in, but the
        serialization does not: Juggler stringifies in a separate global, so
        the page's `toJSON` overrides on built-ins are not consulted."""
        r = self._result(self._send_in(
            "Runtime.callFunction",
            {"executionContextId": context_id,
             "functionDeclaration": "(v) => v",
             "args": [{"objectId": object_id}], "returnByValue": True},
            timeout=None))
        return r.get("value")

    def node_to_main(self, frame_id: str, element: str) -> Optional[str]:
        """Carry a node from the utility world into the page's own world."""
        answer = self._send_in(
            "Page.adoptNode",
            {"frameId": frame_id,
             "executionContextId": self.main_context(frame_id),
             "objectId": element},
            timeout=None) or {}
        return ((answer.get("remoteObject") or {}).get("objectId")) or None

    def release(self, context_id: str, object_id: str) -> None:
        """`Runtime.disposeObject` in an explicit context. Never raises."""
        try:
            self.c.post("Runtime.disposeObject",
                        {"executionContextId": context_id,
                         "objectId": object_id},
                        session=self.session)
        except Exception:
            pass
