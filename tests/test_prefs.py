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
    _accept_language_header,
    _q_ladder,
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


@pytest.mark.unit
def test_accept_language_header_uses_the_q_values_firefox_actually_sends():
    """The wire header, and the whole point is the 9.

    The engine synthesized this in JavaScript with a hardcoded ";q=0.5",
    described in its own comment as "the Firefox-native q-valued form".
    Measured 2026-08-09 against stock Firefox 151, which sends q=0.9 on every
    request: the 0.5 was copied from the stale doc block above
    PrepareAcceptLanguages in nsHttpHandler.cpp, while the code below it
    forwards to rust_prepare_accept_languages, which does 1.0/0.9/0.8.

    So this asserts the LITERAL string, not the shape. A test written as
    `header.startswith(locale)` would have passed on the wrong value, which is
    how the wrong value survived to begin with.
    """
    assert _accept_language_header("en-US") == "en-US,en;q=0.9"
    assert _accept_language_header("pt_BR") == "pt-BR,pt;q=0.9"
    # No region means one tag, and a single tag carries no q at all.
    assert _accept_language_header("fr") == "fr"
    assert ";q=0.5" not in _accept_language_header("it-IT")


@pytest.mark.unit
def test_accept_language_header_q_ladder_matches_the_rust_helper():
    """q = max(10 - min(10, i), 1), replicated from the code and not the prose.

    Firefox never ships more than a handful of tags, but the ladder is the part
    that was wrong, so it is the part worth pinning. Built by hand here rather
    than by calling the same expression the implementation uses, which would
    assert nothing.
    """
    # Ten tags exercise the floor: the tenth would want q=0.0 and gets 0.1.
    tags = ["en-US", "en", "fr", "de", "it", "es", "pt", "nl", "sv", "da"]
    parts = _q_ladder(tags).split(",")
    assert parts[0] == "en-US"
    expected = ["en;q=0.9", "fr;q=0.8", "de;q=0.7", "it;q=0.6", "es;q=0.5",
                "pt;q=0.4", "nl;q=0.3", "sv;q=0.2", "da;q=0.1"]
    assert parts[1:] == expected


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


# ──────────────────────────────────────────────────────────────────────
#  Web APIs that must EXIST, which is a different question from what they
#  answer. Added 2026-08-09 after a 119-field sweep against stock 151.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_the_apis_a_real_firefox_has_are_not_switched_off():
    """Three APIs were missing from the page, and none of it was on purpose.

    `geo.enabled` and `dom.push.enabled` were False for a NETWORK reason -
    startup chatter through a residential proxy - and the side effect was that
    `navigator.geolocation` and `window.PushManager` did not exist. Stock 151
    has both. `'geolocation' in navigator` is one line, and rule 12 counts a
    suppressed signal as a FAIL rather than a pass.

    `dom.w3c_touch_events.enabled` is the cross-OS half: at its default the
    Touch interfaces appear on Windows and not on Linux, so a persona claiming
    Windows had them on one host and not the other.

    Asserted as the literal prefs rather than through a browser, because this
    suite never launches one - the browser-side proof is the sweep, and this is
    what keeps someone from switching them off again for the network reason
    that is still written next to them.
    """
    prefs = translate_profile_to_prefs(generate_profile(42))
    assert prefs["geo.enabled"] is True
    assert prefs["dom.push.enabled"] is True
    # The network goal the False came from is kept by these two, so a future
    # reader does not have to choose between the API and the quiet startup.
    assert prefs["dom.push.connection.enabled"] is False
    # And NOT permissions.default.geo. It was set to 2 (deny) to keep the
    # network quiet, and `navigator.permissions.query({name:"geolocation"})`
    # then answered `denied` where stock answers `prompt` - the only divergence
    # across 21 permission names, measured. A profile that has never been asked
    # says prompt. Asserted as an absence so the next person who wants a quiet
    # geolocation reaches for the provider URL instead.
    assert "permissions.default.geo" not in prefs


