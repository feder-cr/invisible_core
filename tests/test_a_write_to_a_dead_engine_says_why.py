"""A command written to an engine that has already closed its end of the pipe is told why, like a command sent after the close.

The known-bad input: the e2e of the wrapper on a hosted runner (2026-11-12) had `BrowserType.launch: [Errno 32] Broken pipe`, bare. The
engine had died between the check at the top of `send_placed` and the first write (`Browser.enable`), before the reader saw the end of
its pipe, and `Connection._write` let `os.write` raise `BrokenPipeError`. That error carried neither the exit code nor the last lines of
the engine, and it was no `TargetClosedError`, the class a client's rerun rules recognise for "the browser went away".

The write is ONE place (`Connection._write`): a closed pipe there is the same fact as a closed connection. `send` raises
`_closed_error()` (exit code, last output), the command is not left pending, and `post` (no reply wanted) drops the message as it
does once `close` has run. Anything else that makes a write fail is not swallowed.

No browser starts here: the two pipes are `os.pipe()` pairs whose browser ends the test holds, and the process is a stand-in that
reports an exit code. Known-bad, run before this file was trusted: the old `_write` (the first two tests go red with the bare
BrokenPipeError; the third stays green, it proves nothing else was changed).
"""
from __future__ import annotations

import errno
import os

import pytest

from invisible_core.juggler import connection as connection_module
from invisible_core.juggler.connection import Connection, ProcessOutput, TargetClosedError

REASON = b"stealth: the parent refused an undeclared pref and exits\n"


class _DeadProcess:
    """Reports the exit code a crashed engine has."""

    pid = 4242

    def __init__(self, code: int) -> None:
        self.code = code

    def poll(self):
        return self.code


class _SlowToExit:
    """Still running when the write fails, exited a moment later: the window in which the pipe is closed and `poll()` says None."""

    pid = 4243

    def __init__(self, polls_alive: int, code: int = -11) -> None:
        self.polls_alive, self.code = polls_alive, code

    def poll(self):
        if self.polls_alive > 0:
            self.polls_alive -= 1
            return None
        return self.code

    def terminate(self) -> None:
        self.polls_alive = 0

    kill = terminate

    def wait(self, timeout=None):
        self.polls_alive = 0
        return self.code


class _Browser:
    """The browser's ends of the protocol pipe, and its output. `dies` is what an exit does to the pipe it reads from."""

    def __init__(self, exit_code: int = -11, process=None) -> None:
        out_r, self.out_w = os.pipe()
        self.output = ProcessOutput(os.fdopen(out_r, "rb"))
        self.to_r, to_w = os.pipe()
        from_r, self.from_w = os.pipe()
        self.connection = Connection(to_w, from_r, process or _DeadProcess(exit_code))
        self.connection.output = self.output

    def dies(self, last_words: bytes = REASON) -> None:
        os.write(self.out_w, last_words)
        os.close(self.out_w)           # its output ends with it
        os.close(self.to_r)            # nobody reads the protocol pipe any more: the next write is EPIPE
        self.out_w = -1

    def release(self) -> None:
        for fd in (self.from_w, self.to_r, self.out_w):
            try:
                if fd >= 0:
                    os.close(fd)
            except OSError:
                pass
        self.connection.close(timeout=0.2)


@pytest.fixture
def browser():
    b = _Browser()
    yield b
    b.release()


def test_a_command_written_to_a_dead_engine_names_the_exit_and_the_last_output(browser):
    browser.dies()
    with pytest.raises(TargetClosedError) as caught:
        browser.connection.send("Browser.enable", timeout=5)
    text = str(caught.value)
    assert "exited with code -11" in text, text
    assert "undeclared pref" in text, text
    assert "the pipe is closed: [Errno" in text, "the error of the write (EPIPE; EINVAL on Windows) heads the reason: " + text
    assert type(caught.value).__name__ == "TargetClosedError"


def test_the_command_that_could_not_be_written_is_not_left_pending(browser):
    browser.dies()
    with pytest.raises(TargetClosedError):
        browser.connection.send("Browser.enable", timeout=5)
    assert browser.connection._pending == {}


def test_a_message_nobody_waits_for_is_dropped_by_a_dead_engine(browser):
    browser.dies()
    browser.connection.post("Page.screencastFrameAck", {"screencastSessionId": "1"})   # no exception: nothing to tell


def test_a_write_that_fails_for_another_reason_is_not_swallowed(browser, monkeypatch):
    def full(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(connection_module.os, "write", full)
    with pytest.raises(OSError) as caught:
        browser.connection.send("Browser.enable", timeout=5)
    assert caught.value.errno == errno.ENOSPC
    assert not isinstance(caught.value, TargetClosedError)


# --- EINVAL: a closed pipe on Windows only when the engine is gone --------------------------------

def _einval(fd, data):
    raise OSError(errno.EINVAL, "Invalid argument")


@pytest.fixture
def windows(monkeypatch):
    """Stand on the Windows side: EINVAL may be a closed pipe there; the wait for the proof is short."""
    monkeypatch.setattr(connection_module, "_IS_WINDOWS", True, raising=False)
    monkeypatch.setattr(connection_module, "_GONE_WAIT", 0.3, raising=False)


def test_an_einval_with_the_engine_alive_and_the_reader_open_is_raised_as_it_came(windows, monkeypatch):
    """A descriptor that is not valid, a count out of range: nothing says the engine is gone, so nothing says it was."""
    b = _Browser(process=_SlowToExit(polls_alive=10 ** 6))
    try:
        monkeypatch.setattr(connection_module.os, "write", _einval)
        with pytest.raises(OSError) as caught:
            b.connection.send("Browser.enable", timeout=5)
        assert caught.value.errno == errno.EINVAL
        assert not isinstance(caught.value, TargetClosedError)
        assert b.connection._pending == {}
    finally:
        b.release()


def test_an_einval_with_the_engine_exited_is_a_closed_pipe(windows, monkeypatch):
    b = _Browser(exit_code=-11)
    try:
        b.dies()
        monkeypatch.setattr(connection_module.os, "write", _einval)
        with pytest.raises(TargetClosedError) as caught:
            b.connection.send("Browser.enable", timeout=5)
        assert "exited with code -11" in str(caught.value)
        assert "undeclared pref" in str(caught.value)
    finally:
        b.release()


def test_an_einval_while_poll_still_says_running_waits_for_the_exit(windows, monkeypatch):
    """The window: the pipe is closed, the process has not yet been reaped. A bare `poll()` would rethrow the EINVAL."""
    b = _Browser(process=_SlowToExit(polls_alive=5, code=-11))
    try:
        b.dies()
        monkeypatch.setattr(connection_module.os, "write", _einval)
        with pytest.raises(TargetClosedError) as caught:
            b.connection.send("Browser.enable", timeout=5)
        assert "exited with code -11" in str(caught.value)
    finally:
        b.release()


def test_an_einval_off_windows_is_never_a_closed_pipe(monkeypatch):
    monkeypatch.setattr(connection_module, "_IS_WINDOWS", False, raising=False)
    b = _Browser(exit_code=-11)
    try:
        b.dies()
        monkeypatch.setattr(connection_module.os, "write", _einval)
        with pytest.raises(OSError) as caught:
            b.connection.send("Browser.enable", timeout=5)
        assert caught.value.errno == errno.EINVAL and not isinstance(caught.value, TargetClosedError)
    finally:
        b.release()
