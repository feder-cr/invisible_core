"""The core declares no glyph coverage ladder; gamma and contrast stay declared.

The engine used to snap every A8 text mask to 17 levels from a ladder the core
declared in `zoom.stealth.text.coverage_ladder`. That drew text unlike retail on
strokes, coloured text, opaque canvas above 48 px and emoji (measured on
Windows: with the identity ladder 342/342 cases equal retail, and 0 differences
over 468 comparisons for each of the 12 keys). The engine's idwr text now
carries the edge itself, from the ClearType gamma and enhanced contrast the
core declares, so the ladder is gone and those two prefs are what is left.
"""
from __future__ import annotations

import pytest

from invisible_core import prefs as P
from invisible_core._fpforge import _sampler
from invisible_core._fpforge import generate_profile

pytestmark = pytest.mark.unit

LADDER = "zoom.stealth.text.coverage_ladder"
GAMMA = "gfx.font_rendering.cleartype_params.gamma"
CONTRAST = "gfx.font_rendering.cleartype_params.enhanced_contrast"

#: The 12 keys measured on retail Firefox 151.
GAMMAS = {1000, 1400, 1800, 2200}
CONTRASTS = {0, 50, 100}

SEEDS = (0, 1, 7, 42, 123, 0xB005, 0xC0FFEE, 98765)


def _prefs(monkeypatch, seed, platform):
    monkeypatch.setattr(P.sys, "platform", platform)
    return P.translate_profile_to_prefs(generate_profile(seed), timezone="America/New_York")


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_the_coverage_ladder_is_not_among_the_prefs(monkeypatch, platform):
    for seed in SEEDS:
        assert LADDER not in _prefs(monkeypatch, seed, platform), (seed, platform)


def test_the_profile_has_no_alpha_ladder():
    for seed in SEEDS:
        assert not hasattr(generate_profile(seed).font, "alpha_ladder"), seed


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_gamma_and_contrast_are_declared_within_the_twelve_keys(monkeypatch, platform):
    for seed in SEEDS:
        prefs = _prefs(monkeypatch, seed, platform)
        assert prefs[GAMMA] in GAMMAS, (seed, platform, prefs.get(GAMMA))
        assert prefs[CONTRAST] in CONTRASTS, (seed, platform, prefs.get(CONTRAST))


def test_gamma_and_contrast_follow_the_drawn_tuning_between_seeds(monkeypatch):
    """With a table that spreads its mass over the keys, the declared gamma and
    contrast vary between seeds; the shipped table puts all of it on the
    untuned machine until a share is measured, so the draw is set here."""
    rows = [{"value": [g, c], "prob": 1.0 / 12} for g in sorted(GAMMAS) for c in sorted(CONTRASTS)]
    monkeypatch.setattr(_sampler, "_CLEARTYPE_TUNING", rows)
    for platform in ("win32", "linux"):
        gammas, contrasts = set(), set()
        for seed in range(300):
            prefs = _prefs(monkeypatch, seed, platform)
            assert LADDER not in prefs
            gammas.add(prefs[GAMMA])
            contrasts.add(prefs[CONTRAST])
        assert len(gammas) >= 3, (platform, sorted(gammas))
        assert gammas <= GAMMAS and contrasts <= CONTRASTS, (platform, gammas, contrasts)
