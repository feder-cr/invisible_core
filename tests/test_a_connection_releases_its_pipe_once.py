"""A connection releases each pipe end once, and never touches the number again.

The known-bad input, reported against invisible-playwright on 2026-10-15: the
Playwright driver closes a browser's connection from `Browser.close` AND from
the server's shutdown hook. `Connection.close` closed its two descriptor
NUMBERS every time it was called, so the second call closed whatever the
process had been given under those numbers in the meantime - the pipe of
another Firefox launched in the same process, whose launch then died with
EISDIR.

The double call is legitimate (a session has more than one way out); what was
wrong is that nothing owned the descriptors. The same gap let the reader call
`os.read` on a number closed under it, and a write racing `close` land in
another browser's pipe. Each end now has one owner: the write end belongs to
the write lock, the read end to the reader thread, and neither is used after
it is released.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from invisible_core.juggler.connection import Connection, TargetClosedError

NUL = b"\x00"


class _Peer:
    """The browser's side of the two pipes: answers every command, records
    every message it was sent, and closes its ends when ours closes - which
    is what Juggler does when the pipe is closed."""

    def __init__(self) -> None:
        self.to_r, to_w = os.pipe()
        from_r, self.from_w = os.pipe()
        self.connection = Connection(to_w, from_r)
        self.received: list = []
        self.fds = {to_w, from_r}
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        buffer = b""
        try:
            while True:
                chunk = os.read(self.to_r, 65536)
                if not chunk:
                    break
                buffer += chunk
                while NUL in buffer:
                    raw, buffer = buffer.split(NUL, 1)
                    msg = json.loads(raw)
                    self.received.append(msg)
                    os.write(self.from_w, json.dumps(
                        {"id": msg["id"], "result": {}}).encode() + NUL)
        finally:
            os.close(self.to_r)
            os.close(self.from_w)

    def exited(self, timeout: float = 5.0) -> bool:
        self._thread.join(timeout)
        self.connection._reader.join(timeout)
        return not self._thread.is_alive() and not self.connection._reader.is_alive()


def _a_closed_connection_whose_numbers_were_given_to_another():
    first = _Peer()
    first.connection.send("Browser.enable", {}, timeout=5)
    first.connection.close()
    assert first.exited(), "the browser side did not see the pipe close"
    second = _Peer()
    # The precondition, asserted rather than assumed: the descriptors the
    # first connection released are the ones the second one now uses.
    assert first.fds & second.fds, (first.fds, second.fds)
    return first, second


@pytest.mark.unit
def test_a_second_close_leaves_the_pipe_that_now_has_those_numbers_alone():
    first, second = _a_closed_connection_whose_numbers_were_given_to_another()
    first.connection.close()
    # The second browser still talks: its pipe was not closed under it.
    assert second.connection.send("Browser.enable", {}, timeout=5) == {}
    second.connection.close()
    assert second.exited()


@pytest.mark.unit
def test_a_closed_connection_writes_nowhere_even_past_its_closed_check():
    first, second = _a_closed_connection_whose_numbers_were_given_to_another()
    # `_write` is where a send that passed the closed check before `close`
    # ran ends up: it must refuse, not write into the number.
    assert first.connection._write({"id": 999, "method": "x", "params": {}}) is False
    with pytest.raises(TargetClosedError):
        first.connection.send("Browser.enable", {}, timeout=5)
    first.connection.post("Browser.enable", {})
    assert second.connection.send("Browser.enable", {}, timeout=5) == {}
    assert [m["id"] for m in second.received] == [1]
    second.connection.close()
    assert second.exited()


@pytest.mark.unit
def test_the_reader_releases_the_read_end_when_the_browser_goes_first():
    # The browser exits on its own, before anyone calls `close`: the reader
    # sees the end of the pipe and releases its end there, and the `close`
    # that comes later releases only the write end.
    to_r, to_w = os.pipe()
    from_r, from_w = os.pipe()
    gone = Connection(to_w, from_r)
    os.close(from_w)
    gone._reader.join(5)
    assert not gone._reader.is_alive()
    with pytest.raises(TargetClosedError):
        gone.send("Browser.enable", {}, timeout=5)
    other = _Peer()
    assert from_r in other.fds | {other.to_r, other.from_w}, (from_r, other.fds)
    gone.close()
    os.close(to_r)
    assert other.connection.send("Browser.enable", {}, timeout=5) == {}
    other.connection.close()
    assert other.exited()
