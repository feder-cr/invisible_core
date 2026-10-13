"""The lifecycle: frame tree, navigations, load state.

⛔ THIS IS THE PIECE THAT DECIDES WHETHER THE BREAK FROM PLAYWRIGHT HOLDS UP,
and the reason is how it fails: it does not crash, it "works nineteen times
out of twenty". This is why the comments here say WHY a line is the way it
is, not what it does.

THE CLASSIC DEFECT, and the reason for half of this file: **states must be
kept per NAVIGATION, not per frame.** If kept per frame, a `load` left over
from the PREVIOUS document satisfies the wait for the next one, and
`goto()` returns before the page exists. It is the error that almost never
shows, because it needs the two events to arrive in the wrong order.

THE CORRELATION, which the protocol does NOT hand you. `Page.eventFired`
carries `frameId` and `name` (`load` or `DOMContentLoaded`) and **does not
carry the navigationId**. So membership is established by ORDER: after the
`navigationCommitted` of navigation N, the first `load` of that frame
belongs to N. That is what makes it mandatory to clear the states on
`navigationStarted`.

THE WAITS, and which event closes each:

    commit             Page.navigationCommitted
    domcontentloaded   Page.eventFired name=DOMContentLoaded
    load               Page.eventFired name=load
    networkidle        `load`, then zero inflight requests for IDLE_QUIET
                       seconds (`Lifecycle._settle`, the only place)
    networkidle2       `load`, then at most two inflight requests for
                       IDLE_QUIET seconds: pyppeteer's `networkidle2`, which
                       is Chrome's `networkAlmostIdle` (`_settle` too)

⛔ networkidle IS A STATE LIKE THE OTHERS, kept in `Frame.states` and
cleared with them, and `announce` hands it to the page so the client hears it
too. It used to be a predicate evaluated inside `wait_for_state`, which only a
waiter in THIS process could see: the client's `wait_for_load_state(
"networkidle")` waits for a `loadstate` event that was never sent.

⛔ `sameDocumentNavigation` does NOT clear anything: it is the same
document, and a history push does not reload the page. Treating it as a
navigation means waiting for a `load` that will never arrive.
"""
from __future__ import annotations

import threading
import time
from typing import NamedTuple, Optional

from .connection import Interrupted, TargetClosedError
from .deadline import Deadline

#: How much silence the network needs before it counts as "still". It is
#: Playwright's value: shorter, and a pause between two requests would read
#: as quiet.
IDLE_QUIET = 0.5

STATES = ("commit", "domcontentloaded", "load", "networkidle", "networkidle2")


class NavigationError(RuntimeError):
    """The navigation was aborted by the browser, with its text."""


class NavigationInterrupted(NavigationError):
    """Another document committed in the navigation's place before it did
    (`Frame.outcome`): the browser went elsewhere, and nothing failed to load.
    Its own type so a client words it as what it is, not as an error page."""


class NavigationMark(NamedTuple):
    """Where a frame stood before a command that navigates (`Lifecycle.mark`):
    the navigation then current, and the count of same-document navigations.
    The next navigation of either kind moves one of the two. And how many
    events had ended the page's waits (`Connection.end_waits_when`), so one
    that arrives after the mark ends the wait for that navigation."""

    navigation: Optional[str]
    same_document: int
    interruptions: int = 0


