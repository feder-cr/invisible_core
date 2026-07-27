# MOVED FROM invisible_playwright/tests/ ON 2026-07-27.
#
# Every test in this file exercises code in THIS package and reached it through
# a four-line back-compat shim in the wrapper. That is not where coverage for a
# module belongs, and it was not academic: measured on 2026-07-27, six realistic
# one-line breaks in core code SURVIVED the core's own suite and were caught
# only by the wrapper's - `cloak_prefs()` returning {}, SOCKS detection always
# False, the scheme never stripped from a proxy server, `_proxy_is_set` always
# True, the locale always en-US, `get_default_args()` injecting -headless. The
# core's pre-push gate and its publish gate were both green over all six.
#
# `test_no_test_reaches_the_core_through_a_shim` in the wrapper keeps them here.
import re
import sys

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import (
    _accept_language,
    _WIN_LIGHT_COLORS,
    translate_profile_to_prefs,
)


@pytest.mark.unit
def test_translate_includes_gpu_renderer_windows(monkeypatch):
    """On Windows we falsify the GPU to a real-Firefox GPU from the camoufox-derived pool
    (prevalence-weighted; full coherent renderer+vendor+params+extensions). The chosen GPU's
    renderer/vendor are applied verbatim and the renderer is in ANGLE D3D11 wire format."""
    monkeypatch.setattr(sys, "platform", "win32")
    from invisible_core._webgl_personas import select_persona
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p)
    persona = select_persona(42)
    assert prefs["zoom.stealth.webgl.renderer"] == persona["renderer"]
    assert prefs["zoom.stealth.webgl.renderer"].endswith(", D3D11)")
    assert prefs["zoom.stealth.webgl.vendor"] == persona["vendor"]
    assert "Google Inc." in prefs["zoom.stealth.webgl.vendor"]


@pytest.mark.unit
def test_translate_includes_screen():
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p)
    assert prefs["zoom.stealth.screen.width"] == p.screen.width
    assert prefs["zoom.stealth.screen.height"] == p.screen.height


@pytest.mark.unit
def test_translate_is_deterministic_per_seed():
    a = translate_profile_to_prefs(generate_profile(seed=42))
    b = translate_profile_to_prefs(generate_profile(seed=42))
    assert a == b


@pytest.mark.unit
def test_translate_varies_across_seeds():
    a = translate_profile_to_prefs(generate_profile(seed=1))
    b = translate_profile_to_prefs(generate_profile(seed=2))
    assert a != b


@pytest.mark.unit
def test_translate_has_stealth_baseline_constants():
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p)
    assert prefs.get("privacy.resistFingerprinting") is False
    assert "media.peerconnection.enabled" in prefs


# ──────────────────────────────────────────────────────────────────────
#  _accept_language (platform-agnostic)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_accept_language_with_region():
    # AL1
    assert _accept_language("en-US") == "en-US, en"


@pytest.mark.unit
def test_accept_language_no_region():
    # AL2
    assert _accept_language("fr") == "fr"


@pytest.mark.unit
def test_accept_language_underscore_normalized():
    # AL3
    assert _accept_language("pt_BR") == "pt-BR, pt"


# ──────────────────────────────────────────────────────────────────────
#  Platform-specific GPU / MSAA (Windows)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_gpu_renderer_persona_on_windows(monkeypatch):
    # PG2: Windows exposes a validated persona renderer (well-formed ANGLE bucket, NOT empty/native).
    monkeypatch.setattr(sys, "platform", "win32")
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p)
    r = prefs["zoom.stealth.webgl.renderer"]
    assert r and r.startswith("ANGLE (") and r.rstrip().endswith(", D3D11)")
    assert prefs["zoom.stealth.webgl.vendor"].startswith("Google Inc. (")


@pytest.mark.unit
def test_msaa_pinned_to_4_on_windows(monkeypatch):
    # PG4: even when profile.webgl.msaa_samples differs, Windows pins to 4.
    monkeypatch.setattr(sys, "platform", "win32")
    p = generate_profile(seed=42, pin={"webgl.msaa_samples": 8})
    prefs = translate_profile_to_prefs(p)
    assert prefs["webgl.msaa-samples"] == 4
    assert prefs["webgl.msaa-samples"] == 4
    assert prefs["webgl.msaa-force"] is True


