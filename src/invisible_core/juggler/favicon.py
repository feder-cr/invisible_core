"""Which requests of a page are its favicon, so that nothing counts or shows them.

Playwright never shows its client the favicon request a browser makes on its
own: upstream marks a request whose URL ends in `/favicon.ico`, or that a
favicon request redirected to, and then sends none of its `request`,
`response`, `requestfinished` or `requestfailed` events, leaves it out of the
requests in flight that `networkidle` waits on, and gives it to no route
handler. A client written against Playwright expects exactly that.

⛔ ONE RULE AND ONE MEMORY, THE LIFECYCLE'S. Two layers need the answer: the
`Lifecycle`, which counts the requests in flight, and a client's server, which
reports them and offers them to routes. The session's `Lifecycle` owns the
only `FaviconRequests` (`Lifecycle.favicons`): it classifies each request as
its event arrives, and the server asks it by id and keeps nothing of its own.
That works because the lifecycle reads every event of its session before a
page's server does (it subscribes first), and because an id is remembered
past its request's end, so a page that replays the events it held at birth
gets the same answer later. Two memories could disagree - a redirect one of
them missed, a replay, a session that went away under one of them - and one
cannot.

⛔ THE REQUEST STILL GOES TO THE NETWORK. Upstream aborts a favicon request
when a route is on; a real Firefox always asks the site for its favicon, so a
site would see one less request than from any Firefox. Hidden from the client,
it is let through.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Optional

__all__ = ["FaviconRequests", "is_favicon_url"]

#: How many favicon request ids are remembered. An id has to outlive its own
#: `requestFinished`: the engine finishes a redirect's hop BEFORE it sends the
#: next hop, whose only tie to it is `redirectedFrom`, and a page's server can
#: ask after the lifecycle has seen the whole request. There is one favicon
#: request per document, so 64 is far more than either can need, and the bound
#: is what keeps a long session from growing it for ever.
_KEPT = 64


def is_favicon_url(url: str) -> bool:
    """Upstream's test, on the URL without its fragment."""
    return url.split("#", 1)[0].endswith("/favicon.ico")


class FaviconRequests:
    """The favicon requests of one session: fed by its lifecycle, asked by anyone."""

    def __init__(self) -> None:
        self._ids: "OrderedDict[str, None]" = OrderedDict()

    def see(self, method: str, params: dict) -> bool:
        """Read one `Network.*` event and say whether it is a favicon's.

        The lifecycle's call, once per event: a request is classified by its
        `Network.requestWillBeSent`, and its later events are answered by id.
        """
        request_id = params.get("requestId")
        if request_id is None:
            return False
        if method != "Network.requestWillBeSent":
            return request_id in self._ids
        if not (is_favicon_url(params.get("url") or "")
                or params.get("redirectedFrom") in self._ids):
            return False
        self._ids[request_id] = None
        while len(self._ids) > _KEPT:
            self._ids.popitem(last=False)
        return True

    def is_favicon(self, request_id: Optional[str]) -> bool:
        """Whether a request already seen is a favicon's. It classifies nothing."""
        return request_id is not None and request_id in self._ids