@pytest.mark.unit
def test_the_three_touch_surfaces_come_from_one_declaration():
    """maxTouchPoints, the Touch interfaces and any-pointer must agree.

    A page reads the touch story three ways, and they used to come from three
    places: this file (0), `dom.w3c_touch_events.enabled` at its default of 2
    which asks the HOST, and a compiled `Fine | Hover` in nsMediaFeatures.cpp.

    Measured 2026-08-09: our Windows build had the Touch interfaces present
    (this machine has a digitizer) with maxTouchPoints 0 and any-pointer coarse
    false - a machine that supports touch events, has no touch points and has
    no coarse pointer. Our Linux build had no Touch interfaces at all, so one
    persona answered differently on the two hosts.

    The bits are PointerCapabilities from ServoTypes.h: Coarse 1, Fine 2,
    Hover 4.
    """
    desktop = translate_profile_to_prefs(generate_profile(42))
    assert desktop["zoom.stealth.max_touch_points"] == 0
    # NEVER 2 - at 2 the engine calls PlatformSupportsTouch() and answers with
    # whatever machine it is running on.
    assert desktop["dom.w3c_touch_events.enabled"] == 0
    assert desktop["zoom.stealth.pointer.primary"] == 6
    assert desktop["zoom.stealth.pointer.all"] == 6

    # And pinning the count alone has to move all three, or they are not one
    # declaration - they are three that happen to agree today.
    laptop = translate_profile_to_prefs(
        generate_profile(42, pin={"hardware.max_touch_points": 10}))
    assert laptop["zoom.stealth.max_touch_points"] == 10
    assert laptop["dom.w3c_touch_events.enabled"] == 1
    # The primary pointer of a touch laptop is still the mouse; only the union
    # gains Coarse. That distinction is the whole reason `pointer` and
    # `any-pointer` are two different media features.
    assert laptop["zoom.stealth.pointer.primary"] == 6
    assert laptop["zoom.stealth.pointer.all"] == 7


@pytest.mark.unit
def test_the_storage_quota_is_a_value_firefox_can_actually_report():
    """10 GiB, because Firefox caps the group limit at 10 GB and says so.

    `QuotaManager::GetGroupLimitForLimit` in dom/quota/ActorsParent.cpp reads
    `std::min<uint64_t>(aLimit / 5, 10 GB)`, with the comment "cap the group
    limit to 10GB". So `navigator.storage.estimate().quota` is exactly
    10737418240 on any machine whose disk is 100 GiB or more - which is all of
    them - and the disk size never reaches the page at all.

    The sampled pool held 13 values from 40 GiB to 3 TB, chosen to "mask the
    real disk size while staying in a realistic range". There was nothing to
    mask, and none of the 13 was a number a real Firefox can return. Measured
    2026-08-09 against stock 151: stock 10737418240, ours 429496729600.

    Asserted in BYTES as well as MB, because the megabyte figure is the one a
    reader can talk themselves into and the byte figure is the one the page
    sees.

    ASSERTED OVER THE TABLE, NOT OVER SAMPLES. The first version of this test
    checked four seeds, and its first mutation SURVIVED: putting one CPT entry
    back to 400 GiB changed nothing, because none of those four seeds happens
    to draw that row. A gate that samples a distribution cannot see a value
    that is merely rare - which is the property that let this defect last as
    long as it did in the first place.
    """
    import json
    import pathlib

    table_path = (pathlib.Path(__file__).resolve().parents[1]
                  / "src" / "invisible_core" / "_fpforge" / "data"
                  / "cpt_storage_given_class_tier.json")
    data = json.loads(table_path.read_text(encoding="utf-8"))
    bad = {}
    for key, rows in data["table"].items():
        for row in rows:
            if row["value"] != 10240:
                bad.setdefault(key, []).append(row["value"])
    assert not bad, (
        "these CPT entries hold a quota Firefox cannot report - the cap is "
        f"10 GiB = 10240 MB: {bad}")

    # And the sampled path on top, because a correct table reached through a
    # broken translation is still the wrong number on the wire.
    for seed in (1, 42, 1234, 99999):
        prefs = translate_profile_to_prefs(generate_profile(seed))
        mb = prefs["zoom.stealth.storage.quota_mb"]
        assert mb == 10240, seed
        assert mb * 1024 * 1024 == 10737418240, seed

    # Still a field and still pinnable: on a genuinely small disk the real rule
    # is disk/10, and that persona has to remain expressible.
    pinned = translate_profile_to_prefs(
        generate_profile(42, pin={"hardware.storage_quota_mb": 6553}))
    assert pinned["zoom.stealth.storage.quota_mb"] == 6553
