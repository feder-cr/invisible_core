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
    ({"desktop.overlay_scrollbars": False}, "ui.useOverlayScrollbars", 0),
    ({"desktop.text_scale": 125}, "ui.textScaleFactor", 125),
    ({"desktop.palette": "ui.highlight|#112233|#445566"}, "ui.highlight.dark", "#445566"),
    ({"font.dialog_family": "Tahoma"}, "ui.font.-moz-field", "Tahoma"),
])
def test_the_desktop_fields_are_pinnable_like_every_other_surface(monkeypatch, pin, pref, value):
    assert _prefs(monkeypatch, "win32", **pin)[pref] == value


def test_a_caller_overlay_still_wins_over_the_desktop(monkeypatch):
    monkeypatch.setattr(P.sys, "platform", "win32")
    prefs = P.translate_profile_to_prefs(generate_profile(42), timezone=TZ,
                                         extra_prefs={"layout.frame_rate": 75})
    assert prefs["layout.frame_rate"] == 75


def test_both_builds_write_the_same_theme_and_desktop_byte_for_byte(monkeypatch):
    """Every pref the two desktop sections write, compared whole on both
    platforms - values AND types - so a branch added inside either one is
    caught, not only on the keys listed above."""
    for seed, pin in ((42, {}), (7, {"dark_theme": True}),
                      (0, {"desktop.overlay_scrollbars": False})):
        written = {}
        for platform in ("win32", "linux"):
            monkeypatch.setattr(P.sys, "platform", platform)
            prefs = {}
            profile = generate_profile(seed, pin=pin)
            P._apply_theme(prefs, profile)
            P._apply_desktop(prefs, profile)
            written[platform] = {k: (type(v).__name__, v) for k, v in prefs.items()}
        assert written["win32"] == written["linux"], seed


def test_the_desktop_defaults_are_their_only_copy():
    """The group is seeded from its dataclass defaults and nothing else: a
    profile without pins carries exactly `DesktopProfile()`, and no raw key of
    the group is also injected by the sampler, which would win over it."""
    from invisible_core._fpforge import _sampler
    from invisible_core._fpforge.profile import DesktopProfile, _DESKTOP_RAW

    assert generate_profile(42).desktop == DesktopProfile()
    assert not set(_DESKTOP_RAW.values()) & set(_sampler._LOCKED)


@pytest.mark.parametrize("record", [
    "ui.highlight|#112233",            # two parts
    "highlight|#112233|",              # not a ui. name
    "ui.highlight||#445566",           # no light value
])
def test_a_malformed_palette_record_is_refused(monkeypatch, record):
    """A pinned palette with a typo would otherwise lose that colour to the
    host in silence."""
    with pytest.raises(ValueError, match="desktop.palette line 1"):
        _prefs(monkeypatch, "win32", **{"desktop.palette": record})
