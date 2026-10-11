"""`launch` has one deadline, the caller's, for the whole startup.

⛔ THE KNOWN-BAD INPUT, from the firefox-41 gate on Linux under load: the
readiness wait had the caller's `launch(timeout=)`, and the commands after it
had bounds of their own in each of the three clients, `Browser.setBrowserProxy`
10 s and `Browser.enable` 30 s. The proxy command went unanswered for 10 s and
the caller read "the engine refused the proxy, so the browser was closed" while
its 60 s still had time and the engine had refused nothing.

The browser here is a fake on real OS pipes, put where `launch` spawns: it
prints the readiness line after `READY_AFTER` seconds and answers each command
after the delay `answers` names, or with the error `refuses` names. Its process
ends when the pipe to it closes, as Juggler ends the browser.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from invisible_core.juggler import connection
from invisible_core.juggler.connection import (
    NUL, ProtocolError, ReplyTimeout, _READY)

PROXY = {"type": "socks", "host": "127.0.0.1", "port": 1080}


class _Process:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = None
        self.pid = 1

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15


class _Browser:
    def __init__(self, ready_after=0.0, answers=None, refuses=()):
        self.ready_after = ready_after
        self.answers = dict(answers or {})
        self.refuses = set(refuses)
        self.process = None
        # An answer still sleeping when the browser exits must not write: its
        # descriptor is closed by then, and the next test's pipe may have
        # been given the same number.
        self._exit_lock = threading.Lock()
        self._exited = False

    def spawn(self, executable, argv, env):
        to_r, to_w = os.pipe()
        from_r, from_w = os.pipe()
        out_r, out_w = os.pipe()
        self.process = _Process(os.fdopen(out_r, "rb"))
        threading.Thread(target=self._run, args=(to_r, from_w, out_w),
                         daemon=True).start()
        return to_w, from_r, self.process

    def _run(self, to_r, from_w, out_w):
        time.sleep(self.ready_after)
        os.write(out_w, _READY.encode() + b"\n")
        buffer = b""
        while True:
            chunk = os.read(to_r, 65536)
            if not chunk:
                break
            buffer += chunk
            while NUL in buffer:
                raw, buffer = buffer.split(NUL, 1)
                msg = json.loads(raw)
                threading.Thread(target=self._answer, args=(from_w, msg),
                                 daemon=True).start()
        # Exiting closes the browser's ends, as a real one does: the
        # connection's reader is blocked on the other end until then.
        with self._exit_lock:
            self._exited = True
            self.process.returncode = 0
            os.close(from_w)
            os.close(out_w)

    def _answer(self, from_w, msg):
        time.sleep(self.answers.get(msg["method"], 0.0))
        if msg["method"] in self.refuses:
            reply = {"id": msg["id"], "error": {"message": "proxy refused here"}}
        else:
            reply = {"id": msg["id"], "result": {}}
        with self._exit_lock:
            if not self._exited:
                os.write(from_w, json.dumps(reply).encode() + NUL)


def _launch(monkeypatch, browser, **kw):
    monkeypatch.setattr(connection, "_spawn_windows", browser.spawn)
    monkeypatch.setattr(connection, "_spawn_posix", browser.spawn)
    return connection.launch("firefox", "profile", **kw)


@pytest.mark.unit
def test_a_proxy_answered_inside_the_launch_timeout_is_set(monkeypatch):
    browser = _Browser(answers={"Browser.setBrowserProxy": 0.6})
    c = _launch(monkeypatch, browser, ready_timeout=3.0, proxy=PROXY)
    try:
        assert c.ready_seen
        c.startup_send("Browser.enable", {})
    finally:
        c.close()


@pytest.mark.unit
def test_a_launch_past_its_timeout_is_a_timeout_and_the_browser_is_closed(monkeypatch):
    browser = _Browser(answers={"Browser.setBrowserProxy": 3.0})
    started = time.monotonic()
    with pytest.raises(ReplyTimeout, match="Timeout 1000ms exceeded") as caught:
        _launch(monkeypatch, browser, ready_timeout=1.0, proxy=PROXY)
    assert time.monotonic() - started < 2.5
    assert isinstance(caught.value, TimeoutError)
    assert "refused" not in str(caught.value)
    assert browser.process.poll() is not None, "the browser was left running"


@pytest.mark.unit
def test_readiness_and_proxy_share_the_deadline(monkeypatch):
    """0.7 s to print readiness and 0.7 s to answer the proxy is 1.4 s, past
    a 1 s launch: each step alone fits, the startup does not."""
    browser = _Browser(ready_after=0.7, answers={"Browser.setBrowserProxy": 0.7})
    with pytest.raises(ReplyTimeout, match="Timeout 1000ms exceeded"):
        _launch(monkeypatch, browser, ready_timeout=1.0, proxy=PROXY)


@pytest.mark.unit
def test_startup_send_has_what_is_left_of_the_launch(monkeypatch):
    browser = _Browser(ready_after=0.6, answers={"Browser.enable": 0.6})
    c = _launch(monkeypatch, browser, ready_timeout=1.0)
    try:
        with pytest.raises(ReplyTimeout, match="Timeout 1000ms exceeded"):
            c.startup_send("Browser.enable", {})
    finally:
        c.close()


@pytest.mark.unit
def test_a_refused_proxy_is_a_refusal_and_the_browser_is_closed(monkeypatch):
    browser = _Browser(refuses={"Browser.setBrowserProxy"})
    with pytest.raises(ProtocolError, match="refused the proxy") as caught:
        _launch(monkeypatch, browser, ready_timeout=3.0, proxy=PROXY)
    assert not isinstance(caught.value, ReplyTimeout)
    assert browser.process.poll() is not None, "the browser was left running"


@pytest.mark.unit
def test_no_limit_waits_for_a_slow_startup(monkeypatch):
    browser = _Browser(ready_after=0.3, answers={"Browser.setBrowserProxy": 0.5,
                                                 "Browser.enable": 0.5})
    c = _launch(monkeypatch, browser, ready_timeout=None, proxy=PROXY)
    try:
        c.startup_send("Browser.enable", {})
    finally:
        c.close()
