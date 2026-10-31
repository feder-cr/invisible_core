"""The Windows system colour palette, for both colour schemes.

The default of `DesktopProfile.palette`. It lived in `prefs.py` as two dicts
until 49.46.0, a constant the prefs wrote; a value a pref decides is a field of
the Profile (pinnable, inspectable, overridable), and the field needs its
default here, below `prefs.py`, which reads the Profile.
"""
from __future__ import annotations

from typing import Dict

# ──────────────────────────────────────────────────────────────────────
#  System colors - FP Pro probes getComputedStyle(div) with CSS system
#  keywords (ButtonFace, Menu, Highlight, …) and hashes the result into
#  signal s142. On Linux, Firefox resolves these via GTK theme → GTK
#  RGB values diverge from Windows Win32 palette → server-side anomaly
#  even with Windows UA. Declaring the palette retail 151 answers on
#  Windows, for both colour schemes, closes the gap on both builds.
# ──────────────────────────────────────────────────────────────────────

_WIN_LIGHT_COLORS: Dict[str, str] = {
    # Measured against stock Firefox 151 on Windows, 2026-08-09, not chosen.
    # A page resolves every one of these through getComputedStyle, and our Linux
    # build answered GTK's values for all seven: menuhover was rgb(53,132,228)
    # against rgba(0,0,0,0.118), oddtreerow was transparent against white,
    # dialogtext was rgb(46,52,54) against black. Eight colours in one call is a
    # desktop-environment readout, which is a stronger tell than any single one.
    #
    # The names are NOT uniform and that is upstream's doing, not a typo here:
    # nsXPLookAndFeel.cpp spells cellhighlight with a hyphen and menuhover,
    # menuhovertext, menubarhovertext and oddtreerow with an UNDERSCORE. Naming
    # all seven with hyphens left exactly those four unset, and the measurement
    # said so - Linux went from 8 divergences to 4, and the 4 were the 4.
    "ui.-moz-cellhighlight":           "#CECECE",
    # Underscore, like menuhover. Missing until 49.46.0: in the light scheme
    # GTK's default happened to be retail's black, and the dark scheme showed
    # the hole - Linux answered white where retail answers rgb(43,42,51).
    "ui.-moz_cellhighlighttext":       "#000000",
    # F0F0F0. This one value was declared three times in two days, twice from a
    # binary that was not the right judge, and the sequence is worth more than
    # the number.
    #
    #   declared      FFFFFF   no measurement behind it
    #   2026-08-09    F0F0F0   judged by C:/tmp/ff151-win2
    #   2026-08-09    FFFFFF   judged by C:/tmp/retail151, "the signed retail"
    #   2026-08-10    F0F0F0   judged by a binary verified to be both
    #
    # ff151-win2 reports 151.0 and is not retail: Get-AuthenticodeSignature
    # says NotSigned, ProductName is "Nightly", IsPrivateBuild is true. Right
    # version, wrong build. So the second row was rejected - correctly.
    #
    # C:/tmp/retail151 was then downloaded and extracted to be the answer to
    # that, and its application.ini says Version=153.0.3: the extraction had
    # failed and the directory kept what was already in it. Right provenance,
    # wrong version, and the name said 151 both times. So the third row was
    # measured against 153, which genuinely answers white here - the difference
    # is upstream drift between the two majors, which is exactly the thing
    # rule 4's same-major clause exists to keep out of our numbers.
    #
    # The fourth row is C:/tmp/ffjudge-151.0: application.ini Version=151.0
    # BuildID=20260516144017, Authenticode Valid, CN=Mozilla Corporation,
    # ProductName Firefox, IsPrivateBuild false. Both checks, on the same
    # binary, before it was allowed to decide anything. It answers
    # rgb(240,240,240), which means the FIRST correction had the right value
    # from the wrong evidence - and a right answer from a bad judge is still
    # not knowledge, because the next value it decides will be wrong.
    #
    # Measured the same day across all 35 keywords: our Windows build differs
    # from that judge on this one field and no other, and 6 of the 35 do not
    # resolve from content at all. That 1-of-35 is why a wrong judge survives
    # so long - it agrees almost everywhere.
    #
    #   2026-10-31    FFFFFF   judged by retail 159.0, BuildID=20261022150525,
    #                          Authenticode Valid, CN=Mozilla Corporation,
    #                          ProductName Firefox, IsPrivateBuild false
    #
    # Same two checks, a different major. Retail moved to white between 151 and
    # 153 - the drift row three ran into by accident - and 159 keeps it. The
    # 151 value was right for 151; we now declare 159, so the value is 159's.
    "ui.-moz-dialog":                  "#FFFFFF",
    "ui.-moz-dialogtext":              "#000000",
    "ui.-moz_menubarhovertext":        "#15141A",
    "ui.-moz_menuhover":               "#0000001E",
    "ui.-moz_menuhovertext":           "#15141A",
    "ui.-moz_oddtreerow":              "#FFFFFF",
    "ui.activeborder":              "#B4B4B4",
    "ui.activecaption":             "#99B4D1",
    "ui.appworkspace":              "#ABABAB",
    "ui.background":                "#000000",
    "ui.buttonface":                "#F0F0F0",
    "ui.buttonhighlight":           "#FFFFFF",
    "ui.buttonshadow":              "#A0A0A0",
    "ui.buttontext":                "#000000",
    "ui.buttonborder":              "#000000",
    "ui.captiontext":               "#000000",
    "ui.graytext":                  "#6D6D6D",
    "ui.highlight":                 "#0078D7",
    "ui.highlighttext":             "#FFFFFF",
    "ui.inactiveborder":            "#F4F7FC",
    "ui.inactivecaption":           "#BFCDDB",
    "ui.inactivecaptiontext":       "#434E54",
    "ui.infobackground":            "#FFFFE1",
    "ui.infotext":                  "#000000",
    "ui.menu":                      "#F9F9FB",
    "ui.menutext":                  "#000000",
    "ui.scrollbar":                 "#C8C8C8",
    "ui.threeddarkshadow":          "#696969",
    "ui.threedface":                "#F0F0F0",
    "ui.threedhighlight":           "#FFFFFF",
    "ui.threedlightshadow":         "#E3E3E3",
    "ui.threedshadow":              "#A0A0A0",
    "ui.window":                    "#FFFFFF",
    "ui.windowframe":               "#646464",
    "ui.windowtext":                "#000000",
    "ui.mark":                      "#FFFF00",
    "ui.marktext":                  "#000000",
    "ui.accentcolor":                  "#0060DF",
    "ui.accentcolortext":           "#FFFFFF",
    "ui.selecteditem":              "#0078D7",
    "ui.selecteditemtext":          "#FFFFFF",
    "ui.-moz-hyperlinktext":        "#0066CC",
    "ui.-moz-activehyperlinktext":  "#EE0000",
    "ui.-moz-visitedhyperlinktext": "#551A8B",
}

