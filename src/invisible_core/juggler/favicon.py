"""Which requests of a page are its favicon, so that nothing counts or shows them.

Playwright never shows its client the favicon request a browser makes on its
own: upstream marks a request whose URL ends in `/favicon.ico`, or that a
favicon request redirected to, and then sends none of its `request`,
`response`, `requestfinished` or `requestfailed` events, leaves it out of the
requests in flight that `networkidle` waits on, and gives it to no route
handler. A client written against Playwright expects exactly that.

⛔ ONE RULE, HERE, AND ONE MEMORY PER READER. The rule is read by two layers
that see the same events at different moments: the lifecycle, which counts
the requests in flight, and a client's server, which reports them and offers
them to routes. A page's server replays the events it held at birth after the
lifecycle has finished with them, so a shared memory would answer the server
about a request the lifecycle had already forgotten. Each reader keeps its own
`FaviconRequests` and feeds it the events in its own order; what a favicon IS
lives only in `is_favicon_url` and `FaviconRequests.see`.

⛔ THE REQUEST STILL GOES TO THE NETWORK. Upstream aborts a favicon request
when a route is on; a real Firefox always asks the site for its favicon, so a
site would see one less request than from any Firefox. Hidden from the client,
it is let through.
"""
from __future__ import annotations

from collections import OrderedDict

__all__ = ["FaviconRequests", "is_favicon_url"]

#: How many favicon request ids a reader remembers. An id has to outlive its
#: own `requestFinished`: the engine finishes a redirect's hop BEFORE it sends
#: the next hop, whose only tie to it is `redirectedFrom`. There is one favicon
#: request per document, so 64 is far more than a chain in flight can need, and
#: the bound is what keeps a long session from growing it for ever.
_KEPT = 64


def is_favicon_url(url: str) -> bool:
    """Upstream's test, on the URL without its fragment."""
    return url.split("#", 1)[0].endswith("/favicon.ico")


class FaviconRequests:
    """The favicon requests one reader of a session's events has seen."""

    def __init__(self) -> None:
        self._ids: "OrderedDict[str, None]" = OrderedDict()

    def see(self, method: str, params: dict) -> bool:
        """Whether this `Network.*` event belongs to a favicon request.

        A request is classified once, by its `Network.requestWillBeSent`; its
        later events are answered by its id.
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
