"""The bundle must be conditioned on the GPU class the browser will actually report.

`translate_profile_to_prefs` applies `select_persona(profile.seed)`
UNCONDITIONALLY, so the renderer a page sees is always the persona's. Everything
the identification service cross-checks against that renderer - storage quota,
audio output latency and sample rate, screen size, devicePixelRatio, codec
support - is drawn from the bundle. If the bundle was conditioned on a different
class, the profile contradicts itself, and the contradiction is exactly what the
per-GPU pool was calibrated to remove (the 2026-06-18 A/B: a GTX 980 string over
another card's parameters mismatched at ~0.7-0.85).

Conditioning was a CALLER'S RESPONSIBILITY until 2026-07-27, and of the five call
sites three discharged it and two did not:

    config.py                  passed fixed_gpu_class
    launcher.py (wrapper sync) passed it
    async_api.py (wrapper async) passed it
    launch.py                  DID NOT - the profile-manager's launch path
    manager/fingerprint.py     DID NOT - the profile-manager's UI preview

Measured over 500 seeds before the fix: 355 of them (71%) produced a manager
profile whose emitted prefs differed from the wrapper's for the same seed, and
every manager profile with a mismatched class was internally incoherent. Because
BOTH manager paths were wrong in the same way, the UI preview agreed with the
launch and nothing looked wrong from inside the product.

The fix is structural rather than a fourth reminder: `generate_profile` defaults
`fixed_gpu_class` to the seed's own persona class, so a call site cannot omit it.
These tests hold that default in place and prove the two explicit overrides still
win, because a default that cannot be overridden would break pinning.
"""
from __future__ import annotations

import pytest

from invisible_core import generate_profile, translate_profile_to_prefs
from invisible_core._webgl_personas import forced_gpu_class, select_persona

pytestmark = pytest.mark.unit

#: Wide enough to catch a per-class regression. The mismatch sat at 71%, so any
#: sample would have caught THIS one; the width is for the next one, which may
#: be confined to a single class.
_SWEEP = 500


def test_every_seed_gets_a_bundle_matching_the_persona_it_will_expose():
    bad = [
        (s, forced_gpu_class(s), generate_profile(seed=s).gpu.class_tier)
        for s in range(_SWEEP)
        if generate_profile(seed=s).gpu.class_tier != forced_gpu_class(s)
    ]
    assert not bad, (
        f"{len(bad)} of {_SWEEP} seeds build a bundle for one GPU class while "
        f"exposing a persona from another, e.g. {bad[:3]} (seed, persona class, "
        f"bundle class). Every parameter the service cross-checks against the "
        f"renderer comes from that bundle")


def test_the_bare_call_and_the_explicit_call_agree():
    """The two spellings the five call sites used. They must now be the same
    call - that is the whole content of the fix."""
    for seed in range(200):
        bare = translate_profile_to_prefs(generate_profile(seed=seed))
        explicit = translate_profile_to_prefs(
            generate_profile(seed, fixed_gpu_class=forced_gpu_class(seed)))
        differing = sorted(k for k in set(bare) | set(explicit)
                           if bare.get(k) != explicit.get(k))
        assert not differing, (
            f"seed {seed}: omitting fixed_gpu_class still changes {differing}. "
            f"Two call sites omitted it and three did not")


def test_the_exposed_renderer_is_the_personas_on_the_bare_call_too():
    """Ties the two halves together: the prefs carry the persona's renderer, and
    the test above says the bundle matches that persona's class."""
    for seed in (0, 1, 7, 42, 123, 1234):
        prefs = translate_profile_to_prefs(generate_profile(seed=seed))
        assert prefs["zoom.stealth.webgl.renderer"] == select_persona(seed)["renderer"]


# ── the default must not swallow the overrides ────────────────────────────

@pytest.mark.parametrize("tier", ["low_end", "mid_range", "high_end"])
def test_an_explicit_class_pin_still_wins(tier):
    """Pinning is a documented feature and it outranks the default. A default
    that could not be overridden would silently ignore `pin`."""
    assert generate_profile(7, pin={"gpu.class_tier": tier}).gpu.class_tier == tier


@pytest.mark.parametrize("tier", ["low_end", "high_end"])
def test_an_explicit_fixed_gpu_class_still_wins(tier):
    assert generate_profile(7, fixed_gpu_class=tier).gpu.class_tier == tier


def test_a_pin_outranks_fixed_gpu_class():
    """The documented precedence, unchanged: pin, then fixed_gpu_class, then the
    seed's persona."""
    p = generate_profile(7, pin={"gpu.class_tier": "high_end"},
                         fixed_gpu_class="low_end")
    assert p.gpu.class_tier == "high_end"


def test_the_profile_is_still_a_pure_function_of_the_seed():
    """The default is derived from the seed, so determinism must be untouched -
    the property every other guarantee in this package rests on."""
    for seed in (0, 42, 999):
        a, b = generate_profile(seed=seed), generate_profile(seed=seed)
        assert translate_profile_to_prefs(a) == translate_profile_to_prefs(b)