#: The names whose value changes in the DARK scheme, as `ui.<name>.dark`.
#: Measured on retail 151 on Windows 11 on 2026-10-17, a `color-scheme: dark`
#: box against a light one, all 56 keywords. Thirty more change between the
#: schemes (ButtonFace, Canvas, Window...), and none of them belongs here:
#: content answers them from the engine's own stand-ins
#: (sNonNativeThemeStandinColors), not from a pref, and both builds matched
#: retail on every one. These eleven are the ones content reads through a
#: pref; for the rest of the table above the engine falls back from `.dark`
#: to the plain name (GetColorFromPref). The alpha is two hex digits:
#: rgba(0,221,255,0.306) is 0x4E/255.
_WIN_DARK_COLORS: Dict[str, str] = {
    "ui.highlight.dark":              "#00DDFF4E",
    "ui.highlighttext.dark":          "#000000",
    "ui.selecteditem.dark":           "#00DDFF",
    "ui.selecteditemtext.dark":       "#2B2A33",
    "ui.-moz-dialog.dark":            "#23222B",
    "ui.-moz-dialogtext.dark":        "#FBFBFE",
    "ui.-moz-cellhighlight.dark":     "#00DDFF",
    "ui.-moz_cellhighlighttext.dark": "#2B2A33",
    "ui.-moz_menuhover.dark":         "#FFFFFF1E",
    "ui.-moz_menuhovertext.dark":     "#FBFBFE",
    "ui.-moz_oddtreerow.dark":        "#23222B",
}


def palette_text(light: Dict[str, str], dark: Dict[str, str]) -> str:
    """The two tables as the field carries them: one `ui.<name>|<light>|<dark>`
    record per line, the dark part empty where the scheme does not move it."""
    return "\n".join(
        "%s|%s|%s" % (name, value, dark.get(name + ".dark", ""))
        for name, value in light.items())


#: The default of `DesktopProfile.palette`.
PALETTE = palette_text(_WIN_LIGHT_COLORS, _WIN_DARK_COLORS)