class Frame:
    def __init__(self, frame_id: str, parent: Optional[str]):
        self.id = frame_id
        self.parent = parent
        self.url = ""
        self.name = ""
        #: the current navigation, and ONLY its states
        self.navigation: Optional[str] = None
        self.states: set = set()
        #: the aborted navigations, with their text, for whoever was
        #: waiting on them
        self.aborted: dict = {}
        #: Where in the connection's event stream (`EventListeners.delivered`)
        #: each navigation was first reported started, and every document the
        #: frame has committed, in order, as (place, navigation, url). Kept
        #: across a re-announcement of the frame; read by `outcome`.
        self._started: dict = {}
        self._commits: list = []
        #: how many same-document navigations the browser has reported, so a
        #: `goto` that creates no document can wait for the one it caused.
        self.same_document = 0

    def outcome(self, navigation: str, after: Optional[int] = None):
        """The document that decided `navigation`: `(navigation, url)` of its
        own commit, or of the first OTHER document committed after `after`,
        which replaced it; None while neither has arrived. `after` is the place
        of the reply to the command that started `navigation` (`goto`); without
        it, the place where `navigation` was first reported started.

        ⛔ THREE DEFECTS SIT AROUND THIS RULE, and each earlier fix re-opened
        one of the others.

        Before ours: `Page.navigate` answers with the navigationId before
        `navigationStarted` arrives, and in that window the frame still
        carries the states of the PREVIOUS document. Accepting them made
        `goto(until="commit")` return in 0.01s on `about:blank` (2026-08-27).
        Nothing counts until a document has committed after our command.

        After ours: a page that replaces itself from script while it loads -
        `location.replace`, a JavaScript challenge, YouTube's `?themeRefresh=1`
        - starts a NEW navigation after ours committed, and ours never
        reaches `load`: its document is gone. Requiring the frame's current
        navigation to be ours made every such `goto` time out on a page that
        was ready, 45 s on YouTube and Reddit while stock Playwright answered
        in 2 to 4 s on the same Firefox (2026-09-25, [B228]). Once ours has
        committed, the frame's states count, whichever document carries them.

        Instead of ours: a `goto` sent while the previous page's own
        `location.replace` was in flight. Measured 2026-10-12 on firefox-41,
        2 and 3 rounds in 80 of goto, read, goto on a page that redirects
        50 ms after `load`: ours started, the frame was re-announced from a
        new process, the page's redirect - started BEFORE our command - was
        re-announced and committed, and ours was never heard of again: no
        commit, and no abort either, since `FrameTree.onStateChange` drops
        every `NS_BINDING_ABORTED` of the main frame (a process swap fires
        them spuriously). The rule then numbered navigations by when they
        were first seen and followed a later one; the redirect had been seen
        first, so the wait was for a document that would never come, 45 s in
        the MCP server. Upstream Playwright's `goto` decides on the same
        line, the first document committed after the command's reply: ours,
        or "interrupted by another navigation".
        """
        since = after if after is not None else self._started.get(navigation)
        for place, nav, url in self._commits:
            if nav == navigation or (since is not None and place > since):
                return nav, url
        return None

    @property
    def started_loading(self) -> bool:
        """Whether a navigation has ever started in this frame. A
        `loading=lazy` iframe below the fold never starts one."""
        return bool(self._started)


