"""The lifecycle: frame tree, navigations, the four load states.

⛔ Most of these tests do NOT launch a browser, and that is deliberate: the
lifecycle is a state machine fed by events, and a state machine is tested by
feeding it events. The defect this file exists to guard against - the states
of the PREVIOUS document satisfying the wait for the next one - reproduces
in three lines here and "almost never" with a real browser.
"""
from __future__ import annotations

import threading
import time

import pytest

from invisible_core.juggler.lifecycle import (
    Lifecycle, NavigationError, IDLE_QUIET)
from invisible_core.juggler.connection import EventListeners


class FakeConnection(EventListeners):
    """The minimum that `Lifecycle` uses: the subscriber registry and a `send`.

    It INHERITS the registry rather than imitating it, so what these tests
    exercise is the same `add_listener`/`dispatch_event` the browser runs."""

    def __init__(self, responses=None):
        super().__init__()
        self.sent = []
        self._responses = responses or {}

    def send(self, method, params=None, session=None, timeout=30):
        self.sent.append((method, params, session))
        return self._responses.get(method)


def lifecycle(responses=None):
    c = FakeConnection(responses)
    return c, Lifecycle(c, "S1")


def events(v, *pairs):
    for method, params in pairs:
        v.c.dispatch_event(method, params, "S1")


# ── the tree ────────────────────────────────────────────────────────────────

def test_the_frame_without_a_parent_is_the_main_one():
    c, v = lifecycle()
    events(v, ("Page.frameAttached", {"frameId": "F1"}))
    assert v.main_frame == "F1"
    assert v.frames["F1"].parent is None


def test_frameDetached_takes_away_the_SUBTREE_not_just_the_node():
    """An orphaned child would be left responding for a frame that no
    longer exists."""
    c, v = lifecycle()
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.frameAttached", {"frameId": "F2", "parentFrameId": "F1"}),
           ("Page.frameAttached", {"frameId": "F3", "parentFrameId": "F2"}),
           ("Page.frameDetached", {"frameId": "F2"}))
    assert set(v.frames) == {"F1"}, v.frame_tree()


def test_the_events_of_ANOTHER_session_do_not_get_in():
    """Two pages open together: without the sessionId, whoever waits for
    a load gets the other tab's."""
    c, v = lifecycle()
    c.dispatch_event("Page.frameAttached", {"frameId": "OTHER"}, "S2")
    assert v.frames == {}


def test_does_not_steal_events_from_whoever_was_already_subscribed():
    """Two subscribers, one event, both served.

    The chain this replaced could silence an observer by forgetting to call
    the next link; a list cannot. What it CAN do is stop calling the rest
    when one of them raises, so the second half of this asserts on that.
    """
    c = FakeConnection()
    seen = []
    c.add_listener(lambda m, p, s: seen.append(m))
    v = Lifecycle(c, "S1")
    c.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")
    assert seen == ["Page.frameAttached"], "the other subscriber went silent"
    assert "F1" in v.frames


def test_a_subscriber_that_raises_does_not_cost_the_others_their_event():
    c = FakeConnection()
    def explodes(m, p, s):
        raise RuntimeError("boom")
    c.add_listener(explodes)
    v = Lifecycle(c, "S1")
    c.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")
    assert "F1" in v.frames, "the raising subscriber took the event with it"
    assert any("boom" in e for e in c.handler_errors),         "the failure was swallowed without a trace"


# ── the states, and the defect that matters ─────────────────────────────────

def test_navigationStarted_resets_the_states():
    c, v = lifecycle()
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "A",
                                         "url": "http://a/", "name": ""}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "B"}))
    assert v.frames["F1"].states == set(), "A's states survived into B"


def test_THE_STATES_OF_ONE_NAVIGATION_DO_NOT_COUNT_FOR_ANOTHER():
    """⛔ THE KNOWN-BAD INPUT OF THIS FILE.

    Reproduces the defect measured on 2026-08-27: `Page.navigate` answers
    with the navigationId BEFORE `navigationStarted` arrives, and in that
    window the frame still carries `commit`/`load` from the previous
    document. With cleanup only on `navigationStarted`, the wait was
    satisfied by those and returned immediately - measured: 0.01s and
    `url=about:blank`.

    Here A's states are present, our navigation is B, and the wait must
    NOT settle for them.
    """
    c, v = lifecycle()
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "A",
                                         "url": "about:blank", "name": ""}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    assert v.frames["F1"].states >= {"commit", "load"}

    with pytest.raises(TimeoutError) as e:
        v.wait_for_state("F1", "commit", navigation="B", timeout=0.3)
    assert "not our" in str(e.value), (
        "the message doesn't say the states belong to another "
        "navigation: %s" % e.value)

    # and as soon as B commits, the wait unblocks
    events(v, ("Page.navigationCommitted",
               {"frameId": "F1", "navigationId": "B",
                "url": "http://b/", "name": ""}))
    v.wait_for_state("F1", "commit", navigation="B", timeout=1.0)
    assert v.frames["F1"].url == "http://b/"


