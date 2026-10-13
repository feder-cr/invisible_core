"""A `goto` that answers success has landed on the document its navigation
went to, or it does not answer success ([B302]).

THE KNOWN-BAD INPUT, from firefox-45 (`C:/tmp/b230/docopen_goto_probe.py`): the
page being left rewrites itself with document.open() while our goto is on its
way. The engine dropped the abort that document.open() causes, and took the
document.write() that follows for our navigation's commit, with the OLD page's
address:

    REPLY               nav-N   ours
    requestWillBeSent   nav-N   http://127.0.0.1/new?i=N   (sent, never landed)
    navigationStarted   nav-N
    navigationCommitted nav-N   http://127.0.0.1/old?i=N-1  <- the old document
    eventFired          DOMContentLoaded

`goto` answered OK, 7 times in 40, with the page still on the old address:
for an agent, the worst kind of answer, since it acts on a page it is not on.
The engine is fixed in firefox-46; this is the client's own check that a
commit attributed to its navigation is where that navigation's request went,
so that the next defect of the kind fails loudly instead of answering OK.
"""
from __future__ import annotations

import threading

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import LandedElsewhere, Lifecycle, NavigationError

pytestmark = pytest.mark.unit

S = "S1"
F = "mainframe-11"
OURS = "nav-7"
OLD = "http://127.0.0.1/old?i=6"
NEW = "http://127.0.0.1/new?i=7"


class _Engine(EventListeners):
    """Answers `Page.navigate` with OURS, then sends `after` from another
    thread, the way the browser does."""

    def __init__(self, after=()):
        super().__init__()
        self.after = after

    def emit(self, events):
        for method, params in events:
            self.dispatch_event(method, params, S)

    def send(self, method, params=None, session=None, timeout=None):
        assert method == "Page.navigate", method
        threading.Timer(0.05, self.emit, (self.after,)).start()
        return {"navigationId": OURS}


def _request(nav, url, rid="r1", redirected_from=None):
    p = {"requestId": rid, "url": url, "navigationId": nav, "frameId": F,
         "method": "GET", "headers": [], "isIntercepted": False,
         "cause": "TYPE_DOCUMENT", "internalCause": "TYPE_DOCUMENT"}
    if redirected_from:
        p["redirectedFrom"] = redirected_from
    return ("Network.requestWillBeSent", p)


def _started(nav):
    return ("Page.navigationStarted", {"frameId": F, "navigationId": nav})


def _committed(nav, url):
    return ("Page.navigationCommitted", {"frameId": F, "navigationId": nav, "url": url})


_DCL = ("Page.eventFired", {"frameId": F, "name": "DOMContentLoaded"})


def _goto(after, url=NEW):
    engine = _Engine(after=after)
    life = Lifecycle(engine, S)
    engine.emit([("Page.frameAttached", {"frameId": F}), _started("nav-6"),
                 _committed("nav-6", OLD), _DCL])
    return life.goto(url, frame_id=F, until="domcontentloaded", timeout=5)


def test_a_commit_of_ours_on_another_address_is_not_a_success():
    with pytest.raises(LandedElsewhere) as e:
        _goto([_request(OURS, NEW), _started(OURS), _committed(OURS, OLD), _DCL])
    # A navigation error, so every client already has a word for it.
    assert isinstance(e.value, NavigationError)
    assert OLD in str(e.value) and NEW in str(e.value), str(e.value)


def test_a_commit_where_the_request_went_is_a_success():
    out = _goto([_request(OURS, NEW), _started(OURS), _committed(OURS, NEW), _DCL])
    assert out["landed"] == OURS and out["url"] == NEW


def test_the_end_of_a_redirect_chain_is_where_the_request_went():
    final = "http://127.0.0.1/final"
    out = _goto([_request(OURS, NEW, "r1"),
                 _request(OURS, final, "r2", redirected_from="r1"),
                 _started(OURS), _committed(OURS, final), _DCL])
    assert out["url"] == final


def test_the_fragment_is_not_sent_and_does_not_count():
    out = _goto([_request(OURS, NEW), _started(OURS), _committed(OURS, NEW + "#top"), _DCL],
                url=NEW + "#top")
    assert out["url"] == NEW + "#top"


def test_a_navigation_with_no_request_is_not_judged():
    """data:, about:blank and the like go to no network: nothing to compare,
    and nothing to refuse."""
    data = "data:text/html,<p>x</p>"
    out = _goto([_started(OURS), _committed(OURS, data), _DCL], url=data)
    assert out["url"] == data


def test_another_navigation_s_request_does_not_count():
    out = _goto([_request("nav-99", OLD), _request(OURS, NEW, "r2"), _started(OURS),
                 _committed(OURS, NEW), _DCL])
    assert out["url"] == NEW


def test_an_aborted_navigation_names_itself():
    """A client that waits on through a stop (Selenium, as Marionette does)
    needs the id of the navigation that was stopped: the page's own
    navigation may already have started by the time the abort arrives."""
    with pytest.raises(NavigationError) as e:
        _goto([_request(OURS, NEW), _started(OURS),
               ("Page.navigationAborted", {"frameId": F, "navigationId": OURS,
                                           "errorText": "NS_BINDING_ABORTED"})])
    assert str(e.value) == "NS_BINDING_ABORTED"
    assert e.value.navigation == OURS


