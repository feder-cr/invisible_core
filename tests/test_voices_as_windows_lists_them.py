"""The declared speechSynthesis voices are the ones retail Windows lists, in
its order, and none of them is the default.

Measured on retail Windows Firefox 151 on 2026-10-13 (`speechSynthesis.
getVoices()`): David, Mark, Zira, then David Desktop and Zira Desktop, every
one `default` false and `localService` true. Gecko's SAPI backend never calls
SetDefaultVoice (dom/media/webspeech/synth/windows/SapiService.cpp). Until
43.39.0 the profile made David the default and listed Zira before Mark, and a
page reads both from `speechSynthesis.getVoices()`.
"""
from __future__ import annotations

import pytest

from invisible_core._fpforge.profile import VOICES

pytestmark = pytest.mark.unit


def _voices():
    return [tuple(v.split("|")) for v in VOICES.split(",")]


def test_no_declared_voice_is_the_default():
    assert [v[2] for v in _voices()] == ["0"] * 5


def test_the_voices_come_in_the_order_windows_lists_them():
    assert [v[0].split(" - ")[0] for v in _voices()] == [
        "Microsoft David", "Microsoft Mark", "Microsoft Zira",
        "Microsoft David Desktop", "Microsoft Zira Desktop"]
    assert {v[1] for v in _voices()} == {"en-US"}
    assert {v[3] for v in _voices()} == {"1"}