# ──────────────────────────────────────────────────────────────────────
#  Canvas noise skip mask (Windows always uses intel path)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_canvas_noise_mask_windows_uses_intel_path(monkeypatch):
    # CN3: on Windows _renderer_lo is hardcoded to "intel" → mask=15.
    monkeypatch.setattr(sys, "platform", "win32")
    p = generate_profile(
        seed=42,
        pin={"gpu.renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D11)"},
    )
    prefs = translate_profile_to_prefs(p)
    assert prefs["zoom.stealth.canvas.noise_skip_mask"] == 15


# ──────────────────────────────────────────────────────────────────────
#  WebGL extensions (Windows clears them)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_webgl_extensions_persona_on_windows(monkeypatch):
    # WE2: with a persona active on Windows, the webgl1/webgl2 extension lists are FORCED to
    # the chosen GPU's real native-order lists (carried in the persona's coherent `prefs`),
    # NOT cleared. Order is load-bearing (must match the GPU's real capture verbatim).
    monkeypatch.setattr(sys, "platform", "win32")
    from invisible_core._webgl_personas import select_persona
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p)
    persona = select_persona(42)
    assert prefs["zoom.stealth.webgl.extensions"] == persona["prefs"]["zoom.stealth.webgl.extensions"]
    assert prefs["zoom.stealth.webgl2.extensions"] == persona["prefs"]["zoom.stealth.webgl2.extensions"]
    assert prefs["zoom.stealth.webgl.extensions"]  # non-empty (a real GPU's ext list)


# ──────────────────────────────────────────────────────────────────────
#  Timezone (platform-agnostic)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_timezone_set_uses_juggler_pref():
    # TZ1 - juggler.timezone.override is the sole C++-read timezone pref;
    # the old zoom.stealth.timezone alias (orphan) must NOT be reintroduced.
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p, timezone="America/New_York")
    assert prefs["juggler.timezone.override"] == "America/New_York"
    assert "zoom.stealth.timezone" not in prefs


@pytest.mark.unit
def test_timezone_empty_omits_the_key():
    # TZ2
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p, timezone="")
    assert "juggler.timezone.override" not in prefs
    assert "zoom.stealth.timezone" not in prefs


# ──────────────────────────────────────────────────────────────────────
#  extra_prefs overlay (platform-agnostic)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_extra_prefs_adds_custom_key():
    # EP1
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p, extra_prefs={"custom.pref": 42})
    assert prefs["custom.pref"] == 42


@pytest.mark.unit
def test_extra_prefs_none_value_deletes_key():
    # EP2
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(
        p, extra_prefs={"privacy.resistFingerprinting": None}
    )
    assert "privacy.resistFingerprinting" not in prefs


@pytest.mark.unit
def test_extra_prefs_overrides_existing_key():
    # EP3 - override a real baseline key (hw_seed is the live cross-process seed)
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p, extra_prefs={"zoom.stealth.fpp.hw_seed": 999})
    assert prefs["zoom.stealth.fpp.hw_seed"] == 999


@pytest.mark.unit
def test_extra_prefs_none_is_no_op():
    # EP4
    p = generate_profile(seed=42)
    base = translate_profile_to_prefs(p)
    with_none = translate_profile_to_prefs(p, extra_prefs=None)
    assert base == with_none


@pytest.mark.unit
def test_extra_prefs_empty_dict_is_no_op():
    # EP5
    p = generate_profile(seed=42)
    base = translate_profile_to_prefs(p)
    with_empty = translate_profile_to_prefs(p, extra_prefs={})
    assert base == with_empty


# ──────────────────────────────────────────────────────────────────────
#  System colors / dark theme (platform-agnostic - palette is Win10)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_system_colors_present_when_light_theme():
    # SC1
    p = generate_profile(seed=42, pin={"dark_theme": False})
    prefs = translate_profile_to_prefs(p)
    assert prefs["ui.systemUsesDarkTheme"] == 0
    # Spot-check a few keys from the Win10 light palette.
    for key in _WIN_LIGHT_COLORS:
        assert key in prefs
        assert prefs[key] == _WIN_LIGHT_COLORS[key]


@pytest.mark.unit
def test_system_colors_absent_when_dark_theme():
    # SC2
    p = generate_profile(seed=42, pin={"dark_theme": True})
    prefs = translate_profile_to_prefs(p)
    assert prefs["ui.systemUsesDarkTheme"] == 1
    for key in _WIN_LIGHT_COLORS:
        assert key not in prefs


# ──────────────────────────────────────────────────────────────────────
#  Locale prefs (platform-agnostic)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_locale_en_us_accept_languages():
    # LC1
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p, locale="en-US")
    assert prefs["intl.accept_languages"] == "en-US, en"


