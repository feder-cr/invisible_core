"""A frame keeps the name the engine sends with its commit.

`Page.navigationCommitted` carries the frame's name (the iframe's `name`, or
its `id`), and pyppeteer's `frame.name` reads it. Until 41.39.0 the lifecycle
dropped it, so every client answered "" for every frame.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.lifecycle import Lifecycle

pytestmark = pytest.mark.unit


class FakeConnection(EventListeners):
    def send(self, method, params=None, session=None, timeout=30):
        return None


def test_the_commit_names_the_frame_and_the_name_outlives_the_frame():
    c = FakeConnection()
    v = Lifecycle(c, "S1")
    c.dispatch_event("Page.frameAttached", {"frameId": "F1"}, "S1")
    c.dispatch_event("Page.frameAttached", {"frameId": "F2", "parentFrameId": "F1"}, "S1")
    assert v.frames["F2"].name == ""
    c.dispatch_event("Page.navigationCommitted",
                     {"frameId": "F2", "navigationId": "N2", "url": "http://x/f",
                      "name": "theFrameName"}, "S1")
    frame = v.frames["F2"]
    assert frame.name == "theFrameName"
    c.dispatch_event("Page.frameDetached", {"frameId": "F2"}, "S1")
    assert "F2" not in v.frames
    # The object a client held keeps what it knew.
    assert (frame.name, frame.url) == ("theFrameName", "http://x/f")
