"""The launch counter is part of invisible-playwright: it cannot be turned off and cannot be redirected.

`invisible_firefox.usage_ping.enabled` and `invisible_firefox.usage_ping.url` are not accepted in `extra_prefs`: a caller that passes either one gets a
clear error, on every road that reaches `compose_session_prefs` (the fingerprint's own `translate_profile_to_prefs`, the composition, the default prefs
a caller hands to Playwright), whatever the value (True, False, a string, None = "delete it"). Nothing is ignored in silence. The core writes the
address itself, by assignment, and never writes `enabled`: the engine's default (on) is the only state.
"""
import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.config import get_default_stealth_prefs
from invisible_core.prefs import (USAGE_PING_PREFS, USAGE_PING_REFUSAL, USAGE_PING_URL, compose_session_prefs,
                                  translate_profile_to_prefs)

pytestmark = pytest.mark.unit
ENABLED, URL = USAGE_PING_PREFS
MESSAGE = "the launch counter is part of invisible-playwright and cannot be turned off or redirected"


def _profile():
    return generate_profile(seed=4242)


def _compose(**kw):
    return compose_session_prefs(_profile(), timezone="America/New_York", **kw)


def _translate(**kw):
    return translate_profile_to_prefs(_profile(), timezone="America/New_York", **kw)


def _default_prefs(**kw):
    return get_default_stealth_prefs(seed=4242, locale="en-US", timezone="America/New_York", **kw)


ROADS = [("compose_session_prefs", _compose), ("translate_profile_to_prefs", _translate), ("get_default_stealth_prefs", _default_prefs)]
ASKS = [(ENABLED, False), (ENABLED, True), (ENABLED, None), (URL, "https://example.invalid/x"), (URL, USAGE_PING_URL), (URL, None)]


def test_the_message_is_the_one_that_says_it():
    assert USAGE_PING_REFUSAL == MESSAGE


@pytest.mark.parametrize("road, build", ROADS, ids=[r for r, _ in ROADS])
@pytest.mark.parametrize("name, value", ASKS)
def test_either_pref_is_refused_with_the_message_on_every_road(road, build, name, value):
    with pytest.raises(ValueError, match=MESSAGE) as caught:
        build(extra_prefs={name: value})
    assert name in str(caught.value), "the error names the pref that was refused"


@pytest.mark.parametrize("road, build", ROADS, ids=[r for r, _ in ROADS])
def test_the_pref_is_refused_among_others_and_nothing_of_the_overlay_is_applied(road, build):
    with pytest.raises(ValueError, match=MESSAGE):
        build(extra_prefs={"browser.newtab.preload": False, ENABLED: False})


@pytest.mark.parametrize("road, build", ROADS, ids=[r for r, _ in ROADS])
def test_a_session_declares_the_address_and_never_writes_enabled(road, build):
    prefs = build()
    prefs = getattr(prefs, "prefs", prefs)
    if road != "translate_profile_to_prefs":  # the fingerprint alone is not a session: only the composition declares the address
        assert prefs[URL] == USAGE_PING_URL
    assert ENABLED not in prefs, "the core never writes the switch, the engine's default is the state"


def test_other_prefs_of_the_caller_still_go_through():
    prefs = _compose(extra_prefs={"browser.newtab.preload": False}).prefs
    assert prefs["browser.newtab.preload"] is False
    assert prefs[URL] == USAGE_PING_URL