@pytest.mark.unit
def test_locale_underscore_form_normalized():
    # LC2
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p, locale="de_DE")
    assert prefs["intl.accept_languages"] == "de-DE, de"
    assert prefs["general.useragent.locale"] == "de-DE"
    assert prefs["intl.locale.requested"] == "de-DE"


@pytest.mark.unit
def test_locale_empty_falls_back_to_en_us():
    # LC3
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p, locale="")
    assert prefs["intl.accept_languages"] == "en-US, en"


# ──────────────────────────────────────────────────────────────────────
#  Xvfb workarounds (Windows must NOT set Linux-only keys)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_xvfb_workarounds_absent_on_windows(monkeypatch):
    # XW2
    monkeypatch.setattr(sys, "platform", "win32")
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p)
    assert "gfx.webrender.all" not in prefs
    assert "gfx.webrender.force-disabled" not in prefs
    assert "webgl.force-enabled" not in prefs


# ──────────────────────────────────────────────────────────────────────
#  Windows virtual-desktop workarounds
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_virtual_display_workaround_applied_on_windows(monkeypatch):
    # VD1
    monkeypatch.setattr(sys, "platform", "win32")
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p, virtual_display=True)
    assert prefs["security.sandbox.gpu.level"] == 0


@pytest.mark.unit
def test_virtual_display_workaround_absent_when_disabled(monkeypatch):
    # VD2
    monkeypatch.setattr(sys, "platform", "win32")
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p, virtual_display=False)
    assert "security.sandbox.gpu.level" not in prefs


# ──────────────────────────────────────────────────────────────────────
#  Seed-derived LAN IP (platform-agnostic)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_lan_ip_matches_192_168_pattern():
    # LI1
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p)
    ip = prefs["zoom.stealth.webrtc.host_ip"]
    m = re.match(r"^192\.168\.(\d+)\.(\d+)$", ip)
    assert m, f"unexpected LAN IP format: {ip!r}"
    o3, o4 = int(m.group(1)), int(m.group(2))
    assert 1 <= o3 <= 254
    assert 1 <= o4 <= 254


@pytest.mark.unit
def test_lan_ip_deterministic_per_seed():
    # LI2
    a = translate_profile_to_prefs(generate_profile(seed=42))["zoom.stealth.webrtc.host_ip"]
    b = translate_profile_to_prefs(generate_profile(seed=42))["zoom.stealth.webrtc.host_ip"]
    assert a == b


@pytest.mark.unit
def test_lan_ip_seed_zero_has_no_zero_octets():
    # LI3: code adds +1 so neither dynamic octet should ever be 0.
    p = generate_profile(seed=0)
    prefs = translate_profile_to_prefs(p)
    ip = prefs["zoom.stealth.webrtc.host_ip"]
    octets = ip.split(".")
    assert octets[0] == "192"
    assert octets[1] == "168"
    assert int(octets[2]) >= 1
    assert int(octets[3]) >= 1


# ──────────────────────────────────────────────────────────────────────
#  Linux-specific tests - exercise the branches that only fire when
#  ``sys.platform.startswith("linux")``. Patched via ``monkeypatch`` so
#  these run on any host CI environment.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_gpu_renderer_set_from_profile_on_linux(monkeypatch):
    # PG1: on Linux (as on EVERY host) we apply the camoufox-derived Windows-ANGLE GPU persona,
    # so the page sees a consistent Windows GPU (rule: always look Windows). The C++ WebGL
    # override is platform-independent (SanitizeRenderer is pure string regex), so the same
    # persona renderer/vendor is presented on Linux too - no more "Generic Renderer".
    monkeypatch.setattr(sys, "platform", "linux")
    from invisible_core._webgl_personas import select_persona
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p)
    persona = select_persona(42)
    assert prefs["zoom.stealth.webgl.renderer"] == persona["renderer"]
    assert prefs["zoom.stealth.webgl.renderer"].endswith(", D3D11)")
    assert prefs["zoom.stealth.webgl.vendor"] == persona["vendor"]


@pytest.mark.unit
def test_msaa_from_profile_on_linux(monkeypatch):
    # PG3: on Linux, MSAA comes from the profile's sampled value rather
    # than being pinned to 4 (which is the Windows ANGLE default).
    monkeypatch.setattr(sys, "platform", "linux")
    p = generate_profile(seed=42, pin={"webgl.msaa_samples": 8})
    prefs = translate_profile_to_prefs(p)
    assert prefs["webgl.msaa-samples"] == 8
    assert prefs["webgl.msaa-samples"] == 8
    assert prefs["webgl.msaa-force"] is True


