"""Every persona types on the keyboard a real Windows install gives its language and country.

⛔ KNOWN-BAD. Seven layouts and a fixed language map: a Dutch persona typed on
the US keyboard (Windows installs US-International for nl-NL), a German one in
Switzerland on the German keyboard (Swiss German there), a Japanese one on US
(JIS under the IME), and every language outside the seven - Polish, Czech,
Turkish, Russian, Greek, Hebrew, Arabic... - on US. The tables were also read
through the Windows API of the machine that generated them.

Now: Microsoft's table of default input profiles decides the keyboard from
(language, country), then language, then the country's main language, then US;
119 layouts read from the bytes of pinned Windows 11 files, no API.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from invisible_core import _locale
from invisible_core.juggler.keyboard import closure_for, layout_for
from invisible_core.juggler.keylayouts_windows import LAYOUT_NAMES, LAYOUTS, LOCALE_KLID

pytestmark = pytest.mark.unit
ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("langs, region, klid", [
    (["nl"], "NL", "00020409"),        # United States-International
    (["de"], "CH", "00000807"),        # Swiss German
    (["fr"], "CA", "00001009"),        # Canadian French
    (["es-AR"], "AR", "0000080A"),     # Latin American
    (["pt-PT"], "PT", "00000816"),     # Portuguese
    (["pl"], "PL", "00000415"),        # Polish (Programmers)
    (["ja"], "JP", "00000411"),        # JIS, under the IME
    (["ko"], "KR", "00000412"),        # Korean, under the IME
])
def test_language_and_country_pick_the_windows_default(langs, region, klid):
    assert layout_for(langs, region) == klid, LAYOUT_NAMES.get(layout_for(langs, region))


@pytest.mark.parametrize("langs, region, klid", [
    (["ast"], "ES", "0000040A"),   # Asturian in Spain: the Spanish keyboard
    (["sc"], "IT", "00000410"),    # Sardinian in Italy: the Italian keyboard
    (["szl"], "PL", "00000415"),   # Silesian in Poland: the Polish keyboard
])
def test_a_language_without_a_windows_profile_takes_its_country_s(langs, region, klid):
    assert layout_for(langs, region) == klid


def test_no_language_no_country_is_the_us_keyboard():
    assert layout_for(["eo"]) == "00000409"
    assert layout_for(["xx"], None) == "00000409"


def test_every_firefox_build_resolves_to_a_layout_with_keys():
    for build in sorted(_locale._FIREFOX_BUILDS):
        klid = layout_for([build])
        assert klid in LAYOUTS and LAYOUTS[klid]["keys"], build
        assert closure_for(klid)["Enter"]["code"] == "Enter", build


def test_every_tag_in_the_table_points_at_a_shipped_layout():
    assert all(k in LAYOUTS for k in LOCALE_KLID.values())


def test_the_generator_reads_bytes_and_never_the_machine():
    """No Windows API, no layout loaded: the tables cannot depend on the host
    that regenerates them. Every file is checked against a pinned SHA-256."""
    src = (ROOT / "scripts" / "gen_windows_keylayouts.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    for banned in ("ctypes", "WinDLL", "ToUnicodeEx", "LoadKeyboardLayout", "MapVirtualKey", "winreg"):
        assert banned not in code, banned
    assert "PINS[" in code and "sha256" in code
