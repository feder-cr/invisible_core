"""The session's first enumerateDevices() time is declared, not the host's
([B300]).

With fake cameras and microphones the only part of an enumeration that went
to the machine was its speakers, through the host's audio stack, and the first
enumeration took the host's time: 287-351 ms on our Windows build, 15-38 on
our Linux one, 249-278 on retail Windows (251-255 with fake devices too). The
core declares it per session (`zoom.stealth.media.enumeration_ms`) and the
engine answers after it without asking the host, from firefox-45.
"""
from __future__ import annotations

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import translate_profile_to_prefs

pytestmark = pytest.mark.unit

PREF = "zoom.stealth.media.enumeration_ms"


def _prefs(seed, **pin):
    return translate_profile_to_prefs(generate_profile(seed, pin=pin or None))


def test_the_time_is_one_machine_s_range_and_one_seed_keeps_it():
    values = [_prefs(seed)[PREF] for seed in range(1, 120)]
    assert all(240 <= v <= 290 for v in values), values
    assert len(set(values)) > 20, "every session the same time is a constant"
    assert _prefs(7)[PREF] == _prefs(7)[PREF]


def test_with_real_devices_the_enumeration_is_firefox_s_own():
    assert _prefs(7, **{"hardware.fake_media_devices": False})[PREF] == 0


def test_the_time_can_be_pinned_as_the_other_hardware_fields():
    assert _prefs(7, **{"hardware.media_enumeration_ms": 260})[PREF] == 260
