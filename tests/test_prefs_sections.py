"""The prefs pipeline: each section on its own, and the ORDER between them.

`translate_profile_to_prefs` was 203 lines - every spoof this project ships
leaves through that one dict. It is fourteen named steps now, and this file
covers what the split makes reachable: each section in isolation, and the two
ordering rules that are a contract rather than an accident.

THE SPLIT ITSELF was verified by recording the output first (the lesson from
2026-07-26 section 5, where splitting a stream generator changed it twice
through float multiplication not being associative). 400 profiles across four
locales, four timezones, three overlay shapes and both `virtual_display` values,
hashed before and after: identical. `test_prefs.py` next door still covers the
whole dict; this covers the seams the split created.
"""
from __future__ import annotations

import sys

import pytest

from invisible_core import prefs as P
from invisible_core._fpforge import generate_profile

pytestmark = pytest.mark.unit


@pytest.fixture()
def profile():
    return generate_profile(42)


# ------------------------------------------------------- the ordering rules

def test_the_caller_overlay_can_override_anything_the_pipeline_set(profile):
    """It runs LAST, and that is the contract the argument is documented with."""
    plain = P.translate_profile_to_prefs(profile)
    key = "zoom.stealth.hw_concurrency"
    assert key in plain
    got = P.translate_profile_to_prefs(profile, extra_prefs={key: 999})
    assert got[key] == 999


def test_the_caller_overlay_can_delete_a_pref_entirely(profile):
    """`None` is a sentinel meaning "remove this key", which is different from
    setting it to "" - for `general.useragent.override` an empty string means a
    literally empty UA."""
    key = "zoom.stealth.hw_concurrency"
    got = P.translate_profile_to_prefs(profile, extra_prefs={key: None})
    assert key not in got


@pytest.mark.parametrize("platform,table", [
    ("linux", "_LINUX_XVFB_WORKAROUNDS"),
    ("win32", "_WIN_VIRT_DESKTOP_WORKAROUNDS"),
])
def test_the_platform_workarounds_never_take_a_choice_away(
        monkeypatch, platform, table):
    """`setdefault`, so a section above always wins. Anything else and a
    workaround silently overrides a sampled value on one platform only - a bug
    that reproduces on one developer's machine and nowhere else.

    BOTH branches, with the platform patched. The first version of this ran on
    the host platform only, and a mutation flipping the LINUX branch to plain
    assignment SURVIVED the whole suite on Windows - the exact "reproduces
    nowhere you are looking" shape the test is about. Measured: with
    `sys.platform` left alone, 1 of the 2 mutations was caught; patched, both.
    """
    monkeypatch.setattr(P.sys, "platform", platform)
    sentinel = object()
    prefs = {key: sentinel for key in getattr(P, table)}
    assert prefs, f"{table} is empty; this test would pass over nothing"

    P._apply_platform_workarounds(prefs, virtual_display=True)

    assert all(v is sentinel for v in prefs.values()), (
        f"a {platform} workaround overwrote a value the pipeline had already "
        f"chosen")

    # And it DOES apply them when nothing chose first, or the parametrisation
    # above would pass against a function that does nothing at all.
    fresh = {}
    P._apply_platform_workarounds(fresh, virtual_display=True)
    assert set(fresh) == set(getattr(P, table))


def test_the_pipeline_runs_the_overlay_last():
    """Read off the source, because the property is the ORDER and the order is
    only visible as a sequence of calls."""
    import ast
    import inspect

    src = inspect.getsource(P.translate_profile_to_prefs)
    tree = ast.parse(src.lstrip())
    calls = [n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id.startswith("_apply_")]
    assert calls[-1] == "_apply_caller_overlay", (
        f"the caller overlay is not last; the pipeline ends with {calls[-1]!r}, "
        f"so a user override can be silently overwritten by whatever follows it")
    assert calls[-2] == "_apply_platform_workarounds"
    assert calls[0] == "_apply_gpu_persona", (
        "the persona must be applied first: _apply_extension_lists is told "
        "whether one was, and everything else may overwrite what it set")


# ------------------------------------------------------------ the sections

def test_the_canvas_noise_mask_is_a_constant_not_a_branch(profile):
    """It was `_renderer_lo = "intel"` then `if "intel" in _renderer_lo`, which
    reads as a per-vendor choice and is a constant compared against itself: the
    1/8 branch has never executed. Measured across 300 seeds before the change,
    every profile got 15."""
    seen = {P.translate_profile_to_prefs(generate_profile(s))
            ["zoom.stealth.canvas.noise_skip_mask"] for s in range(200)}
    assert seen == {P._CANVAS_NOISE_SKIP_MASK} == {15}