def test_a_navigation_that_REPLACES_ours_after_commit_closes_the_wait():
    """The known-bad input of [B228], measured 2026-09-25.

    A page that replaces itself from script while it loads - `location.replace`,
    a JavaScript challenge, YouTube's `?themeRefresh=1` - starts a new
    navigation after ours committed. Ours never reaches `load` (its document is
    gone), the new one does, and stock Playwright's `goto` returns on it. This
    one used to wait for ours until the timeout: 45 s on a ready page.
    """
    c, v = lifecycle()
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "A"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "A",
                                         "url": "http://a/", "name": ""}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "B"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "B",
                                         "url": "http://a/next", "name": ""}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    v.wait_for_state("F1", "load", navigation="A", timeout=0.5)
    assert v.frames["F1"].url == "http://a/next"


def test_a_navigation_from_BEFORE_ours_does_not_count_even_once_both_are_known():
    """The other side of the same line: numbering the navigations must not turn
    an OLDER one into a successor.

    P is the page we are leaving, A is ours and has started. An event of P's
    that arrives late puts P back as the frame's current navigation, with a
    load. The wait for A must not settle for it: P came BEFORE A, which is the
    2026-08-27 defect in its second form. Accepting any navigation that is
    merely different from ours would pass this, and must not.
    """
    c, v = lifecycle()
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "P"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "A"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "P",
                                         "url": "http://p/", "name": ""}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    assert v.frames["F1"].navigation == "P"
    assert not v.frames["F1"].follows("A")
    with pytest.raises(TimeoutError) as e:
        v.wait_for_state("F1", "load", navigation="A", timeout=0.3)
    assert "not our" in str(e.value), e.value


def test_sameDocumentNavigation_does_NOT_reset_the_states():
    """It's the same document: a history push does not reload the page,
    and treating it as a navigation makes you wait for a load that never
    arrives."""
    c, v = lifecycle()
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "A",
                                         "url": "http://a/", "name": ""}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}),
           ("Page.sameDocumentNavigation",
            {"frameId": "F1", "url": "http://a/#x"}))
    assert "load" in v.frames["F1"].states
    assert v.frames["F1"].url == "http://a/#x"


def test_load_implies_domcontentloaded():
    c, v = lifecycle()
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    assert "domcontentloaded" in v.frames["F1"].states


def test_an_aborted_navigation_RAISES_instead_of_timing_out():
    c, v = lifecycle()
    events(v,
           ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "A"}),
           ("Page.navigationAborted", {"frameId": "F1", "navigationId": "A",
                                       "errorText": "NS_ERROR_UNKNOWN_HOST"}))
    with pytest.raises(NavigationError) as e:
        v.wait_for_state("F1", "load", navigation="A", timeout=5)
    assert "NS_ERROR_UNKNOWN_HOST" in str(e.value)


def test_an_invented_state_is_rejected_immediately():
    c, v = lifecycle()
    with pytest.raises(ValueError) as e:
        v.wait_for_state("F1", "whenIFeelLikeIt", timeout=0.1)
    assert "four" in str(e.value)


# ── networkidle ─────────────────────────────────────────────────────────────

def test_the_inflight_counter_does_not_go_below_zero():
    """A response without its request really does arrive - a load that
    started before we attached - and a negative counter would make
    networkidle unreachable FOREVER."""
    c, v = lifecycle()
    events(v, ("Network.requestFinished", {"requestId": "R"}),
           ("Network.requestFinished", {"requestId": "R2"}))
    assert v.inflight == 0


