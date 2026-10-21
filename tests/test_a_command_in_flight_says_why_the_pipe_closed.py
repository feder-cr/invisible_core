"""A command waiting for its reply when the pipe closes is told why, like the
command sent after it.

The known-bad input, measured by a5 on 2026-10-21: an engine that refuses an
undeclared pref in the middle of a session (the parent refusing during
`Browser.newPage`) prints the reason and exits. The reason was in the kept
output, and a command sent AFTER the close carried it (`_closed_error`), but
the command IN FLIGHT got a bare "the pipe closed": the reader woke it with a
message of its own that never asked `_why_closed`. Two places said what a
closed pipe means, and one of them did not know the reason.

And the second half of the same fact: the reason travels on the browser's
OUTPUT, another pipe with another reader, so it can arrive after the
connection's reader has already woken the waiter. The waiter waits for the
output to end (`ProcessOutput.settle`) before reading it.

No browser starts here: the two pipes are `os.pipe()` pairs whose browser
ends the test holds.

Known-bad, run before this file was trusted: the reader's box carrying the
old `{"error": {"name": "TargetClosedError", "message": "the pipe closed"}}`
again (the first test goes red: no reason in the message); `settle` a no-op
(the second goes red: the line written after the close is missing).
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from invisible_core.juggler.connection import (Connection, ProcessOutput,
                                               TargetClosedError)

REASON = b"stealth: zoom.stealth.canvas.edge_tolerance is not declared\n"


class _Browser:
    """The browser's ends: its output, and the two halves of the protocol
    pipe. `goes_away` closes the protocol pipe, which is what an exit does."""

    def __init__(self) -> None:
        out_r, self.out_w = os.pipe()
        self.output = ProcessOutput(os.fdopen(out_r, "rb"))
        self.to_r, to_w = os.pipe()
        from_r, self.from_w = os.pipe()
        self.connection = Connection(to_w, from_r, None)
        self.connection.output = self.output

    def prints(self, line: bytes) -> None:
        os.write(self.out_w, line)

    def goes_away(self) -> None:
        os.close(self.from_w)

    def output_ends(self) -> None:
        os.close(self.out_w)

    def release(self) -> None:
        for fd in (self.to_r, self.out_w):
            try:
                os.close(fd)
            except OSError:
                pass


def _in_flight(conn: Connection, method: str) -> tuple:
    """Send `method` from a thread and return (thread, what it raised)."""
    caught: list = []

    def call():
        try:
            conn.send(method, timeout=10)
        except BaseException as failure:      # noqa: BLE001 - the type is the point
            caught.append(failure)

    t = threading.Thread(target=call)
    t.start()
    # The command is pending before the browser goes: `send` registered it.
    deadline = time.monotonic() + 5
    while not conn._pending and time.monotonic() < deadline:
        time.sleep(0.01)
    assert conn._pending, "the command never went out"
    return t, caught


def test_a_command_waiting_when_the_pipe_closes_names_the_reason():
    b = _Browser()
    b.prints(REASON)
    deadline = time.monotonic() + 5
    while not b.output.tail() and time.monotonic() < deadline:
        time.sleep(0.01)
    t, caught = _in_flight(b.connection, "Browser.newPage")
    b.goes_away()
    b.output_ends()
    t.join(timeout=5)
    b.release()
    assert not t.is_alive(), "the waiter was left to the timeout"
    assert caught and isinstance(caught[0], TargetClosedError), caught
    message = str(caught[0])
    assert message.startswith("Browser.newPage: the pipe closed"), message
    assert "edge_tolerance is not declared" in message, message


def test_the_reason_printed_just_after_the_close_still_reaches_the_waiter():
    b = _Browser()
    t, caught = _in_flight(b.connection, "Browser.newPage")
    b.goes_away()                             # the protocol pipe first
    time.sleep(0.2)
    b.prints(REASON)                          # then the line, on the other pipe
    b.output_ends()
    t.join(timeout=5)
    b.release()
    assert caught and isinstance(caught[0], TargetClosedError), caught
    assert "edge_tolerance is not declared" in str(caught[0]), str(caught[0])


def test_the_waiter_and_the_next_call_give_the_same_reason():
    b = _Browser()
    b.prints(REASON)
    t, caught = _in_flight(b.connection, "Browser.newPage")
    b.goes_away()
    b.output_ends()
    t.join(timeout=5)
    with pytest.raises(TargetClosedError) as after:
        b.connection.send("Browser.getInfo", timeout=1)
    b.release()
    waiting = str(caught[0]).split(": the pipe closed: ", 1)[1]
    later = str(after.value).split("the pipe is closed: ", 1)[1]
    assert waiting == later, (waiting, later)