class Lifecycle:
    """Follows a single page. Hooks into a `Connection`'s events."""

    def __init__(self, connection, session: str):
        self.c = connection
        self.session = session
        self.frames: dict = {}
        self.main_frame: Optional[str] = None
        self.ready = False
        self._inflight = 0
        self._last_activity = time.monotonic()
        #: The last moment more than two requests were in flight, which is
        #: where networkidle2's quiet is measured from.
        self._last_crowded = self._last_activity
        self._cv = threading.Condition()
        #: How many events have ended this page's waits, and the last one,
        #: for the waits that began before it (`Connection.end_waits_when`,
        #: which keeps the rule; `_check_alive` raises it).
        self._interruptions = 0
        self._interruption: Optional[tuple] = None
        #: Who hears what only this object can decide: `announce(what,
        #: frame_id, url=None)`, called UNDER the lock and BEFORE any waiter
        #: is woken, so whatever it sends upward is on its way before the
        #: answer of a wait that the same event satisfies. Set by the page
        #: once its channel exists. `what` is one of `frameAttached` (a frame
        #: id seen for the first time), `frameDetached` (a frame gone, its own
        #: detach or its parent's new document), `networkidle` and
        #: `sameDocument`: the frame tree is this object's, and a client that
        #: keeps frames of its own builds and retires them from these alone.
        self.announce = None
        #: The one pending silence check, or None. See `_settle`.
        self._settle_timer: Optional[threading.Timer] = None
        #: The connection has ended (`_on_close`): no event will come, and a
        #: wait - with a limit or without one - says so instead of waiting.
        self._gone = False
        # ⛔ REGISTERED, NOT CHAINED, and the difference is not style: this
        # used to capture the previous `on_event` and install a closure over
        # it, so every page added a nested call to the delivery of every
        # event. The registry delivers to each subscriber in turn, which is
        # also why nothing here has to remember to pass the event on - the
        # observer that a chain could silence by forgetting one line cannot
        # be silenced by a list.
        connection.add_listener(self._route)
        connection.add_close_listener(self._on_close)

    def _route(self, method: str, params: dict,
               event_session: Optional[str]) -> None:
        if event_session == self.session:
            self._on_event(method, params)

    def detach(self) -> None:
        """Stop listening. Called when the page this follows goes away."""
        self.c.remove_listener(self._route)
        self.c.remove_close_listener(self._on_close)
        with self._cv:
            self.announce = None
            if self._settle_timer is not None:
                self._settle_timer.cancel()
                self._settle_timer = None

    def _on_close(self) -> None:
        with self._cv:
            self._gone = True
            self._cv.notify_all()

    def _check_alive(self, waiting_for: str,
                     began: Optional[int] = None) -> None:
        """Raise if the connection ended: what is awaited can no longer come.
        Called under the lock, after the wait's own condition, so a wait
        already satisfied answers rather than raising.

        And raise `Interrupted` when an event that ends this page's waits
        arrived after the wait began (`began`, the count it read then): the
        dialog that ends Marionette's navigation wait (`navigate.sys.mjs`,
        `onPromptOpened`), for the client that asked for it."""
        if self._gone:
            raise TargetClosedError(
                "the browser closed while waiting for %s" % waiting_for)
        if began is not None and self._interruptions != began:
            raise Interrupted(*self._interruption)

    def frame(self, frame_id: str):
        """The frame with this id, or None.

        ⛔ HERE AND NOT AT THE CALLER, because how frames are kept is this
        object's business. Callers used to reach in with
        `self.lifecycle.frames.get(id)` - two of them - and each one of those
        also asserted that `frames` is a dict keyed by id. `_frame()` below
        CREATES one when it is missing, which is right for an event arriving
        for a frame we never saw attach and wrong for a caller that only wants
        to look: the two readings need two names.
        """
        return self.frames.get(frame_id)

    # ── event intake ────────────────────────────────────────────────────────
    def _on_event(self, method: str, p: dict) -> None:
        with self._cv:
            if self.c.ends_waits(self.session, method, p):
                self._interruptions += 1
                self._interruption = (method, p)
                self._cv.notify_all()
            f = self._apply(method, p)
            self._settle()
            if f is not None or method.startswith("Network."):
                self._cv.notify_all()

    def _settle(self) -> None:
        """Give networkidle to every frame whose document has loaded, once the
        network has been quiet for IDLE_QUIET. Called under the lock.

        ⛔ networkidle IMPLIES `load`, THE WAY `load` IMPLIES
        `domcontentloaded`. The request counter is page-wide and knows nothing
        about documents. With a `beforeunload` listener on the page being
        left, Firefox sends `navigationStarted` BEFORE the new document's
        request: the counter is zero and has been quiet for seconds - the OLD
        page's silence. Measured 2026-10-03 leaving a production single-page
        login app: `goto(..., "networkidle")` returned in 78 ms with no
        Response, still on the old page. `load` is cleared on
        `navigationStarted`, so requiring it measures the quiet on the frame's
        current document.

        ⛔ AND IT IS BORN ON A SILENCE DEADLINE, when NOTHING happens, so no
        event can deliver it: a timer does, one at a time. A check that finds
        activity since it was scheduled schedules the remainder.

        Once given it stays until the next document, as in Playwright: a
        request the page makes later does not take it back.
        """
        now = time.monotonic()
        later = None
        # The two quiets: networkidle counts from the last request event of
        # any kind with nothing in flight, networkidle2 from the last moment
        # more than two were in flight.
        for state, most, since in (("networkidle", 0, self._last_activity),
                                   ("networkidle2", 2, self._last_crowded)):
            waiting = [f for f in self.frames.values()
                       if "load" in f.states and state not in f.states]
            if not waiting or self._inflight > most:
                continue
            quiet_for = now - since
            if quiet_for < IDLE_QUIET:
                left = IDLE_QUIET - quiet_for
                later = left if later is None else min(later, left)
                continue
            for f in waiting:
                f.states.add(state)
                self._tell(state, f.id)
            self._cv.notify_all()
        if later is not None and self._settle_timer is None:
            self._settle_timer = threading.Timer(later, self._settle_later)
            self._settle_timer.daemon = True
            self._settle_timer.start()

    def _settle_later(self) -> None:
        with self._cv:
            self._settle_timer = None
            self._settle()

    def _apply(self, method: str, p: dict):
        if method == "Page.ready":
            self.ready = True
            return None

        if method == "Page.frameAttached":
            # ⛔ A FRAME ID ALREADY KNOWN IS THE SAME FRAME, RE-ANNOUNCED, and
            # it keeps everything it knows. A navigation that moves the frame
            # to another content process brings up a new PageAgent there, which
            # announces the frames it holds (`PageAgent`'s constructor) and
            # re-sends the navigation in flight; the old process says nothing.
            # Measured 2026-10-12 on firefox-41: our own `goto` re-announces
            # the main frame before its commit, and a page that replaces itself
            # from script after `load` re-announces it again. Building a new
            # Frame here threw away the history `Frame.outcome` reads, so
            # a `goto` waiting for a state its document never reached (that
            # document was replaced) waited for the timeout: networkidle 12 in
            # 12 on a local page, domcontentloaded on a loaded host where the
            # events arrive together (invisible_dots, 45 s). Playwright keeps
            # the same object for the same reason ("retain frame identity on
            # cross-process navigation").
            fid = p["frameId"]
            parent = p.get("parentFrameId")
            f = self.frames.get(fid)
            if f is None:
                f = self.frames[fid] = Frame(fid, parent)
                self._tell("frameAttached", fid)
            else:
                f.parent = parent
            if parent is None:
                self.main_frame = fid
            return f

        if method == "Page.frameDetached":
            # The whole subtree is removed, not just the one node: an
            # orphaned child would be left answering for a frame that no
            # longer exists.
            self._retire(p["frameId"], itself=True)
            return None

        if method == "Page.navigationStarted":
            f = self._frame(p["frameId"])
            f.navigation = p["navigationId"]
            f._started.setdefault(f.navigation, self.c.delivered)
            # ⛔ THIS is where the correctness of the whole file lives: the
            # states of the previous document do NOT count for this one.
            f.states = set()
            return f

        if method == "Page.navigationCommitted":
            # ⛔ A NEW DOCUMENT HAS NONE OF THE OLD ONE'S FRAMES. The engine does
            # not say so when the navigation moved the frame to another process
            # (the old process is simply left), nor after a back-forward cache
            # restore: measured 2026-10-12 on firefox-41, an iframe stayed here
            # for the rest of the session after its parent navigated away, and
            # the client's `page.frames` grew by the stale child and a repeated
            # main frame at every such navigation. The children a frame has when
            # it commits belong to the document being replaced; the new
            # document's attach after. Playwright retires them at the same
            # moment (`frameCommittedNewDocumentNavigation`).
            self._retire(p["frameId"], itself=False)
            f = self._frame(p["frameId"])
            nav = p.get("navigationId")
            f.url = p.get("url", f.url)
            # The frame's name (the iframe's `name`, or its `id`), which the
            # engine sends with every commit and pyppeteer's `frame.name` reads.
            f.name = p.get("name", f.name)
            # Without an id it is a document already committed, re-announced
            # by a new PageAgent (its constructor), not a new one.
            if nav is not None:
                f.navigation = nav
                f._commits.append((self.c.delivered, nav, f.url))
            f.states.add("commit")
            return f

        if method == "Page.navigationAborted":
            f = self._frame(p["frameId"])
            f.aborted[p["navigationId"]] = p.get("errorText", "aborted")
            return f

        if method == "Page.sameDocumentNavigation":
            f = self._frame(p["frameId"])
            f.url = p.get("url", f.url)
            f.same_document += 1
            # ⛔ No clearing here: it is the same document.
            # Announced HERE, under the lock and before `_on_event` wakes a
            # waiter, so the page's `navigated` is on its way up before the
            # answer of a `goto` waiting for this very event.
            self._tell("sameDocument", f.id, f.url)
            return f

        if method == "Page.eventFired":
            f = self._frame(p["frameId"])
            name = p.get("name")
            if name == "load":
                f.states.add("load")
                # `load` implies `domcontentloaded`: if an unexpected event
                # order meant the second one never arrived, whoever is
                # waiting for it would be stuck in front of a page that is
                # already loaded.
                f.states.add("domcontentloaded")
            elif name == "DOMContentLoaded":
                f.states.add("domcontentloaded")
            return f

        if method == "Network.requestWillBeSent":
            self._inflight += 1
            self._last_activity = time.monotonic()
            if self._inflight > 2:
                self._last_crowded = self._last_activity
        elif method in ("Network.requestFinished", "Network.requestFailed"):
            if self._inflight > 2:
                self._last_crowded = time.monotonic()
            # It never drops below zero: a response without its request
            # does arrive for real, for instance for a load that started
            # before we hooked in, and a negative counter would make
            # `networkidle` unreachable forever.
            self._inflight = max(0, self._inflight - 1)
            self._last_activity = time.monotonic()
        return None

    def _tell(self, what: str, frame_id: str, url: Optional[str] = None) -> None:
        """Hand `what` to whoever listens (`announce`), under the lock and
        before any waiter is woken."""
        if self.announce is not None:
            self.announce(what, frame_id, url)

    def _retire(self, frame_id: str, *, itself: bool) -> None:
        """Remove `frame_id`'s descendants, and the frame too if `itself`,
        telling each one, the deepest first, the way a tree comes down."""
        gone = [k for k in self.frames
                if self._descendants(k, frame_id) or (itself and k == frame_id)]
        gone.sort(key=lambda k: -self._depth(k))
        for k in gone:
            self.frames.pop(k, None)
        for k in gone:
            self._tell("frameDetached", k)

    def _depth(self, fid: str) -> int:
        depth, seen, cur = 0, set(), self.frames.get(fid)
        while cur and cur.parent and cur.parent not in seen:
            seen.add(cur.parent)
            depth += 1
            cur = self.frames.get(cur.parent)
        return depth

    def _descendants(self, fid: str, ancestor: str) -> bool:
        seen = set()
        cur = self.frames.get(fid)
        while cur and cur.parent and cur.parent not in seen:
            if cur.parent == ancestor:
                return True
            seen.add(cur.parent)
            cur = self.frames.get(cur.parent)
        return False

    def _frame(self, fid: str) -> Frame:
        # An event can name a frame we never saw attach (we hooked in on an
        # already-live page). It gets created instead of being lost.
        if fid not in self.frames:
            self.frames[fid] = Frame(fid, None)
            if self.main_frame is None:
                self.main_frame = fid
        return self.frames[fid]

    # ── waiting ─────────────────────────────────────────────────────────────
    def _reached(self, f: Frame, state: str) -> bool:
        # One rule for the four: networkidle is born in `_settle`, the
        # others at event intake, and all of them live in `f.states`.
        return state in f.states

    def _children_reached(self, f: Frame, state: str) -> bool:
        """`state` on every descendant of `f` that has started loading:
        pyppeteer's `_checkLifecycle`, with puppeteer's later rule that a
        frame which never started (a lazy iframe out of view) is not waited
        for, since it never would be."""
        for c in self.frames.values():
            if c.parent == f.id and c.started_loading and not (
                    self._reached(c, state) and self._children_reached(c, state)):
                return False
        return True

    def mark(self, frame_id: str) -> "NavigationMark":
        """Where `frame_id` stands now: its current navigation, and how many
        same-document navigations it has had. Taken BEFORE a command that
        navigates, it is what `wait_for_new_navigation` waits to move; taken
        after, it could already include the navigation it is meant to see."""
        with self._cv:
            f = self.frames.get(frame_id)
            if f is None:
                return NavigationMark(None, 0, self._interruptions)
            return NavigationMark(f.navigation, f.same_document,
                                  self._interruptions)

    def wait_for_new_navigation(self, frame_id: str, since: "NavigationMark",
                                state: Optional[str] = "load",
                                timeout: Optional[float] = 30.0, *,
                                replaced: str = "raise",
                                children: bool = False) -> Optional[str]:
        """Wait for the frame's next navigation after the mark `since`, and
        answer with the id of the new document's navigation, or None when the
        next navigation stayed in the same document. `replaced` and `children`
        as in `wait_for_state`; with `replaced="follow"` the answer is the
        document the wait ended on.

        ⛔ A SAME-DOCUMENT NAVIGATION ENDS THE WAIT AT ONCE (B290). History has
        same-document entries - a hash, a `pushState` - and going to one starts
        no navigation: there is no id and no document to load. Until 41.38.0
        this waited only for a navigation id, so `go_back` to a hash entry
        waited out its whole timeout and raised, in all three clients, where
        Playwright and pyppeteer answer null at once (their navigation watchers
        complete on `navigatedWithinDocument`) and Marionette ends its wait on
        `hashchange` and `popstate`. The frame counts those navigations
        (`Page.sameDocumentNavigation`), so the mark carries the count.

        With `state` None the answer comes when the new document's navigation
        has started, which is what Selenium's "none" strategy waits for.

        ⛔ THE ID IS RETURNED BECAUSE ONLY THIS FUNCTION KNOWS IT. The caller
        needs it to answer `reload` / `go_back` with a Response, and reading
        the frame's current navigation afterwards would be a different value
        the moment the page navigates on its own. It is computed here anyway.

        ⛔ THIS IS THE HISTORY CASE, AND WITHOUT IT `go_back` IS A NO-OP THAT
        REPORTS SUCCESS. `Page.goBack` answers `{success: true}` the moment the
        browser accepts the request, not when the new document is loaded. Wait
        for `load` at that instant and it is already set - by the document you
        are navigating AWAY from - so the wait returns immediately and the next
        read sees the old page.

        Measured on 2026-08-27: `go_back()` from the second page, then
        `title()`, answered "second". The states were right, the document was
        not, and nothing raised.

        `goto` does not need this because `Page.navigate` hands back the
        navigationId to wait on. History gives no id, so the only thing to
        anchor on is that the frame's CURRENT navigation has changed.
        """
        deadline = Deadline(timeout)
        with self._cv:
            while True:
                f = self.frames.get(frame_id)
                if f is not None and f.same_document != since.same_document:
                    return None
                if f is not None and f.navigation != since.navigation:
                    navigation = f.navigation
                    break
                self._check_alive("the next navigation of frame %s" % frame_id,
                                  since.interruptions)
                if deadline.passed():
                    raise TimeoutError(
                        "no new navigation on frame %s in %.0fs: the previous "
                        "one (%s) is still current"
                        % (frame_id, timeout, since.navigation))
                self._cv.wait(deadline.slice(0.05))
        if state is not None:
            landed = self.wait_for_state(frame_id, state, navigation=navigation,
                                         timeout=deadline.left(floor=0.05),
                                         began=since.interruptions,
                                         replaced=replaced, children=children)
            if replaced == "follow":
                return landed
        return navigation

    def wait_for_navigation_start(self, frame_id: str, previous: Optional[str],
                                  timeout: Optional[float], *,
                                  began: Optional[int] = None) -> Optional[str]:
        """The id of a navigation `frame_id` starts that is not `previous`, or
        None if none starts within `timeout`.

        The question a client asks after an action that MAY navigate: Selenium
        waits for the page a click unloads into and only if the click unloads
        one (Marionette, `clickElement`), so "did one start" is answered with
        no error either way. `wait_for_new_navigation` asks it too, and turns
        the None into its timeout. `began`: as in `wait_for_state`."""
        deadline = Deadline(timeout)
        with self._cv:
            if began is None:
                began = self._interruptions
            while True:
                f = self.frames.get(frame_id)
                if f is not None and f.navigation != previous:
                    return f.navigation
                self._check_alive("a new navigation of frame %s" % frame_id,
                                  began)
                if deadline.passed():
                    return None
                self._cv.wait(deadline.slice(0.05))

    def wait_for_main_frame(self, timeout: Optional[float] = 20.0) -> str:
        """The main frame id, waiting for it to arrive.

        ⛔ IT ARRIVES AS AN EVENT, so reading `main_frame` right after attaching
        to a page is a race: it is set by `Page.frameAttached`, which the
        browser sends when it is ready and not when we ask. On a free machine
        the attribute is usually already there, which is exactly what makes
        this the kind of race that ships - it fails under load, on somebody
        else's machine, once in twenty runs.
        """
        deadline = Deadline(timeout)
        with self._cv:
            while self.main_frame is None:
                self._check_alive("the main frame")
                if deadline.passed():
                    raise TimeoutError(
                        "no main frame in %.0fs: the page never announced one. "
                        "Frames seen: %s" % (timeout, sorted(self.frames)))
                self._cv.wait(deadline.slice(0.05))
            return self.main_frame

    def wait_for_state(self, frame_id: str, state: str, *,
                        navigation: Optional[str] = None,
                        timeout: Optional[float] = 30.0,
                        after: Optional[int] = None,
                        url: Optional[str] = None,
                        began: Optional[int] = None,
                        replaced: str = "raise",
                        children: bool = False) -> Optional[str]:
        """Wait until `frame_id` reaches `state`, and answer with the
        navigation of the document it was reached on. Given a `navigation`,
        only once its document has committed, and from then on on whichever
        document the frame carries; replaced by another before it committed,
        it is `NavigationInterrupted` at once (`Frame.outcome`, which says what
        `after` is), in upstream Playwright's words, naming `url`, the address
        asked for, when the caller has one.

        `replaced="follow"` waits on the replacing document instead, as
        pyppeteer's `NavigatorWatcher` does: it completes on whatever document
        the frame carries once one has committed after the wait began, and
        never says interrupted. `children=True` wants `state` on every
        descendant frame that has started loading too, its `_checkLifecycle`
        (`Lifecycle._children_reached`). Both are the defaults' opposite only
        for the client that asks.

        `began`: the count of events that ended this page's waits, read
        before the command whose outcome this waits for (a `NavigationMark`
        carries it); an event after it ends the wait with `Interrupted`.
        Not given, it is read now."""
        if state not in STATES:
            raise ValueError("unknown state: %r (the states are %s)"
                              % (state, ", ".join(STATES)))
        if replaced not in ("raise", "follow"):
            raise ValueError("replaced is 'raise' or 'follow', not %r"
                             % (replaced,))
        deadline = Deadline(timeout)
        with self._cv:
            if began is None:
                began = self._interruptions
            while True:
                f = self.frames.get(frame_id)
                decided = None
                if f is not None:
                    if navigation and navigation in f.aborted:
                        raise NavigationError(f.aborted[navigation])
                    if navigation is not None:
                        decided = f.outcome(navigation, after)
                        if (decided is not None and decided[0] != navigation
                                and replaced == "raise"):
                            raise NavigationInterrupted(
                                'Navigation %s is interrupted by another '
                                'navigation to "%s"'
                                % ('to "%s"' % url if url else navigation,
                                   decided[1]))
                    # ⛔ CLEARING THE STATES ON `navigationStarted` IS NOT
                    # ENOUGH, and this is the real defect measured on
                    # 2026-08-27.
                    #
                    # `Page.navigate` answers with the navigationId BEFORE
                    # `navigationStarted` has arrived. In that window the
                    # frame still carries the states of the previous
                    # document - `about:blank` already has commit,
                    # domcontentloaded and load - and whoever is waiting
                    # for `commit` finds it right away and returns.
                    # Measured: the first `goto(until="commit")` returned
                    # in 0.01s with `url=about:blank`, i.e. before it had
                    # even started.
                    #
                    # The fix is not to wait a moment: it is to require
                    # that OUR document has committed before any state
                    # counts (`Frame.outcome` says why the states of a later
                    # document count too). A document from BEFORE ours is
                    # not ours, no matter what it says.
                    if ((navigation is None or decided is not None)
                            and self._reached(f, state)
                            and (not children
                                 or self._children_reached(f, state))):
                        return f.navigation
                self._check_alive("%s on frame %s" % (state, frame_id), began)
                if deadline.passed():
                    if f is None:
                        reason = "the frame does not exist"
                    elif navigation is not None and decided is None:
                        # The message must say THIS, because it is the
                        # case where the states are there but are not
                        # ours, and without this line it would look like
                        # the browser is not responding.
                        reason = ("the current navigation is %s, not our "
                                  "%s, which has not committed"
                                  % (f.navigation, navigation))
                    else:
                        reason = ("states reached: %s"
                                  % (sorted(f.states) or "none"))
                    raise TimeoutError(
                        "%s not reached in %.0fs (%s, inflight requests: "
                        "%d)" % (state, timeout, reason, self._inflight))
                # networkidle comes true on a SILENCE DEADLINE, when nothing
                # happens; `_settle`'s timer notifies then, so every state
                # can sleep until the next notify.
                self._cv.wait(deadline.left())

    # ── navigation ──────────────────────────────────────────────────────────
    def goto(self, url: str, *, frame_id: Optional[str] = None,
              until: Optional[str] = "load", timeout: Optional[float] = 30.0,
              referer: Optional[str] = None, replaced: str = "raise",
              children: bool = False) -> dict:
        """Navigate `frame_id` (the main frame when not given) to `url` and
        wait for `until` on its document, one of the `STATES`. `replaced` and
        `children` as in `wait_for_state`; the answer's `landed` is the
        navigation of the document the wait ended on, `navigationId` the one
        the command started.

        `until=None` waits for no state: it returns once the engine has
        started the navigation, its reply to `Page.navigate`. That is
        WebDriver's "none" page load strategy (Marionette returns once the
        navigation has started, `navigate.sys.mjs`), which a client offers and
        Playwright does not. A same-document navigation is still waited for:
        it has no document to wait on, and the engine reports it at once.
        """
        if until is not None and until not in STATES:
            raise ValueError("unknown state: %r (the states are %s)"
                              % (until, ", ".join(STATES)))
        fid = frame_id or self.main_frame
        if fid is None:
            raise RuntimeError("no frame: the page has not announced its "
                                "main frame yet")
        params = {"frameId": fid, "url": url}
        if referer:
            params["referer"] = referer
        deadline = Deadline(timeout)
        # Counted BEFORE the command: the engine may report the same-document
        # navigation before its reply arrives, and a dialog that opens while
        # the command is on its way ends the wait.
        with self._cv:
            before = self._frame(fid).same_document
            began = self._interruptions
        # The reply's place in the event stream is the line `Frame.outcome`
        # decides on.
        result, after = self.c.send_placed("Page.navigate", params,
                                           session=self.session,
                                           timeout=timeout)
        nav = (result or {}).get("navigationId")

        # ⛔ A NULL `navigationId` is not an error: the protocol declares
        # it `Nullable`, and Juggler answers it for a url that differs from
        # the current one only by its fragment. There is no `load` to wait
        # for, and waiting for one would be a timeout on something that
        # succeeded.
        #
        # ⛔ BUT THE ANSWER WAITS FOR THE SAME-DOCUMENT NAVIGATION, as
        # upstream's `goto` does. Answering on the reply put it ahead of
        # `Page.sameDocumentNavigation`, and the client read the OLD url
        # after `goto` returned, until its next call delivered the event
        # (measured 2026-10-04, one step behind on every fragment goto).
        if nav is None:
            with self._cv:
                while self._frame(fid).same_document == before:
                    self._check_alive("the same-document navigation to %s" % url,
                                      began)
                    if deadline.passed():
                        raise TimeoutError(
                            "the same-document navigation to %s was not "
                            "reported in %.0fs" % (url, timeout))
                    self._cv.wait(deadline.left())
                return {"navigationId": None, "landed": None,
                        "url": self.frames[fid].url}

        landed = nav
        if until is not None:
            landed = self.wait_for_state(fid, until, navigation=nav,
                                         timeout=timeout, after=after, url=url,
                                         began=began, replaced=replaced,
                                         children=children)
        return {"navigationId": nav, "landed": landed,
                "url": self.frames[fid].url}

    # ── inspection ──────────────────────────────────────────────────────────
    def frame_tree(self) -> dict:
        return {fid: {"parent": f.parent, "url": f.url,
                       "states": sorted(f.states),
                       "navigation": f.navigation}
                for fid, f in self.frames.items()}

    @property
    def inflight(self) -> int:
        return self._inflight
