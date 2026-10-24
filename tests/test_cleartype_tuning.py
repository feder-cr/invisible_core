"""The ClearType tuning a persona draws text with, and the canvas it leaves.

Retail Firefox 151 never changes a pixel between drawing and reading, so the
canvas a page reads is the canvas that was drawn. With the fonts fixed, what
differs between Windows machines is the edge DirectWrite draws, which the
ClearType tuning decides. These tests hold the table that declares it and the
canvas prefs that follow from that decision.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from invisible_core._fpforge import _sampler
from invisible_core._fpforge import profile as P
from invisible_core._fpforge.profile import generate_profile
from invisible_core.prefs import translate_profile_to_prefs

pytestmark = pytest.mark.unit

_TABLE = Path(_sampler.__file__).with_name("data") / "cleartype_tuning.json"


def _rows():
    return json.loads(_TABLE.read_text(encoding="utf-8"))["entries"]


def _prefs(seed=42, pin=None):
    return translate_profile_to_prefs(generate_profile(seed, pin=pin),
                                      timezone="America/New_York")


def test_every_key_in_the_table_was_measured_on_retail():
    keys = [tuple(r["value"]) for r in _rows()]
    assert len(keys) == len(set(keys)), keys
    assert set(keys) <= set(P.COVERAGE_LADDERS), sorted(set(keys) - set(P.COVERAGE_LADDERS))


def test_the_table_is_a_probability_distribution():
    assert abs(sum(float(r["prob"]) for r in _rows()) - 1.0) < 1e-9
    assert all(float(r["prob"]) >= 0 for r in _rows())


def test_the_untuned_machine_is_a_row_and_the_constants_name_it():
    untuned = (P.FONT_CLEARTYPE_GAMMA, P.FONT_CLEARTYPE_CONTRAST)
    assert untuned == (1800, 100)
    assert untuned in [tuple(r["value"]) for r in _rows()]


def test_the_table_says_why_its_frequencies_are_what_they_are():
    doc = json.loads(_TABLE.read_text(encoding="utf-8"))
    assert doc["source"]["frequencies"]
    assert any("retail" in line for line in doc["_comment"])


def test_the_draw_is_its_own_stream_and_deterministic():
    for seed in (0, 1, 42, 123, 98765):
        assert _sampler.derive_cleartype_tuning(seed) == _sampler.derive_cleartype_tuning(seed)
    state = random.getstate()
    _sampler.derive_cleartype_tuning(7)
    assert random.getstate() == state


def test_the_draw_follows_the_table(monkeypatch):
    rows = [{"value": [1800, 100], "prob": 0.5}, {"value": [2200, 50], "prob": 0.5}]
    monkeypatch.setattr(_sampler, "_CLEARTYPE_TUNING", rows)
    drawn = {_sampler.derive_cleartype_tuning(s) for s in range(400)}
    assert drawn == {(1800, 100), (2200, 50)}


def test_a_sampled_key_reaches_the_prefs_with_its_own_ladder(monkeypatch):
    monkeypatch.setattr(_sampler, "_CLEARTYPE_TUNING", [{"value": [1400, 50], "prob": 1.0}])
    prefs = _prefs()
    assert prefs["gfx.font_rendering.cleartype_params.gamma"] == 1400
    assert prefs["gfx.font_rendering.cleartype_params.enhanced_contrast"] == 50
    ladder = tuple(int(v) for v in prefs["zoom.stealth.text.coverage_ladder"].split(","))
    assert ladder == P.COVERAGE_LADDERS[(1400, 50)]


def test_a_pin_still_beats_the_draw(monkeypatch):
    monkeypatch.setattr(_sampler, "_CLEARTYPE_TUNING", [{"value": [1400, 50], "prob": 1.0}])
    font = generate_profile(42, pin={"font.cleartype_gamma": 2200,
                                     "font.cleartype_contrast": 0}).font
    assert (font.cleartype_gamma, font.cleartype_contrast) == (2200, 0)
    assert font.alpha_ladder == P.COVERAGE_LADDERS[(2200, 0)]


def test_no_floor_shadows_the_draw(monkeypatch):
    """A setdefault beside a sampled key is a second source that agrees until
    it does not: moving the table must move the profile."""
    monkeypatch.setattr(_sampler, "_CLEARTYPE_TUNING", [{"value": [1000, 0], "prob": 1.0}])
    font = generate_profile(42).font
    assert (font.cleartype_gamma, font.cleartype_contrast) == (1000, 0)


def test_baseline_protection_is_on_with_only_its_canvas_target():
    prefs = _prefs()
    assert prefs["privacy.baselineFingerprintingProtection"] is True
    assert prefs["privacy.baselineFingerprintingProtection.overrides"] == (
        "-ScreenAvailToResolution,-MaxTouchPointsCollapse")