@pytest.mark.unit
def test_msaa_zero_disables_force_on_linux(monkeypatch):
    # PG3b: MSAA=0 means "no MSAA" so ``webgl.msaa-force`` must be False.
    # Verifies the ``> 0`` guard on the force flag.
    monkeypatch.setattr(sys, "platform", "linux")
    p = generate_profile(seed=42, pin={"webgl.msaa_samples": 0})
    prefs = translate_profile_to_prefs(p)
    assert prefs["webgl.msaa-samples"] == 0
    assert prefs["webgl.msaa-force"] is False


@pytest.mark.unit
def test_canvas_noise_mask_intel_on_linux(monkeypatch):
    # CN1: Intel renderer → 1/16 noise (mask=15). Pinning the renderer
    # exercises the live ``_renderer_lo`` branch on Linux (where the
    # value is read from the profile rather than hardcoded as on Windows).
    monkeypatch.setattr(sys, "platform", "linux")
    p = generate_profile(
        seed=42,
        pin={
            "gpu.renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "gpu.vendor": "Google Inc. (Intel)",
        },
    )
    prefs = translate_profile_to_prefs(p)
    assert prefs["zoom.stealth.canvas.noise_skip_mask"] == 15


@pytest.mark.unit
def test_canvas_noise_mask_nvidia_on_linux(monkeypatch):
    # CN2: the canvas-noise mask follows the REAL HOST GPU (the canvas is drawn by real
    # hardware, NOT the exposed persona), so it is the Intel-class 1/16 rate (mask=15) on the
    # dev/test host even when an NVIDIA persona is exposed - the persona vendor does NOT drive
    # the noise rate anymore (would over-noise on an Intel host).
    monkeypatch.setattr(sys, "platform", "linux")
    p = generate_profile(
        seed=42,
        pin={
            "gpu.renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "gpu.vendor": "Google Inc. (NVIDIA)",
        },
    )
    prefs = translate_profile_to_prefs(p)
    assert prefs["zoom.stealth.canvas.noise_skip_mask"] == 15


@pytest.mark.unit
def test_webgl_extensions_preserved_on_linux(monkeypatch):
    # WE1: on Linux the curated WebGL1/2 extension lists from _BASELINE
    # remain in the prefs dict so the patched binary publishes them
    # instead of native Mesa's set.
    monkeypatch.setattr(sys, "platform", "linux")
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p)
    assert prefs["zoom.stealth.webgl.extensions"]
    assert prefs["zoom.stealth.webgl2.extensions"]
    # Spot-check a canonical Windows ANGLE extension is in the list.
    assert "ANGLE_instanced_arrays" in prefs["zoom.stealth.webgl.extensions"]
    assert "OVR_multiview2" in prefs["zoom.stealth.webgl2.extensions"]


@pytest.mark.unit
def test_xvfb_workarounds_applied_on_linux(monkeypatch):
    # XW1: Linux Firefox under Xvfb can't run WebRender, so we force the
    # software path. These are added via ``setdefault`` so callers can
    # still override them via ``extra_prefs``.
    monkeypatch.setattr(sys, "platform", "linux")
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p)
    assert prefs["gfx.webrender.all"] is False
    assert prefs["gfx.webrender.force-disabled"] is True
    assert prefs["webgl.force-enabled"] is True


@pytest.mark.unit
def test_xvfb_workarounds_caller_can_override(monkeypatch):
    # XW1b: the workarounds are added with ``setdefault``, so a user-
    # supplied ``extra_prefs`` value wins. Verifies the override path
    # doesn't get clobbered by the platform branch.
    monkeypatch.setattr(sys, "platform", "linux")
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(
        p, extra_prefs={"webgl.force-enabled": False}
    )
    assert prefs["webgl.force-enabled"] is False


@pytest.mark.unit
def test_virtual_display_no_op_on_linux(monkeypatch):
    # VD3: ``virtual_display`` is a Windows-only concept (CreateDesktop
    # alt-desktop GPU sandbox workaround). Even when True, Linux must
    # not pick up ``security.sandbox.gpu.level``.
    monkeypatch.setattr(sys, "platform", "linux")
    p = generate_profile(seed=42)
    prefs = translate_profile_to_prefs(p, virtual_display=True)
    assert "security.sandbox.gpu.level" not in prefs
