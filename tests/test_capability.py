"""`udp_is_usable`: the one line that says whether UDP is usable, not granted.

It used to be an expression inside `measure`, which does network, so the gate
that exists to prove this exact rule could not call it and kept a hand copy
instead. The copy fell behind when the core was translated and the field names
moved (`uscita_udp` -> `udp_exit`, `udp_coerente` -> `udp_matches_tcp`), which
is the ordinary fate of a second place that knows the same thing.

The assertions below are about the THREE answers. Two of them would be easy to
get right by accident; the one that carries the weight is `None`, which says the
question does not apply, and which any reader tempted to write `bool(...)` would
quietly turn into "no".
"""
from __future__ import annotations

import pytest

from invisible_core._capability import udp_is_usable

pytestmark = pytest.mark.unit


def test_a_gateway_that_did_not_grant_udp_answers_not_applicable():
    """None, and `is None` rather than a falsy check.

    A falsy assertion passes on False too, which is the exact flattening this
    test exists to refuse: a gateway that REFUSED and one that granted and then
    could not carry a packet are different facts about the provider.
    """
    assert udp_is_usable(False, None, None) is None
    # The grant is read for truth, the way `measure` read it inline: None from a
    # probe that did not complete is not a grant either.
    assert udp_is_usable(None, None, None) is None
    # And a stale UDP exit lying around cannot promote a refusal into an answer.
    assert udp_is_usable(False, "203.0.113.7", "203.0.113.7") is None


def test_granted_but_no_udp_exit_came_back_is_a_no():
    """The measured case: the relay is handed over and STUN never answers.

    `is False` and not just falsy, for the same reason as above in the other
    direction: this must not be allowed to become None either.
    """
    assert udp_is_usable(True, None, "203.0.113.7") is False
    assert udp_is_usable(True, "", "203.0.113.7") is False


def test_granted_with_a_different_exit_is_a_no():
    assert udp_is_usable(True, "198.51.100.4", "203.0.113.7") is False


def test_granted_with_no_tcp_exit_to_compare_against_is_a_no():
    """Nothing to agree with is not agreement.

    Both sides absent must not collapse into "equal", which is what a bare
    `udp_exit_ip == tcp_exit_ip` would do: None == None is True.
    """
    assert udp_is_usable(True, "198.51.100.4", None) is False
    assert udp_is_usable(True, None, None) is False


def test_granted_and_the_two_exits_agree_is_the_only_yes():
    assert udp_is_usable(True, "203.0.113.7", "203.0.113.7") is True


def test_the_three_answers_stay_three():
    """The tri-state as one assertion, which is the known-bad this file is for.

    Flatten the not-applicable case into False and this set has two members
    instead of three, whatever the individual cases were written to accept.
    """
    answers = {
        udp_is_usable(False, None, None),
        udp_is_usable(True, "198.51.100.4", "203.0.113.7"),
        udp_is_usable(True, "203.0.113.7", "203.0.113.7"),
    }
    assert answers == {None, False, True}, (
        "the three states collapsed: %r. `None` means the gateway granted no "
        "UDP at all and is not a 'no'." % sorted(answers, key=repr))


def test_measure_reports_what_udp_is_usable_decides(monkeypatch):
    """The JOINT, driven through the real `measure` with the network faked.

    Asserting that `measure`'s source contains a call would only prove the text
    says so. This runs the function for all three states and reads the field a
    consumer reads, so an extraction that returned the right answers from the
    wrong place, or wired the arguments in the wrong order, is caught here and
    not in a grep.
    """
    from invisible_core import _capability

    def drive(allowed, udp_ip, tcp_ip):
        monkeypatch.setattr(_capability, "tcp_exit", lambda p, **k: tcp_ip)
        monkeypatch.setattr(_capability, "udp_associate", lambda p, **k: (allowed, "why"))
        monkeypatch.setattr(_capability, "udp_exit", lambda p, **k: udp_ip)
        monkeypatch.setattr(_capability, "has_ipv6", lambda p, **k: False)
        return _capability.measure({"server": "socks5://gateway.example:1080"})

    assert drive(False, None, "203.0.113.7")["udp_matches_tcp"] is None
    assert drive(True, "198.51.100.4", "203.0.113.7")["udp_matches_tcp"] is False
    assert drive(True, "203.0.113.7", "203.0.113.7")["udp_matches_tcp"] is True
    assert drive(True, None, "203.0.113.7")["udp_matches_tcp"] is False
    assert drive(True, "203.0.113.7", None)["udp_matches_tcp"] is False


def test_the_two_exit_arguments_are_symmetric_so_only_the_signature_pins_them():
    """Measured, and it is why there is no behavioural test of argument order.

    `bool(x) and x == y` gives the same answer as `bool(y) and y == x` for every
    input: enumerated over four exit values and five grant values, 0 of 80
    combinations tell the swap apart. So a mutation that swaps the two IP
    arguments at the call site is an EQUIVALENT mutant, and a test claiming to
    catch it would be a comment, not an assertion. The bench found exactly that
    claim in an earlier draft of this file and it was removed rather than
    strengthened.

    What IS worth pinning is the signature, because the workbench gate calls
    this positionally: reordering the parameters is invisible to every
    behavioural assertion above and would silently hand the gate a grant where
    it means an address.
    """
    import inspect
    from invisible_core._capability import udp_is_usable as fn

    assert list(inspect.signature(fn).parameters) == [
        "udp_allowed", "udp_exit_ip", "tcp_exit_ip"], (
        "the signature moved; an external caller passing these positionally is "
        "now passing them to the wrong parameters")
    # And the grant is NOT symmetric with the others, which is the half a
    # behavioural test can see - so it is asserted rather than assumed.
    assert fn(True, "203.0.113.7", "203.0.113.7") is True
    assert fn("203.0.113.7", "203.0.113.7", True) is not True


def test_the_decision_is_importable_without_touching_the_network():
    """A caller that cannot afford `measure` has to be able to reach the rule.

    The module declares no `__all__`, so a top-level name with no leading
    underscore is the export, the same way `tcp_exit` and `measure` are. If that
    ever changes, this is what says so before the workbench gate goes dark
    again.
    """
    import invisible_core._capability as mod

    assert callable(getattr(mod, "udp_is_usable", None)), (
        "udp_is_usable is not reachable on the module; the workbench gate "
        "imports it by name")
    assert not hasattr(mod, "__all__") or "udp_is_usable" in mod.__all__
