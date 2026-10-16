"""The branding set a page reads is declared by the core (Profile.branding).

A page reaches the browser's branding through chrome://branding/content/ and
resource://usercontext-content/branding/, and what it reads there tells a build
from the retail one. The engine serves whichever set this package declares in
`zoom.stealth.branding.set`, and refuses to launch without it, so these tests
pin the declaration down: always present, the retail brand by default,
overridable like every other declared surface, and refused here, at the moment
it is passed, when it names a set the engine does not ship.
"""
import pytest

from invisible_core._fpforge import generate_profile
from invisible_core._fpforge.profile import (
    BRANDING,
    BRANDING_SETS,
    _PIN_TO_RAW,
    _validate_pin_key,
)
from invisible_core.prefs import translate_profile_to_prefs


@pytest.mark.unit
def test_the_default_is_the_retail_brand():
    assert BRANDING == "official"
    assert BRANDING in BRANDING_SETS
    assert generate_profile(seed=42).branding == "official"


@pytest.mark.unit
@pytest.mark.parametrize("seed", [1, 42, 7777, 123456])
def test_the_branding_is_invariant_not_sampled(seed):
    # A brand is a property of the build a session claims to be, not of the
    # machine, so no seed may move it.
    assert generate_profile(seed=seed).branding == BRANDING


@pytest.mark.unit
@pytest.mark.parametrize("branding", BRANDING_SETS)
def test_every_shipped_set_can_be_pinned(branding):
    _validate_pin_key("branding")
    assert _PIN_TO_RAW["branding"] == "branding"
    profile = generate_profile(seed=42, pin={"branding": branding})
    assert profile.branding == branding
    assert translate_profile_to_prefs(profile)["zoom.stealth.branding.set"] == branding


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["", "Official", "retail", "official/..", 1, None])
def test_a_set_the_engine_does_not_ship_is_refused_at_birth(bad):
    with pytest.raises(ValueError, match="not a set the engine ships"):
        generate_profile(seed=42, pin={"branding": bad})


@pytest.mark.unit
def test_the_declaration_is_always_emitted():
    # The engine's declaration gate refuses a launch without it, so a profile
    # that forgot it would not start at all.
    prefs = translate_profile_to_prefs(generate_profile(seed=42))
    assert prefs["zoom.stealth.branding.set"] == "official"