def test_networkidle_wants_SILENCE_not_just_zero():
    c, v = lifecycle()
    # A loaded document: networkidle implies load (see the class test).
    events(v, ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}),
           ("Network.requestWillBeSent", {"requestId": "R"}),
           ("Network.requestFinished", {"requestId": "R"}))
    assert v.inflight == 0
    # Right after zero, the silence has not matured yet.
    with pytest.raises(TimeoutError):
        v.wait_for_state("F1", "networkidle", timeout=IDLE_QUIET / 2)
    # Waiting for the quiet period, though, it unblocks.
    v.wait_for_state("F1", "networkidle", timeout=IDLE_QUIET * 4)


def test_networkidle_unblocks_by_TIMEOUT_not_by_an_event():
    """⛔ The condition comes true when NOTHING happens. If the wait slept
    until the next event, it would stay stuck in exactly the case it must
    succeed. Here no event arrives after the last one."""
    c, v = lifecycle()
    # A loaded document: networkidle implies load (see the class test).
    events(v, ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}),
           ("Network.requestWillBeSent", {"requestId": "R"}),
           ("Network.requestFinished", {"requestId": "R"}))
    t0 = time.monotonic()
    v.wait_for_state("F1", "networkidle", timeout=5)
    assert time.monotonic() - t0 < 2, "unblocked too late"


# ── goto ────────────────────────────────────────────────────────────────────

def test_a_NULL_navigationId_is_not_an_error():
    """The protocol declares it Nullable: it happens when the navigation
    does not create a new document (an anchor). Waiting for a load there
    would be a timeout on something that succeeded.

    The engine's same-document event is what `goto` waits for instead, and
    here it arrives BEFORE the command's reply, which must still count."""
    c, v = lifecycle({"Page.navigate": {"navigationId": None}})
    events(v, ("Page.frameAttached", {"frameId": "F1"}))
    real_send = c.send

    def send(method, params=None, session=None, timeout=30):
        if method == "Page.navigate":
            events(v, ("Page.sameDocumentNavigation",
                       {"frameId": "F1", "url": "http://a/#x"}))
        return real_send(method, params, session, timeout)
    c.send = send
    result = v.goto("http://a/#x", timeout=1)
    assert result == {"navigationId": None, "url": "http://a/#x"}


def test_goto_without_a_main_frame_SAYS_SO():
    c, v = lifecycle()
    with pytest.raises(RuntimeError) as e:
        v.goto("http://a/")
    assert "main frame" in str(e.value)


def test_goto_networkidle_waits_for_ITS_OWN_document_not_the_quiet_old_one():
    """⛔ The old page's silence must not satisfy the wait for the new one.

    Measured on 2026-10-03: a production single-page login app loaded and went
    quiet, then `goto(<another site>, wait_until="networkidle")` returned at
    once with no Response while the browser was still on the old page, so a
    caller reported a same-document navigation there. `navigationStarted` clears the states, and from then on
    `follows()` accepts the frame, but networkidle read only the GLOBAL
    request counter - zero, and quiet for seconds - so it was reached before
    the new document's request had even been sent.
    """
    c, v = lifecycle({"Page.navigate": {"navigationId": "N1"}})
    events(v, ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "N0"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "N0",
                                         "url": "http://old/"}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    time.sleep(IDLE_QUIET * 1.5)  # the old page has gone quiet

    real_send = c.send

    def send(method, params=None, session=None, timeout=30):
        out = real_send(method, params, session, timeout)
        if method == "Page.navigate":
            # the browser starts ours; its document has not answered yet
            events(v, ("Page.navigationStarted",
                       {"frameId": "F1", "navigationId": "N1"}))
        return out
    c.send = send

    with pytest.raises(TimeoutError) as e:
        v.goto("http://new/", until="networkidle", timeout=IDLE_QUIET * 2)
    assert "networkidle" in str(e.value)

    # once ours commits and loads, and the network is quiet, it is reached
    events(v, ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "N1",
                                            "url": "http://new/"}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    v.wait_for_state("F1", "networkidle", navigation="N1",
                     timeout=IDLE_QUIET * 4)
    assert v.frames["F1"].url == "http://new/"


@pytest.mark.parametrize("anchored", [True, False],
                         ids=["anchored", "unanchored"])
