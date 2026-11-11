# The network geolocation URL is the engine's: its default is empty on every OS
# (a STEALTH-HOOK on Mozilla's own declaration in all.js), so the core declares
# nothing under geo.provider.network.url. A pref is declared once; the position a
# page reads is the core's geolocation override, never a lookup, and `geo.enabled`
# stays True so that `navigator.geolocation` exists.
import pathlib
import sys

import pytest

import invisible_core.prefs as prefs_module
from invisible_core._fpforge import generate_profile
from invisible_core.prefs import translate_profile_to_prefs


@pytest.mark.unit
@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
@pytest.mark.parametrize("seed", [1, 42, 20260811])
def test_no_persona_declares_a_geo_provider_network_url(monkeypatch, platform, seed):
    monkeypatch.setattr(sys, "platform", platform)
    prefs = translate_profile_to_prefs(generate_profile(seed), timezone="Europe/Rome")
    assert [k for k in prefs if k.startswith("geo.provider.network.url")] == []
    assert prefs["geo.enabled"] is True


@pytest.mark.unit
def test_the_source_has_no_such_key():
    source = pathlib.Path(prefs_module.__file__).read_text(encoding="utf-8")
    assert '"geo.provider.network.url' not in source
