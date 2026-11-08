"""The system colours a page reads are Firefox 159 retail on Windows 11's.

Each value was confirmed with REA on the retail xul.dll: the links, Mark,
Accent and Field come from the engine's stand-in table, the dark selection
from GenericDarkColor, the light selection from GetSysColor COLOR_HIGHLIGHT.
Pref colours are CSS colour strings (GetPrefColor -> ComputeColor), so the
dark Highlight carries its alpha: 0x4E/255 = 0.306.
"""
from __future__ import annotations

import pytest

from invisible_core import prefs as P
from invisible_core._fpforge import generate_profile

pytestmark = pytest.mark.unit

#: (pref, light, dark). dark None: the plain pref answers both schemes.
TABLE = [
    ("ui.-moz-hyperlinktext", "#0000EE", "#00CADB"),
    ("ui.-moz-visitedhyperlinktext", "#551A8B", "#FFADFF"),
    ("ui.-moz-activehyperlinktext", "#EE0000", "#FF6666"),
    ("ui.highlight", "#0078D4", "#00DDFF4E"),
    ("ui.selecteditem", "#0078D4", "#00DDFF"),
    ("ui.selecteditemtext", "#FFFFFF", "#2B2A33"),
    ("ui.highlighttext", "#FFFFFF", None),
    ("ui.mark", "#FFFF00", None),
    ("ui.marktext", "#000000", None),
    ("ui.accentcolor", "#0060DF", None),
    ("ui.accentcolortext", "#FFFFFF", None),
    ("ui.-moz-field", "#FFFFFF", "#2B2A33"),
    ("ui.-moz-fieldtext", "#000000", "#FBFBFE"),
]


@pytest.mark.parametrize("seed", [0, 7, 42, 1561645783])
@pytest.mark.parametrize("dark_theme", [False, True])
def test_every_system_colour_is_retail_159(seed, dark_theme):
    prefs = P.translate_profile_to_prefs(
        generate_profile(seed, pin={"dark_theme": dark_theme}),
        timezone="America/New_York")
    for name, light, dark in TABLE:
        assert prefs.get(name) == light, name
        if dark is not None:
            assert prefs.get(name + ".dark") == dark, name
        else:
            # the dark scheme falls back to the plain pref (GetColorFromPref)
            assert name + ".dark" not in prefs or name == "ui.highlighttext", name
