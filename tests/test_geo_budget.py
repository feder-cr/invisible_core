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


def _costo_reale(timeout):
    """Quanto puo' costare UNA richiesta, secondo `requests` e non secondo noi.

    Un timeout SCALARE non limita la richiesta: `requests` lo applica alla fase
    di connessione e poi di nuovo a quella di lettura, quindi `timeout=10` puo'
    spendere venti secondi. Una COPPIA `(connessione, lettura)` costa al massimo
    la loro somma.

    Questo banco modellava lo scalare come costo totale - cioe' l'intento invece
    della libreria - ed e' la ragione per cui il difetto e' sopravvissuto sotto
    quattro test verdi: misurato il 2026-08-10 su un proxy che non rispondeva,
    l'errore riportava `20.1s` con `budget=15` e "1 of 3 endpoints". Il budget
    non era mai stato rispettato in produzione, e qui risultava rispettato.
    """
    return sum(timeout) if isinstance(timeout, tuple) else 2.0 * timeout


def _hang_all(monkeypatch, clock, *, cost: float):
    """Every endpoint burns its full allowed timeout, then fails."""
    seen: list[float] = []

    def fake_get(url, proxies=None, timeout=None):
        seen.append(timeout)
        clock.now += min(_costo_reale(timeout), cost)
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
    speso = sum(_costo_reale(t) for t in seen)
    assert speso <= 15.0 + 1e-9, f"spent {speso}s against a 15s budget"
    assert clock.now - 1000.0 <= 15.0 + 1e-9


def test_a_slow_first_endpoint_shortens_the_second_instead_of_adding_to_it(
    monkeypatch, clock
):
    """This is what makes it a budget and not just a smaller timeout."""
    seen = _hang_all(monkeypatch, clock, cost=99.0)
    with pytest.raises(_geo.GeoTimezoneError):
        _geo.discover_egress_ip(timeout=10.0, budget=12.0)
    assert sum(seen[0]) == 10.0, "the first request should get the full timeout"
    assert sum(seen[1]) == pytest.approx(2.0), (
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
    assert all(sum(t) > 0 for t in seen), seen
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
    assert f"of {len(_geo._IP_ECHO_ENDPOINTS)} endpoint(s)" in msg
    # And the half a rewording cannot break: how far it got is an ATTRIBUTE,
    # not a sentence. The prose above is for a person reading a log; this is
    # what a caller should branch on.
    assert len(excinfo.value.attempts) == 1


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


# ── a locale that could not be resolved must not be silent ─────────────────

def test_a_failed_locale_lookup_says_so_instead_of_returning_en_US_quietly(
        monkeypatch, capsys):
    """The mismatch this prevents is the one the timezone trap exists for.

    Locale and timezone are resolved from the SAME egress IP, but only the
    locale falls back. So a failure here produced a session whose timezone said
    one country and whose language said the United States - a cross-field
    inconsistency a detector checks for, reaching users with no signal at all.

    The outcome is deliberately unchanged: raising would break launches that
    work today, on a field a caller can set explicitly. Only the silence is
    fixed.
    """
    def boom(*a, **kw):
        raise RuntimeError("geoip unavailable")

    monkeypatch.setattr(_geo, "discover_egress_ip", boom)
    got = _geo.resolve_session_locale(None, None)
    assert got == "en-US", "the fallback outcome must not change"

    err = capsys.readouterr().err
    assert "could not resolve the session locale" in err
    assert "geoip unavailable" in err, "the cause must be named, not swallowed"
    assert "locale=" in err, "the message must name the way out"


def test_the_warning_distinguishes_the_proxy_case(monkeypatch, capsys):
    """Behind a proxy the mismatch is worse - the timezone follows the exit
    country while the language does not - so the message has to say which
    situation the reader is in."""
    monkeypatch.setattr(_geo, "ip_to_locale",
                        lambda *a, **kw: (_ for _ in ()).throw(ValueError("no record")))
    monkeypatch.setattr(_geo, "_proxy_is_set", lambda proxy: True)
    _geo.resolve_session_locale("203.0.113.7", {"server": "socks5://x:1"})
    assert "behind a proxy" in capsys.readouterr().err


def test_a_resolved_locale_stays_quiet(monkeypatch, capsys):
    """A warning on the happy path would train people to ignore it."""
    monkeypatch.setattr(_geo, "_proxy_is_set", lambda proxy: True)
    monkeypatch.setattr(_geo, "ip_to_locale", lambda *a, **kw: "it-IT")
    assert _geo.resolve_session_locale("203.0.113.7", {"server": "s"}) == "it-IT"
    assert capsys.readouterr().err == ""
