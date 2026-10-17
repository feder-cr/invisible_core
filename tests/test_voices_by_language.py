"""The voices a session declares are those of its language (B315).

A machine that browses in Italian has Italian voices, and an it-IT session that
declared only the five American ones contradicted itself in one
`speechSynthesis.getVoices()`. The table holds only what was measured on a real
Windows: the en-US set, and the set of an en-US Windows with Italian added
(measured 2026-10-15 on retail Firefox 151). A language with no measurement
keeps the en-US set, as every session did before.
"""
from __future__ import annotations

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core._fpforge.profile import VOICES, VOICES_IT, voices_for
from invisible_core.prefs import translate_profile_to_prefs

pytestmark = pytest.mark.unit

PREF = "zoom.stealth.voices.list"


def _names(voices):
    return [v.split("|")[0].split(" - ")[0] for v in voices.split(",")]


def test_the_italian_set_is_the_measured_one_in_its_order():
    assert _names(VOICES_IT) == [
        "Microsoft David", "Microsoft Mark", "Microsoft Zira",
        "Microsoft Cosimo", "Microsoft Elsa",
        "Microsoft David Desktop", "Microsoft Zira Desktop", "Microsoft Elsa Desktop"]
    rows = [v.split("|") for v in VOICES_IT.split(",")]
    assert {r[2] for r in rows} == {"0"}, "Gecko's SAPI backend makes no voice the default"
    assert {r[3] for r in rows} == {"1"}
    assert {r[1] for r in rows} == {"en-US", "it-IT"}


@pytest.mark.parametrize("locale,expected", [
    ("it-IT", VOICES_IT), ("it", VOICES_IT), ("it-CH", VOICES_IT),
    ("en-US", VOICES), ("en-GB", VOICES),
    # Not measured: the en-US set, as before 2026-10-16, never an invented one.
    ("de-DE", VOICES), ("fr-FR", VOICES),
])
def test_a_session_declares_the_voices_of_its_language(locale, expected):
    prefs = translate_profile_to_prefs(generate_profile(seed=42), locale=locale, timezone="America/New_York")
    assert prefs[PREF] == expected
    assert voices_for(locale) == expected


def test_an_explicit_pin_wins_over_the_language():
    pinned = "Microsoft Hedda - German (Germany)|de-DE|0|1"
    profile = generate_profile(seed=42, pin={"hardware.voices": pinned})
    assert translate_profile_to_prefs(profile, locale="it-IT", timezone="America/New_York")[PREF] == pinned


@pytest.mark.parametrize("locale", ["en-US", "it-IT", "ja-JP"])
def test_the_list_is_never_empty(locale):
    # The engine's declaration gate refuses an empty list rather than read the
    # operating system's voice registry.
    assert translate_profile_to_prefs(generate_profile(seed=7), locale=locale, timezone="America/New_York")[PREF]
