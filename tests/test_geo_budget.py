"""The geo step has to be bounded as a STEP, not just per request.

The bug: three IP-echo endpoints, tried in sequence, ten seconds each. Every
individual request was bounded and the step as a whole was not, so the worst
case was thirty seconds of a launch and one launch in six spent 35s here. A
per-request timeout answers "how long do we wait for this server"; it cannot
answer "how long is the caller willing to wait", and only the second question
is the one a launch cares about.

Every test here drives the clock and the transport rather than the network, so
they are deterministic and take milliseconds. The point is the arithmetic of
the budget, and real sockets would only make that harder to see.
"""
from __future__ import annotations

import pytest

from invisible_core import _geo

pytestmark = pytest.mark.unit


class _Clock:
    """A monotonic clock that only moves when a request 'takes' time."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(_geo.time, "monotonic", c)
    return c


def _hang_all(monkeypatch, clock, *, cost: float):
    """Every endpoint burns its full allowed timeout, then fails."""
    seen: list[float] = []

    def fake_get(url, proxies=None, timeout=None):
        seen.append(timeout)
        clock.now += min(timeout, cost)
        raise OSError("timed out")

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    return seen


def test_the_step_stops_at_the_budget_however_many_endpoints_there_are(
    monkeypatch, clock
):
    """The regression itself, stated as arithmetic.

    Endpoints that each burn the full per-request timeout must not add up past
    the budget. Before the fix this cost len(endpoints) * timeout with nothing
    watching the total.
    """
    seen = _hang_all(monkeypatch, clock, cost=99.0)
    with pytest.raises(_geo.GeoTimezoneError):
        _geo.discover_egress_ip(timeout=10.0, budget=15.0)
    assert sum(seen) <= 15.0 + 1e-9, f"spent {sum(seen)}s against a 15s budget"
    assert clock.now - 1000.0 <= 15.0 + 1e-9


def test_a_slow_first_endpoint_shortens_the_second_instead_of_adding_to_it(
    monkeypatch, clock
):
    """This is what makes it a budget and not just a smaller timeout."""
    seen = _hang_all(monkeypatch, clock, cost=99.0)
    with pytest.raises(_geo.GeoTimezoneError):
        _geo.discover_egress_ip(timeout=10.0, budget=12.0)
    assert seen[0] == 10.0, "the first request should get the full timeout"
    assert seen[1] == pytest.approx(2.0), (
        f"the second should get only the remaining budget, got {seen[1]}"
    )


def test_the_budget_never_hands_a_request_a_non_positive_timeout(
    monkeypatch, clock
):
    """requests treats timeout<=0 as an immediate failure, and a stream of
    those would be a busy-loop dressed up as retries. Once the budget is gone
    the loop stops instead."""
    seen = _hang_all(monkeypatch, clock, cost=99.0)
    with pytest.raises(_geo.GeoTimezoneError):
        _geo.discover_egress_ip(timeout=10.0, budget=10.0)
    assert all(t > 0 for t in seen), seen
    assert len(seen) == 1, "the budget was spent by the first request"


def test_the_failure_says_the_budget_ran_out_and_how_far_it_got(
    monkeypatch, clock
):
    """An operator seeing this in a log must not have to guess whether the
    proxy is broken or the deadline is simply too tight for it."""
    _hang_all(monkeypatch, clock, cost=99.0)
    with pytest.raises(_geo.GeoTimezoneError) as excinfo:
        _geo.discover_egress_ip(timeout=10.0, budget=10.0)
    msg = str(excinfo.value)
    assert "budget ran out" in msg
    assert f"of {len(_geo._IP_ECHO_ENDPOINTS)} endpoints" in msg


def test_a_fast_success_still_returns_on_the_first_endpoint(monkeypatch, clock):
    """The happy path must not have been slowed down or reordered."""
    calls: list[str] = []

    class _Resp:
        text = " 203.0.113.7 \n"

        def raise_for_status(self):
            pass

    def fake_get(url, proxies=None, timeout=None):
        calls.append(url)
        clock.now += 0.05
        return _Resp()

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    assert _geo.discover_egress_ip(timeout=10.0, budget=15.0) == "203.0.113.7"
    assert len(calls) == 1, "a success must not keep trying the other endpoints"


def test_a_later_endpoint_still_wins_inside_the_budget(monkeypatch, clock):
    """Bounding the step must not have turned the fallback list into a
    one-shot: a first endpoint that fails FAST should leave the rest usable."""
    class _Resp:
        text = "198.51.100.9"

        def raise_for_status(self):
            pass

    state = {"n": 0}

    def fake_get(url, proxies=None, timeout=None):
        state["n"] += 1
        clock.now += 0.2
        if state["n"] == 1:
            raise OSError("connection refused")
        return _Resp()

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    assert _geo.discover_egress_ip(timeout=10.0, budget=15.0) == "198.51.100.9"
    assert state["n"] == 2


def test_the_default_budget_is_smaller_than_trying_every_endpoint(monkeypatch):
    """A default that exceeds the old worst case would be a no-op default, and
    the fix would only work for callers who already knew to pass a budget."""
    import inspect

    sig = inspect.signature(_geo.discover_egress_ip)
    budget = sig.parameters["budget"].default
    timeout = sig.parameters["timeout"].default
    assert budget < timeout * len(_geo._IP_ECHO_ENDPOINTS), (
        f"default budget {budget}s does not bound the {timeout}s x "
        f"{len(_geo._IP_ECHO_ENDPOINTS)} worst case it exists to cap"
    )
