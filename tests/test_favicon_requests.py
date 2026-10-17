"""Which requests are the favicon (`invisible_core.juggler.favicon`).

The rule is Playwright's: a URL ending in `/favicon.ico`, or a request a
favicon request redirected to. These cases pin it, the redirect in the order
the engine really sends it, and the memory's lifetime.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.favicon import FaviconRequests, is_favicon_url

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("url, expected", [
    ("http://127.0.0.1/favicon.ico", True),
    ("https://example.test/a/b/favicon.ico", True),
    ("http://127.0.0.1/favicon.ico#x", True),
    ("http://127.0.0.1/favicon.ico?v=2", False),    # upstream's test, as written
    ("http://127.0.0.1/favicon.ico.png", False),
    ("http://127.0.0.1/myfavicon.ico", False),
    ("http://127.0.0.1/", False),
])
def test_the_url_rule_is_upstreams(url, expected):
    assert is_favicon_url(url) is expected


def _sent(rid, url, redirected_from=None):
    params = {"requestId": rid, "url": url}
    if redirected_from:
        params["redirectedFrom"] = redirected_from
    return ("Network.requestWillBeSent", params)


def test_every_event_of_a_favicon_request_is_known_by_its_id():
    f = FaviconRequests()
    assert f.see(*_sent("FAV", "http://h/favicon.ico"))
    for method in ("Network.responseReceived", "Network.requestFinished",
                   "Network.requestFailed"):
        assert f.see(method, {"requestId": "FAV"}), method
    assert not f.see("Network.responseReceived", {"requestId": "OTHER"})


def test_a_hop_redirected_from_a_favicon_is_one_even_after_its_predecessor_finished():
    """The engine sends the previous hop's `requestFinished` BEFORE the next
    hop's `requestWillBeSent`: a memory emptied at finish would lose the chain."""
    f = FaviconRequests()
    f.see(*_sent("FAV", "http://h/favicon.ico"))
    f.see("Network.requestFinished", {"requestId": "FAV"})
    assert f.see(*_sent("FAV-redirect1", "http://cdn/icon.png", "FAV"))
    assert f.see(*_sent("FAV-redirect2", "http://cdn2/icon.png", "FAV-redirect1"))


def test_a_page_request_redirected_to_the_favicon_is_a_favicon_from_that_hop():
    f = FaviconRequests()
    assert not f.see(*_sent("R", "http://h/old-icon"))
    assert f.see(*_sent("R-redirect1", "http://h/favicon.ico", "R"))
    assert not f.see("Network.requestFinished", {"requestId": "R"})


def test_an_early_favicon_is_still_known_after_many_later_ones():
    """Known bad: the memory once kept the last 64 ids, and a late question about
    an early favicon (a page replaying the events it held at birth) answered
    "not a favicon" once 64 more had come. An id lives as long as its session."""
    f = FaviconRequests()
    f.see(*_sent("F0", "http://h/favicon.ico"))
    f.see("Network.requestFinished", {"requestId": "F0"})
    for i in range(1, 1000):
        f.see(*_sent("F%d" % i, "http://h/favicon.ico"))
    assert f.is_favicon("F0")
    assert f.see("Network.requestFinished", {"requestId": "F0"})


def test_asking_classifies_nothing_and_the_answer_outlives_the_request():
    """`is_favicon` is the server's question: it never decides on its own, so
    there is one memory to agree with, and that memory still answers after the
    request has finished (a page replays the events it held at birth later)."""
    f = FaviconRequests()
    assert not f.is_favicon("FAV")
    f.see(*_sent("FAV", "http://h/favicon.ico"))
    f.see("Network.requestFinished", {"requestId": "FAV"})
    assert f.is_favicon("FAV")
    assert not f.is_favicon(None)


def test_an_event_without_a_request_id_is_not_a_favicon():
    assert not FaviconRequests().see("Network.requestWillBeSent",
                                     {"url": "http://h/favicon.ico"})