def test_a_persona_suppresses_the_extension_clearing(profile):
    """With a persona the coherent extension lists were already applied; with
    none, they are cleared so the host-real renderer reports its native set. The
    two are the same decision seen from two ends, which is why the persona is
    passed along rather than recomputed."""
    if sys.platform.startswith("linux"):
        pytest.skip("the extension clearing is a Windows/mac path")
    with_persona = {}
    P._apply_extension_lists(with_persona, persona={"prefs": {}})
    assert with_persona == {}

    without = {}
    P._apply_extension_lists(without, persona=None)
    assert without == {"zoom.stealth.webgl.extensions": "",
                       "zoom.stealth.webgl2.extensions": ""}


def test_the_locale_defaults_to_en_US_and_normalises_underscores():
    prefs = {}
    P._apply_locale(prefs, "")
    assert prefs["general.useragent.locale"] == "en-US"

    prefs = {}
    P._apply_locale(prefs, "it_IT")
    assert prefs["general.useragent.locale"] == "it-IT"
    assert prefs["intl.locale.requested"] == "it-IT"


def test_the_locale_override_carries_the_whole_accept_language_list():
    """navigator.languages must stay the desktop-default two elements; the C++
    DidSet takes the primary tag out of this for Intl."""
    prefs = {}
    P._apply_locale(prefs, "fr-FR")
    assert prefs["juggler.locale.override"] == prefs["intl.accept_languages"]
    assert prefs["juggler.locale.override"].startswith("fr-FR")


def test_no_timezone_writes_no_timezone_pref():
    """An empty timezone must leave the pref ABSENT, not set to "". The C++
    chain reads this as the sole source of truth, and an empty override is not
    the same as no override."""
    prefs = {}
    P._apply_timezone(prefs, "")
    assert "juggler.timezone.override" not in prefs
    P._apply_timezone(prefs, "Europe/Rome")
    assert prefs["juggler.timezone.override"] == "Europe/Rome"


def test_the_webrtc_host_ip_is_seed_derived_and_looks_like_a_home_router(profile):
    import ipaddress

    seen = set()
    for seed in range(200):
        prefs = {}
        P._apply_webrtc_host_ip(prefs, generate_profile(seed))
        ip = prefs["zoom.stealth.webrtc.host_ip"]
        addr = ipaddress.IPv4Address(ip)
        assert addr in ipaddress.IPv4Network("192.168.0.0/16"), ip
        # Neither octet may be 0 or 255: a synthetic candidate on a network or
        # broadcast address is not a host address a real router hands out.
        a, b = ip.split(".")[2:]
        assert 1 <= int(a) <= 254 and 1 <= int(b) <= 254, ip
        seen.add(ip)
    assert len(seen) > 150, f"only {len(seen)} distinct LAN IPs across 200 seeds"


def test_the_same_seed_gives_the_same_host_ip(profile):
    a, b = {}, {}
    P._apply_webrtc_host_ip(a, profile)
    P._apply_webrtc_host_ip(b, generate_profile(42))
    assert a == b, "the synthetic ICE candidate must be stable per session"


def test_a_light_theme_brings_the_windows_colour_palette(profile):
    dark, light = {}, {}

    class _P:
        dark_theme = True
    P._apply_theme(dark, _P())
    assert dark["ui.systemUsesDarkTheme"] == 1
    assert not (set(dark) & set(P._WIN_LIGHT_COLORS))

    _P.dark_theme = False
    P._apply_theme(light, _P())
    assert light["ui.systemUsesDarkTheme"] == 0
    assert set(P._WIN_LIGHT_COLORS) <= set(light)


def test_the_codec_prefs_use_names_the_binary_actually_reads(profile):
    """`media.mediasource.{webm,mp4}.enabled` do not exist in Firefox. Setting a
    name the binary never reads is a no-op, and the per-seed codec diversity it
    was meant to produce was fictional - every identity reported the same codec
    surface."""
    prefs = {}
    P._apply_codecs(prefs, profile)
    assert "media.webm.enabled" in prefs and "media.mp4.enabled" in prefs
    assert not any("mediasource" in k for k in prefs), sorted(prefs)