class _TwoGotos(EventListeners):
    """Answers each `Page.navigate` with the next id, and runs `script` from
    another thread once the second has been answered."""

    def __init__(self, script):
        super().__init__()
        self.ids = iter(["nav-A", "nav-B"])
        self.script = script
        self.answered = 0

    def emit(self, events):
        for method, params in events:
            self.dispatch_event(method, params, S)

    def send(self, method, params=None, session=None, timeout=None):
        assert method == "Page.navigate", method
        self.answered += 1
        if self.answered == 2:
            threading.Timer(0.05, self.emit, (self.script,)).start()
        return {"navigationId": next(self.ids)}


def test_a_goto_another_goto_stops_is_interrupted_by_it():
    """Upstream Playwright on its Firefox (1.63, `C:/tmp/b230/second_goto_probe.py
    upstream`): a goto the caller starts while another is on its way stops
    the first, which raises "is interrupted by another navigation to" the
    second's address, 3 times in 3; the engine reports the first stopped, so
    the client names who stopped it."""
    first_url, second_url = "http://127.0.0.1/held?first=0", "http://127.0.0.1/second"
    engine = _TwoGotos([_started("nav-A"),
                        ("Page.navigationAborted", {"frameId": F, "navigationId": "nav-A",
                                                    "errorText": "NS_BINDING_ABORTED"}),
                        _started("nav-B"), _committed("nav-B", second_url), _DCL])
    life = Lifecycle(engine, S)
    engine.emit([("Page.frameAttached", {"frameId": F}), _started("nav-6"),
                 _committed("nav-6", OLD), _DCL])
    first = {}

    def go_first():
        try:
            life.goto(first_url, frame_id=F, until="domcontentloaded", timeout=5)
        except Exception as e:
            first["error"] = e

    t = threading.Thread(target=go_first)
    t.start()
    import time as _time
    _time.sleep(0.1)
    out = life.goto(second_url, frame_id=F, until="domcontentloaded", timeout=5)
    t.join(5)
    assert out["url"] == second_url
    from invisible_core.juggler.lifecycle import NavigationInterrupted
    assert isinstance(first.get("error"), NavigationInterrupted), first
    assert str(first["error"]) == ('Navigation to "%s" is interrupted by another '
                                   'navigation to "%s"' % (first_url, second_url))


def _goto_following(after):
    engine = _Engine(after=after)
    life = Lifecycle(engine, S)
    engine.emit([("Page.frameAttached", {"frameId": F}), _started("nav-6"),
                 _committed("nav-6", OLD), _DCL])
    return life.goto(NEW, frame_id=F, until="domcontentloaded", timeout=5,
                     replaced="follow")


_ABORT = ("Page.navigationAborted", {"frameId": F, "navigationId": OURS,
                                     "errorText": "NS_BINDING_ABORTED"})


def test_following_a_navigation_the_page_stopped_by_navigating_waits_on_its_navigation():
    """pyppeteer's NavigatorWatcher never says interrupted: a document that
    took the frame in place of the one asked for is waited on in its place.
    From firefox-46 the engine reports the stop the page's own navigation
    causes ([B302]), after that navigation has started (the engine reports a
    stop at the end of its task); following, that is the document to wait on,
    not an error."""
    landed = "http://127.0.0.1/landed"
    out = _goto_following([_request(OURS, NEW), _started(OURS), _started("nav-8"),
                           _ABORT, _committed("nav-8", landed), _DCL])
    assert out["landed"] == "nav-8" and out["url"] == landed


def test_following_a_navigation_stopped_with_nothing_in_its_place_raises():
    """document.open(), window.stop(): nothing takes the frame, and waiting on
    would be the timeout."""
    with pytest.raises(NavigationError) as e:
        _goto_following([_request(OURS, NEW), _started(OURS), _ABORT])
    assert str(e.value) == "NS_BINDING_ABORTED"


def test_an_interrupted_navigation_names_itself():
    """The same for a navigation another document replaced: a client that
    waits on, as Marionette does, waits for the document after it."""
    from invisible_core.juggler.lifecycle import NavigationInterrupted
    with pytest.raises(NavigationInterrupted) as e:
        _goto([_request(OURS, NEW), _started(OURS), _started("nav-8"),
               _committed("nav-8", "http://127.0.0.1/landed"), _DCL])
    assert e.value.navigation == OURS


def test_a_load_before_our_commit_is_not_our_load():
    """C7 of the event-filter audit: the states were cleared when a navigation
    STARTED, so an event of the document being left that arrived between our
    start and our commit - its `load`, when our navigation stops its loads -
    counted for ours, and goto(until="load") could answer at our commit,
    before our document loaded. Not seen on firefox-46
    (`C:/tmp/b230/c7_load_probe.py`), but nothing in the client stopped it;
    Playwright clears the lifecycle at the commit, and so does this."""
    engine = _Engine(after=[_request(OURS, NEW), _started(OURS),
                            ("Page.eventFired", {"frameId": F, "name": "load"}),
                            _committed(OURS, NEW)])
    life = Lifecycle(engine, S)
    engine.emit([("Page.frameAttached", {"frameId": F}), _started("nav-6"),
                 _committed("nav-6", OLD), _DCL])
    with pytest.raises(TimeoutError):
        life.goto(NEW, frame_id=F, until="load", timeout=0.6)


def test_our_own_load_after_our_commit_still_counts():
    out = _goto([_request(OURS, NEW), _started(OURS), _committed(OURS, NEW), _DCL,
                 ("Page.eventFired", {"frameId": F, "name": "load"})])
    assert out["landed"] == OURS
