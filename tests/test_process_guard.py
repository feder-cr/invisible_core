"""The lifetime guard, and the two things measurement corrected about it.

`invisible_core.process` is shared because both products launch the same
browser and both had the same problem. The wrapper closed it on 2026-07-26; the
profile manager had no guard at all, and on 2026-07-27 a killed manager left
EIGHT firefox processes behind, three runs out of three - 100%, against the
wrapper's intermittent 50%.

Two findings came out of measuring rather than reasoning, and both are here:

  1. `_adopted` recorded ATTEMPTS, not successes. A process whose assignment
     failed was never retried and the returned count was a number of tries. It
     reported 8 adopted out of 8 while 8 survived the kill - a guard announcing
     a guarantee it did not have, which is worse than no guard.

  2. Adopting after the fact CANNOT work for this browser.
     `AssignProcessToJobObject` returns ERROR_ACCESS_DENIED (5) for six of the
     eight, and ERROR_NOT_ENOUGH_QUOTA (1816) for a seventh: Firefox puts its
     content processes into its own sandbox jobs, and a process already in a
     non-nestable job cannot be added to another. Only the top process could be
     taken. `spawn_into` inverts it - create suspended, assign, resume - so the
     whole tree is BORN inside the job, which is the case Windows supports.
     After: 8 survivors became 0, three runs of three.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from invisible_core import process as P

pytestmark = pytest.mark.unit


class _FakeProc:
    def __init__(self, pid):
        self.pid = pid


def _guard_with(adopt_result):
    """A JobObjectGuard with the Win32 layer replaced, so the POLICY is testable
    on any platform. Everything below is about the loop, not about ctypes."""
    g = P.JobObjectGuard.__new__(P.JobObjectGuard)
    g._adopted = set()
    g._adopt = adopt_result
    return g


def test_a_failed_assignment_is_not_recorded_as_adopted():
    """The defect that made the guard lie. If a failure is remembered as done,
    it is never retried and the count is a number of attempts."""
    attempts = []

    def adopt(pid):
        attempts.append(pid)
        return False                      # every assignment is refused

    guard = _guard_with(adopt)
    monkey = [_FakeProc(1), _FakeProc(2)]
    original = P.find_processes
    P.find_processes = lambda _t: monkey
    try:
        bound = guard.bind(P.SessionToken.mint(), wait=1.2, settle=0.2)
    finally:
        P.find_processes = original

    assert bound == 0, "a guard that adopted nothing must not report a count"
    assert guard._adopted == set(), (
        "a refused assignment was recorded as adopted, so it will never be "
        "retried and the guard reports holding a process it does not hold")
    assert len(attempts) > 2, "the failures were not retried on a later pass"


def test_a_successful_assignment_is_recorded_once():
    seen = []
    guard = _guard_with(lambda pid: (seen.append(pid), True)[1])
    original = P.find_processes
    P.find_processes = lambda _t: [_FakeProc(11), _FakeProc(12)]
    try:
        bound = guard.bind(P.SessionToken.mint(), wait=2.0, settle=0.2)
    finally:
        P.find_processes = original
    assert bound == 2
    assert guard._adopted == {11, 12}
    assert seen == [11, 12], "a pid was assigned twice"


def test_the_null_guard_offers_the_same_spawn_so_no_caller_branches():
    """`spawn_into` has to exist on both implementations, or every launcher
    grows an `if os.name == 'nt'` - which is the thing this module removes."""
    assert hasattr(P.NullGuard(), "spawn_into")
    assert hasattr(P.guard_for(), "spawn_into")


def test_the_null_guard_spawn_is_an_ordinary_spawn_and_says_so():
    guard = P.NullGuard()
    proc = guard.spawn_into([sys.executable, "-c", "pass"])
    assert isinstance(proc, subprocess.Popen)
    proc.wait(timeout=30)
    assert guard.guaranteed is False


def test_spawn_into_returns_a_running_process_not_a_suspended_one():
    """`spawn_into` creates the process SUSPENDED so it can be assigned before
    it spawns anything. If the resume is ever lost, every launch hangs with a
    browser that never starts - a worse failure than the leak it fixes."""
    guard = P.guard_for()
    proc = guard.spawn_into([sys.executable, "-c", "import sys; sys.exit(7)"])
    assert proc.wait(timeout=60) == 7, (
        "the child never ran to completion; a suspended process was left "
        "suspended")


def test_the_launch_plan_carries_a_token_that_is_already_in_its_env():
    """The manager had no identity for its tree at all. A token that is not part
    of the plan is a token somebody has to remember to put in the environment."""
    from invisible_core.process import TOKEN_VAR

    tok = P.SessionToken.mint()
    env = tok.stamp({"A": "1"})
    assert env[TOKEN_VAR] == tok.value and env["A"] == "1"
    assert tok.matches(type("P", (), {"environ": lambda self: env})())
