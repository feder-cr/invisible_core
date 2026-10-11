"""A command the browser did not answer is a type of its own, not a refusal.

The known-bad input, from the firefox-41 gate on Linux under load: the
wrapper's launch sent `Browser.setBrowserProxy` with a fixed 10 s, got no reply
in time, and told the caller "the engine refused the proxy". The connection
raised the same ProtocolError for a refusal and for a silence, so the caller
could only tell them apart by matching the sentence.

And `wait_for_ready(None)`, the readiness wait of a caller who asked for no
limit (Playwright's `launch(timeout=0)`), which used to be turned into 60 s.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from invisible_core.juggler.connection import (
    Connection, ProcessOutput, ProtocolError, ReplyTimeout, _READY)


def _pipes():
    to_r, to_w = os.pipe()
    from_r, from_w = os.pipe()
    return Connection(to_w, from_r), to_r, from_w


@pytest.mark.unit
def test_a_reply_that_never_comes_is_a_reply_timeout_and_still_a_protocol_error():
    c, _to_r, _from_w = _pipes()
    with pytest.raises(ReplyTimeout, match="no response") as caught:
        c.send("Browser.setBrowserProxy", {}, timeout=0.2)
    assert isinstance(caught.value, ProtocolError)
    assert isinstance(caught.value, TimeoutError)


@pytest.mark.unit
def test_a_refusal_is_a_protocol_error_and_not_a_timeout():
    c, _to_r, from_w = _pipes()
    reply = {"id": 1, "error": {"message": "proxy type is not supported"}}
    threading.Timer(0.1, os.write, (from_w, json.dumps(reply).encode() + b"\x00")).start()
    with pytest.raises(ProtocolError, match="not supported") as caught:
        c.send("Browser.setBrowserProxy", {}, timeout=5)
    assert not isinstance(caught.value, ReplyTimeout)


class _Alive:
    returncode = None

    def poll(self):
        return None


@pytest.mark.unit
def test_readiness_without_a_bound_waits_for_the_line():
    r, w = os.pipe()
    output = ProcessOutput(os.fdopen(r, "rb"))
    threading.Timer(0.5, os.write, (w, _READY.encode() + b"\n")).start()
    started = time.monotonic()
    assert output.wait_for_ready(_Alive(), None) is True
    assert time.monotonic() - started >= 0.4
    os.close(w)


@pytest.mark.unit
def test_readiness_without_a_bound_ends_when_the_process_exits():
    class _Exited:
        returncode = 1

        def poll(self):
            return 1

    r, w = os.pipe()
    output = ProcessOutput(os.fdopen(r, "rb"))
    os.close(w)
    assert output.wait_for_ready(_Exited(), None) is False