def test_networkidle_is_never_reached_on_a_document_that_has_not_loaded(anchored):
    """The class behind the goto case above: networkidle is the last of the
    four states, so it implies `load` the way `load` implies
    `domcontentloaded`, and ONE definition says so, for every wait.

    With the requirement written into `goto`'s anchored wait only, a second
    definition of networkidle existed beside `_reached`, and an unanchored
    wait during a navigation still read the old page's silence: states
    cleared by `navigationStarted`, counter at zero, quiet for seconds.

    Known-bad: answer networkidle from the request counter alone again, or
    gate it on `load` for one kind of wait only.
    """
    c, v = lifecycle()
    events(v, ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "N0"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "N0",
                                         "url": "http://old/"}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    time.sleep(IDLE_QUIET * 1.5)  # the old page has gone quiet
    events(v, ("Page.navigationStarted", {"frameId": "F1", "navigationId": "N1"}))
    nav = {"navigation": "N1"} if anchored else {}

    with pytest.raises(TimeoutError) as e:
        v.wait_for_state("F1", "networkidle", timeout=IDLE_QUIET * 2, **nav)
    assert "networkidle" in str(e.value)

    events(v, ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "N1",
                                            "url": "http://new/"}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    v.wait_for_state("F1", "networkidle", timeout=IDLE_QUIET * 4, **nav)


def test_networkidle_is_born_once_and_announced_without_anyone_waiting():
    """networkidle is a STATE of the document, born in one place and handed
    to whoever listens, like `load`.

    It used to exist only as a predicate evaluated inside `wait_for_state`, so
    nothing could tell the client: the server never emitted
    `loadstate {"add": "networkidle"}`, and `page.wait_for_load_state(
    "networkidle")` - which waits for exactly that event - timed out on a
    loaded, silent page. Measured on main 2026-10-04: load in 0.0 s,
    networkidle TimeoutError at 5 s.

    Known-bad: answer networkidle from a predicate inside the wait again, so
    nobody hears it unless somebody is waiting.
    """
    c, v = lifecycle()
    heard = []
    v.announce = lambda what, frame_id, url=None: heard.append((what, frame_id))
    events(v, ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationStarted", {"frameId": "F1", "navigationId": "N0"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "N0",
                                         "url": "http://a/"}),
           ("Network.requestWillBeSent", {"requestId": "R"}),
           ("Network.requestFinished", {"requestId": "R"}),
           ("Page.eventFired", {"frameId": "F1", "name": "load"}))
    time.sleep(IDLE_QUIET * 3)
    assert heard == [("networkidle", "F1")], heard
    assert "networkidle" in v.frames["F1"].states

    # A later request does not take it back (Playwright fires it once per
    # document); the next document does.
    events(v, ("Network.requestWillBeSent", {"requestId": "R2"}))
    assert "networkidle" in v.frames["F1"].states
    events(v, ("Page.navigationStarted", {"frameId": "F1", "navigationId": "N1"}))
    assert "networkidle" not in v.frames["F1"].states
    assert heard == [("networkidle", "F1")], heard


def test_a_same_document_goto_answers_after_the_engine_reports_it():
    """Upstream Playwright's `goto`, for a navigation that creates no
    document, waits for the frame's same-document navigation before it
    answers, so the client has the new URL when `goto` returns.

    This one answered the moment `Page.navigate` replied `navigationId: null`,
    before `Page.sameDocumentNavigation` had arrived: `page.url` kept the old
    URL until the NEXT call delivered the event. Measured on main 2026-10-04:
    `goto(url + "#sec")` returned with `page.url == url`, and after a second
    `goto(url + "#other")` it read `url + "#sec"` - one step behind.

    Known-bad: return on a null navigationId without waiting, or announce the
    URL after waking the waiter.
    """
    c, v = lifecycle({"Page.navigate": {"navigationId": None}})
    events(v, ("Page.frameAttached", {"frameId": "F1"}),
           ("Page.navigationCommitted", {"frameId": "F1", "navigationId": "N0",
                                         "url": "http://a/"}))
    order = []
    v.announce = lambda what, frame_id, url=None: order.append((what, url))
    real_send = c.send

    def send(method, params=None, session=None, timeout=30):
        out = real_send(method, params, session, timeout)
        if method == "Page.navigate":
            threading.Timer(0.2, events, (v, ("Page.sameDocumentNavigation", {
                "frameId": "F1", "url": "http://a/#x"}))).start()
        return out
    c.send = send

    result = v.goto("http://a/#x", timeout=5)
    order.append(("returned", result["url"]))
    assert order == [("sameDocument", "http://a/#x"),
                     ("returned", "http://a/#x")], order
