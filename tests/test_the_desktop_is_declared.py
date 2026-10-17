"""What the persona's Windows desktop decides is declared, the same on both
builds (28-valori-dell-host.md, group A, phase 1 of "tutto su core").

Each value here had an engine default that asked the HOST or differed between
our Windows and our Linux build, measured on three arms - retail 151 on
Windows 11, our Windows build, our Linux build - on 2026-10-17. The values are
retail's. The TYPE of each one is checked against the engine by
tests/test_pref_types.py; this file says which value, and that the platform
the core runs on does not move it.
"""
from __future__ import annotations

import pytest

from invisible_core import prefs as P
from invisible_core._fpforge import generate_profile

pytestmark = pytest.mark.unit

TZ = "America/New_York"

#: The declared desktop, as retail 151 on Windows 11 answers it.
EXPECTED = {
    "layout.frame_rate": 60,                              # A1
    "ui.useOverlayScrollbars": 1,                          # A2
    "widget.non-native-theme.scrollbar.style": 5,          # A2, Windows 11
    "ui.systemScrollbarSize": 17,                          # A2, SM_CXVSCROLL
    "ui.dragThresholdX": 3,                                # A3
    "ui.dragThresholdY": 3,                                # A3
    "ui.scrollToClick": 0,                                 # A5
    "browser.display.document_color_use": 0,               # A6
    "dom.forms.select.customstyling": True,                # A6
    "gfx.color_management.force_srgb": True,               # A8
    "security.osclientcerts.autoload": False,              # A11
    "layout.word_select.eat_space_to_next_word": True,     # A13
    "ui.textScaleFactor": 100,                             # A17
    "widget.non-native-theme.use-theme-accent": False,     # A4
}


def _prefs(monkeypatch, platform, seed=42, **pin):
    monkeypatch.setattr(P.sys, "platform", platform)
    return P.translate_profile_to_prefs(generate_profile(seed, pin=pin or None),
                                        timezone=TZ)


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_the_desktop_is_retail_s_on_both_builds(monkeypatch, platform):
    prefs = _prefs(monkeypatch, platform)
    got = {k: prefs.get(k, "<absent>") for k in EXPECTED}
    assert got == EXPECTED


def test_the_two_builds_declare_the_same_desktop(monkeypatch):
    """Rule 7-ter: the same persona does the same thing on both builds. The
    known-bad this catches is any of these written inside a platform branch,
    which is where most of them came from in the engine."""
    for seed in (0, 42, 1561645783):
        win = _prefs(monkeypatch, "win32", seed)
        lin = _prefs(monkeypatch, "linux", seed)
        assert {k: win[k] for k in EXPECTED} == {k: lin[k] for k in EXPECTED}, seed


def test_the_three_form_control_fonts_are_the_dialog_font(monkeypatch):
    """A7: retail answers `getComputedStyle(input).fontFamily` with
    "MS Shell Dlg 2"; the other seven system fonts are the shell font."""
    prefs = _prefs(monkeypatch, "win32")
    for element in ("-moz-button", "-moz-field", "-moz-list"):
        assert prefs["ui.font." + element] == "MS Shell Dlg 2", element
    for element in ("caption", "icon", "menu", "message-box", "small-caption",
                    "status-bar", "-moz-pull-down-menu"):
        assert prefs["ui.font." + element] == "Segoe UI", element


@pytest.mark.parametrize("pin,pref,value", [
    ({"screen.refresh_hz": 144}, "layout.frame_rate", 144),
    ({"hardware.overlay_scrollbars": False}, "ui.useOverlayScrollbars", 0),
    ({"font.dialog_family": "Tahoma"}, "ui.font.-moz-field", "Tahoma"),
])
def test_the_desktop_fields_are_pinnable_like_every_other_surface(monkeypatch, pin, pref, value):
    assert _prefs(monkeypatch, "win32", **pin)[pref] == value


def test_a_caller_overlay_still_wins_over_the_desktop(monkeypatch):
    monkeypatch.setattr(P.sys, "platform", "win32")
    prefs = P.translate_profile_to_prefs(generate_profile(42), timezone=TZ,
                                         extra_prefs={"layout.frame_rate": 75})
    assert prefs["layout.frame_rate"] == 75
