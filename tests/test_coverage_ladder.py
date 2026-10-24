"""The text coverage ladder is looked up from the declared ClearType key ([B361]).

Until 50.48.0 the core declared ClearType gamma 2200 and, beside it, a ladder as
a constant of its own; measured on retail Firefox 151, that constant is the one
gamma 1800 draws, so the two described different machines. Now the ladder is a
row of `_fpforge/data/coverage_ladders.json`, keyed by (gamma, contrast) and
generated from captures (no formula reproduces the bytes), and a key without a
measured row is refused at birth.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from invisible_core._fpforge import profile as P
from invisible_core._fpforge.profile import generate_profile
from invisible_core.prefs import translate_profile_to_prefs

_DATA = Path(P.__file__).resolve().parent / "data" / "coverage_ladders.json"


def _prefs(pin=None):
    return translate_profile_to_prefs(generate_profile(42, pin=pin),
                                      timezone="America/New_York")


def test_every_measured_ladder_has_seventeen_levels_from_0_to_255():
    """Sixteen coverage levels and the background, as DirectWrite leaves them."""
    assert P.COVERAGE_LADDERS
    for key, ladder in P.COVERAGE_LADDERS.items():
        assert len(ladder) == 17, (key, ladder)
        assert ladder[0] == 0 and ladder[-1] == 255, (key, ladder)
        assert list(ladder) == sorted(set(ladder)), (key, ladder)


def test_the_table_says_it_is_generated_and_from_what():
    doc = json.loads(_DATA.read_bytes())
    assert "never edited by hand" in doc["about"]
    assert doc["source"]["build_id"] and doc["source"]["captures"] == len(doc["ladders"])
    assert "do not edit by hand" in doc["generator"]


def test_the_declared_key_is_an_untuned_windows_machine():
    """Gamma 1800, contrast 100: what retail Firefox 151 draws on a Windows
    machine whose ClearType was never tuned. 2200 is DirectWrite's API default,
    and declaring it moved the text away from retail's (1707 differing pixels
    against 1527 at 1800, on the canvas bench)."""
    assert (P.FONT_CLEARTYPE_GAMMA, P.FONT_CLEARTYPE_CONTRAST) == (1800, 100)


def test_the_declared_key_has_a_measured_row_and_the_profile_carries_it():
    assert (P.FONT_CLEARTYPE_GAMMA, P.FONT_CLEARTYPE_CONTRAST) in P.COVERAGE_LADDERS
    font = generate_profile(42).font
    assert font.alpha_ladder == P.COVERAGE_LADDERS[(font.cleartype_gamma,
                                                    font.cleartype_contrast)]


def test_the_emitted_ladder_is_the_row_of_the_emitted_gamma_and_contrast():
    """Judged at the prefs the engine reads, not at the Profile: the three
    values a page's text depends on describe one machine."""
    prefs = _prefs()
    key = (prefs["gfx.font_rendering.cleartype_params.gamma"],
           prefs["gfx.font_rendering.cleartype_params.enhanced_contrast"])
    ladder = tuple(int(v) for v in prefs["zoom.stealth.text.coverage_ladder"].split(","))
    assert ladder == P.COVERAGE_LADDERS[key], (key, ladder)


def test_a_pinned_gamma_takes_its_own_ladder():
    drawn = generate_profile(42).font
    other = next(g for g, c in sorted(P.COVERAGE_LADDERS)
                 if c == drawn.cleartype_contrast and g != drawn.cleartype_gamma)
    font = generate_profile(42, pin={"font.cleartype_gamma": other}).font
    assert font.alpha_ladder == P.COVERAGE_LADDERS[(other, drawn.cleartype_contrast)]
    assert font.alpha_ladder != P.COVERAGE_LADDERS[(drawn.cleartype_gamma,
                                                    drawn.cleartype_contrast)]


@pytest.mark.parametrize("pin", [{"font.cleartype_gamma": 2000},
                                 {"font.cleartype_contrast": 75},
                                 {"font.cleartype_gamma": 1800, "font.cleartype_contrast": 101}])
def test_a_key_that_was_never_measured_is_refused(pin):
    with pytest.raises(ValueError, match="no measured coverage ladder"):
        generate_profile(42, pin=pin)


def test_a_pinned_ladder_still_wins():
    """How a caller measures the unsnapped edge: an empty ladder disables the snap."""
    prefs = _prefs({"font.alpha_ladder": ()})
    assert prefs["zoom.stealth.text.coverage_ladder"] == ""


def test_the_text_quality_is_declared_and_only_one_the_engine_reads():
    assert _prefs()["zoom.stealth.text.system_quality"] == P.FONT_SYSTEM_TEXT_QUALITY == 5
    for quality in P.SYSTEM_TEXT_QUALITIES:
        assert _prefs({"font.system_text_quality": quality})[
            "zoom.stealth.text.system_quality"] == quality
    with pytest.raises(ValueError, match="system_text_quality"):
        generate_profile(42, pin={"font.system_text_quality": 3})


#: The prefs that decide how a glyph edge is drawn. A persona's own prefs are
#: written over the emitted ones, so one carrying any of these would replace
#: the table's row without a word.
_TEXT_RASTER = ("zoom.stealth.text.", "gfx.font_rendering.")


def test_no_persona_in_the_pool_carries_a_text_raster_pref():
    """The explicit ladder pin is a MEASURING escape and only that: in what this
    package ships, the table is the one source of the ladder."""
    from invisible_core._webgl_personas import _gpu_pool
    carrying = sorted({(p["renderer"], k) for p in _gpu_pool()
                       for k in p.get("prefs", {}) if k.startswith(_TEXT_RASTER)})
    assert not carrying, carrying


def test_every_seed_emits_the_row_of_the_key_it_declares():
    """Over many identities, persona included, the ladder a page's text is
    snapped to is the row of the gamma and contrast the same session declares."""
    for seed in range(0, 2000, 37):
        prefs = translate_profile_to_prefs(generate_profile(seed), timezone="America/New_York")
        key = (prefs["gfx.font_rendering.cleartype_params.gamma"],
               prefs["gfx.font_rendering.cleartype_params.enhanced_contrast"])
        ladder = tuple(int(v) for v in prefs["zoom.stealth.text.coverage_ladder"].split(","))
        assert ladder == P.COVERAGE_LADDERS[key], (seed, key)
