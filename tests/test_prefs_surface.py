"""What `translate_profile_to_prefs` actually emits.

THIS FUNCTION HAD NO TEST. Measured before writing this file: `prefs.py` sat at
15% statement coverage and `translate_profile_to_prefs` executed zero lines in
the whole suite - its only appearance was a `monkeypatch.setattr` replacing it
(`tests/test_launch.py:28`). Every `zoom.stealth.*` pref the patched binary
reads is produced here, so the body could be replaced with `return {}` and the
suite stayed green while shipping a browser with no spoofing at all.

Two failures had already happened behind that gap, both found by reading the
Firefox source rather than by any test:

  * `media.mediasource.{webm,mp4}.enabled` are not Firefox pref names. They are
    absent from `modules/libpref/init/StaticPrefList.yaml`, so the per-seed
    codec sampling was inert and every identity reported the same codec surface
    - an invariant across the fleet, which is the opposite of the intent;
  * `zoom.stealth.screen.avail_{width,height}` are undeclared AND ignored:
    `nsScreen::GetAvailRect` derives the available rect from
    `zoom_stealth_screen_width/height` minus a fixed taskbar.

So the tests here are about NAMES and REACHABILITY, not about values. A pref
whose name the binary does not read is indistinguishable, from Python, from one
that works perfectly.
"""
from __future__ import annotations

import os
import pathlib

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import translate_profile_to_prefs

pytestmark = pytest.mark.unit


def _prefs(seed: int = 42, **kw):
    return translate_profile_to_prefs(generate_profile(seed), **kw)


# ── the function runs at all, and produces the surface it claims ────────────

def test_it_emits_the_stealth_namespace_rather_than_an_empty_dict():
    prefs = _prefs()
    stealth = [k for k in prefs if k.startswith("zoom.stealth.")]
    assert len(stealth) >= 20, (
        f"only {len(stealth)} zoom.stealth.* prefs emitted: {sorted(stealth)}")


@pytest.mark.parametrize("name", [
    # One per subsystem the binary gates on. If any of these stops being
    # emitted, that spoof is silently off and nothing else in the suite knows.
    "zoom.stealth.screen.width",
    "zoom.stealth.screen.height",
    "zoom.stealth.webgl.renderer",
    "zoom.stealth.webgl.vendor",
    "zoom.stealth.fpp.hw_seed",
    "zoom.stealth.audio.max_channel_count",
    "general.platform.override",
    "general.useragent.override",
])
def test_the_load_bearing_prefs_are_present(name):
    assert name in _prefs(), f"{name} is no longer emitted"


def test_the_codec_prefs_use_names_firefox_actually_declares():
    """The bug this file was written after.

    `media.mediasource.webm.enabled` / `.mp4.enabled` do not exist in Firefox,
    so setting them did nothing and the sampled codec diversity never reached a
    page. The real switches are `media.webm.enabled` / `media.mp4.enabled`.
    """
    prefs = _prefs()
    assert "media.webm.enabled" in prefs
    assert "media.mp4.enabled" in prefs
    for dead in ("media.mediasource.webm.enabled", "media.mediasource.mp4.enabled"):
        assert dead not in prefs, (
            f"{dead} is back; it is not a Firefox pref name and setting it is a "
            f"no-op that reads exactly like a working spoof")


def test_the_undeclared_screen_prefs_stay_out():
    """`nsScreen::GetAvailRect` ignores these and derives the rect from
    screen.width/height. Emitting them costs nothing but implies a control that
    does not exist, which is how they survived unnoticed."""
    prefs = _prefs()
    for dead in ("zoom.stealth.screen.avail_width", "zoom.stealth.screen.avail_height"):
        assert dead not in prefs, f"{dead} is back and the binary does not read it"


# ── the property the whole product rests on ────────────────────────────────

def test_the_same_seed_produces_byte_identical_prefs():
    """Seed reproducibility, asserted on the SHIPPED prefs rather than on the
    profile object. Callers rely on it, and the gate that covers it
    (`fppro_consistency.py`) lives in the workbench and needs a browser - so
    inside this package nothing asserted it at all."""
    assert _prefs(7) == _prefs(7)


def test_two_seeds_differ_in_the_fields_that_identify_a_machine():
    """Otherwise every install ships one identity, which is worse than none."""
    a, b = _prefs(1), _prefs(2)
    differing = {k for k in a if a[k] != b.get(k)}
    assert differing, "two seeds produced identical prefs"
    assert any(k.startswith("zoom.stealth.webgl.") for k in differing), (
        f"the GPU persona did not move between seeds: {sorted(differing)[:8]}")


# ── cross-check against the real Firefox source, where it is available ──────

_FF_SRC = pathlib.Path(os.environ.get("STEALTH_FIREFOX_SRC", "C:/ff/source"))


def _names_the_binary_reads() -> set[str]:
    """Every `zoom.stealth.*` string literal anywhere in the Firefox tree.

    NOT just StaticPrefList.yaml. A pref read from JS with
    `getBoolPref(name, default)` needs no static declaration and works fine -
    the first version of this check used the yaml alone and flagged
    `zoom.stealth.debugger.force_detach`, which devtools/server/actors/thread.js
    reads perfectly well. Searching for the literal is the honest definition of
    "the binary reads this".
    """
    import re

    found: set[str] = set()
    pattern = re.compile(r"zoom[._]stealth[._][A-Za-z0-9_.]+")
    for path in _FF_SRC.rglob("*"):
        if path.suffix not in {".cpp", ".h", ".js", ".jsm", ".mjs", ".yaml", ".idl"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "zoom" not in text:
            continue
        for hit in pattern.findall(text):
            # C++ StaticPrefs mangles dots to underscores; normalise both ways.
            found.add(hit.replace("_stealth_", ".stealth.").replace("zoom_", "zoom."))
            found.add(hit)
    return found


@pytest.mark.skipif(
    not (_FF_SRC / "modules" / "libpref" / "init" / "StaticPrefList.yaml").is_file(),
    reason=(
        "no Firefox source tree beside this checkout. This cross-check is a "
        "workbench convenience: the authoritative version runs where the "
        "binary is built. Set STEALTH_FIREFOX_SRC to point at one."
    ),
)
def test_every_stealth_pref_emitted_is_one_the_binary_reads():
    """The check that would have caught every dead-pref bug at the source.

    A `zoom.stealth.*` name the binary never reads is a spoof that silently
    does nothing, and from Python the two are indistinguishable. Four had
    already shipped that way: the two codec names, and screen.dpr and
    webgl.msaa, which appear in NO file of the tree at all.
    """
    readable = _names_the_binary_reads()
    emitted = {k for k in _prefs() if k.startswith("zoom.stealth.")}
    missing = sorted(k for k in emitted if k not in readable
                     and k.replace(".", "_") not in readable)
    assert not missing, (
        "these prefs are emitted but appear nowhere in the engine source, so "
        f"they are no-ops that look like working spoofs: {missing}")
