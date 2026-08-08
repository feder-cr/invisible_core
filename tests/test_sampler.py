"""Unit tests for invisible_core._fpforge._sampler.

Covers classify_gpu (decision-table over GPU strings), _screen_tier,
and the public Forge / sample entry points.
"""
# MOVED FROM invisible_playwright/tests/ ON 2026-07-27.
#
# _fpforge is this package's fingerprint generator. Its tests reached it through
# a back-compat shim in the wrapper, which is how coverage for a module ends up
# in a suite that belongs to another package on another release cadence - see
# the measurement in invisible_playwright/tests/test_suite_boundaries.py.
import pytest

from invisible_core._fpforge import _sampler
from invisible_core._fpforge._sampler import (
    Forge,
    _LOCKED,
    _screen_tier,
    classify_gpu,
    sample,
)


# ── classify_gpu ────────────────────────────────────────────────────────
#
# Decision-table tests against every branch of the classifier. Inputs use
# the ANGLE renderer string format that Firefox actually exposes.

def _gpu(renderer):
    return {"renderer": renderer, "vendor": "Google Inc."}


@pytest.mark.unit
@pytest.mark.parametrize("renderer", [
    "ANGLE (Intel, Intel(R) HD Graphics 3000 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (Intel, Intel(R) HD Graphics 4000 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (Intel, Intel(R) HD Graphics 2500 Direct3D11 vs_5_0 ps_5_0)",
])
def test_classify_gpu_intel_hd_old_buckets(renderer):
    """CG1-CG3 [DT]: HD 2500/3000/4000 → integrated_old."""
    assert classify_gpu(_gpu(renderer)) == "integrated_old"


@pytest.mark.unit
@pytest.mark.parametrize("renderer", [
    "ANGLE (Intel, Intel(R) HD Graphics 530 Direct3D11)",
    "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11)",
    "ANGLE (Intel, Intel(R) Iris Xe Graphics Direct3D11)",
    # Integrated Arc iGPUs (Core Ultra "Arc 130T/140T/Graphics") stay integrated_modern.
    "ANGLE (Intel, Intel(R) Arc(TM) 140T GPU Direct3D11)",
])
def test_classify_gpu_intel_modern(renderer):
    """CG4-CG7 [DT]: modern Intel HD/UHD/Iris + integrated Arc → integrated_modern."""
    assert classify_gpu(_gpu(renderer)) == "integrated_modern"


@pytest.mark.unit
@pytest.mark.parametrize("renderer,expected", [
    # Discrete Intel Arc DESKTOP cards are NOT integrated: A5xx/A7xx/Bxxx ~ mid-range
    # discrete (RTX 3060 tier); A3xx are entry discrete → low_end.
    ("ANGLE (Intel, Intel(R) Arc(TM) A750 Graphics Direct3D11 vs_5_0 ps_5_0)", "mid_range"),
    ("ANGLE (Intel, Intel(R) Arc(TM) A770 Graphics Direct3D11)", "mid_range"),
    ("ANGLE (Intel, Intel(R) Arc(TM) B580 Graphics Direct3D11)", "mid_range"),
    ("ANGLE (Intel, Intel(R) Arc(TM) A380 Graphics Direct3D11)", "low_end"),
])
def test_classify_gpu_intel_arc_discrete(renderer, expected):
    """Discrete Intel Arc desktop SKUs map to a discrete-GPU class, not integrated."""
    assert classify_gpu(_gpu(renderer)) == expected


@pytest.mark.unit
@pytest.mark.parametrize("renderer", [
    "ANGLE (AMD, AMD Radeon Graphics Direct3D11)",
    "ANGLE (AMD, AMD Radeon Vega 8 Direct3D11)",
])
def test_classify_gpu_amd_integrated(renderer):
    """CG8-CG9 [DT]: AMD APU graphics → integrated_modern."""
    assert classify_gpu(_gpu(renderer)) == "integrated_modern"


@pytest.mark.unit
@pytest.mark.parametrize("renderer", [
    "ANGLE (NVIDIA, NVIDIA GeForce 8800 GTX Direct3D11)",
    "ANGLE (NVIDIA, NVIDIA GeForce GTX 480 Direct3D11)",
    "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11)",
    "ANGLE (NVIDIA, NVIDIA GeForce GT 1030 Direct3D11)",
])
def test_classify_gpu_nvidia_vintage_buckets(renderer):
    """CG10-CG13 [DT]: vintage GeForce buckets → low_end."""
    assert classify_gpu(_gpu(renderer)) == "low_end"


@pytest.mark.unit
def test_classify_gpu_nvidia_modern_geforce_falls_to_low_end():
    """CG14 [DT]: GeForce GTX 1060 - sanitized vintage → low_end via fallback."""
    assert classify_gpu(_gpu(
        "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11)"
    )) == "low_end"


@pytest.mark.unit
def test_classify_gpu_nvidia_quadro_k_matches_vintage_pattern():
    """CG15 [DT]: Quadro K2200 → low_end (matches vintage Quadro K pattern)."""
    assert classify_gpu(_gpu(
        "ANGLE (NVIDIA, NVIDIA Quadro K2200 Direct3D11)"
    )) == "low_end"


@pytest.mark.unit
def test_classify_gpu_amd_radeon_high_end_boundary():
    """CG16 [DT]: AMD Radeon RX 5700 XT (n=5700) → high_end."""
    assert classify_gpu(_gpu(
        "ANGLE (AMD, AMD Radeon RX 5700 XT Direct3D11)"
    )) == "high_end"


@pytest.mark.unit
@pytest.mark.parametrize("renderer", [
    "ANGLE (AMD, AMD Radeon RX 5500 Direct3D11)",
    "ANGLE (AMD, AMD Radeon RX 580 Direct3D11)",
])
def test_classify_gpu_amd_radeon_mid_range(renderer):
    """CG17-CG18 [DT]: RX 5500 / RX 580 → mid_range."""
    assert classify_gpu(_gpu(renderer)) == "mid_range"


@pytest.mark.unit
def test_classify_gpu_amd_radeon_below_mid_range():
    """CG19 [DT]: RX 480 (n=480, not in mid_range buckets) → low_end."""
    assert classify_gpu(_gpu(
        "ANGLE (AMD, AMD Radeon RX 480 Direct3D11)"
    )) == "low_end"


@pytest.mark.unit
def test_classify_gpu_amd_firepro_falls_through_to_fallback():
    """CG20 [DT]: AMD FirePro W7100 - workstation regex requires
    'Radeon' prefix, FirePro alone doesn't match → falls through to
    mid_range fallback. (Plan claimed workstation; actual code path
    only routes Radeon-Pro-prefixed cards into the workstation bucket.)
    """
    assert classify_gpu(_gpu(
        "ANGLE (AMD, AMD FirePro W7100 Direct3D11)"
    )) == "mid_range"


@pytest.mark.unit
def test_classify_gpu_amd_radeon_pro_workstation():
    """CG21 [DT]: AMD Radeon Pro WX 7100 → workstation."""
    assert classify_gpu(_gpu(
        "ANGLE (AMD, AMD Radeon Pro WX 7100 Direct3D11)"
    )) == "workstation"


@pytest.mark.unit
def test_classify_gpu_unknown_renderer_falls_back_to_mid_range():
    """CG22 [DT]: completely unknown vendor/renderer → mid_range fallback."""
    assert classify_gpu(_gpu(
        "ANGLE (Unknown, Something Else Direct3D11)"
    )) == "mid_range"


@pytest.mark.unit
def test_classify_gpu_empty_renderer_falls_back_to_mid_range():
    """CG23 [BVA]: empty renderer string → mid_range fallback."""
    assert classify_gpu({"renderer": "", "vendor": ""}) == "mid_range"


@pytest.mark.unit
@pytest.mark.parametrize("renderer", [
    "ANGLE (AMD, AMD Radeon RX 5699 Direct3D11)",   # CG24: just below 5700
    "ANGLE (AMD, AMD Radeon RX 5601 Direct3D11)",   # CG25: just above 5600
    "ANGLE (AMD, AMD Radeon RX 579 Direct3D11)",    # CG26: just below 580
    "ANGLE (AMD, AMD Radeon RX 591 Direct3D11)",    # CG27: just above 590
])
def test_classify_gpu_amd_radeon_boundary_values_outside_mid_range(renderer):
    """CG24-CG27 [BVA]: AMD Radeon numbers just outside mid_range buckets → low_end."""
    assert classify_gpu(_gpu(renderer)) == "low_end"


@pytest.mark.unit
def test_classify_gpu_missing_renderer_key_uses_empty_default():
    """CG28 [ERR]: dict without 'renderer' key → mid_range fallback (r='')."""
    assert classify_gpu({"vendor": "X"}) == "mid_range"


# ── _screen_tier ────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("w,h,expected", [
    (1920, 1080, "1080p"),         # ST1 [ECP]
    (2560, 1440, "1440p"),         # ST2 [ECP]
    (3840, 2160, "2160p"),         # ST3 [ECP]
    (3440, 1440, "ultrawide"),     # ST4 [ECP] aspect 2.39 > 2.1
    (1921, 1080, "1440p"),         # ST5 [BVA] just above 1920
    (2561, 1440, "2160p"),         # ST6 [BVA] just above 2560
    (3841, 2160, "ultrawide"),     # ST7 [BVA] just above 3840
    (1280, 720,  "1080p"),         # ST8 [BVA] below 1920
])
def test_screen_tier_classification(w, h, expected):
    assert _screen_tier({"screen": {"w": w, "h": h}}) == expected


@pytest.mark.unit
def test_screen_tier_empty_context_defaults_to_1080p():
    """ST9 [ERR]: empty ctx → defaults w=1920, h=1080 → 1080p."""
    assert _screen_tier({}) == "1080p"


@pytest.mark.unit
def test_screen_tier_4200x2000_is_ultrawide_via_width_branch():
    """ST10 [BVA]: w=4200,h=2000 - ratio 2.1 is NOT >2.1 (strict), but
    w>3840 also routes to the final ultrawide branch."""
    assert _screen_tier({"screen": {"w": 4200, "h": 2000}}) == "ultrawide"


# ── Forge / sample ──────────────────────────────────────────────────────

# Keys the Forge.sample bundle must always contain. Builds on _LOCKED +
# every Bayesian-sampled field exposed in the return dict.
_EXPECTED_KEYS = {
    "stealth_seed",
    *_LOCKED.keys(),
    "webgl_renderer", "webgl_vendor", "gpu_class",
    "intra_tier", "screen_tier",
    "screen_w", "screen_h", "screen_avail_w", "screen_avail_h", "dpr",
    "hw_concurrency", "msaa_samples",
    "audio_sample_rate", "audio_output_latency_ms", "audio_max_channel_count",
    "av1_enabled", "webm_encoder_enabled",
    "mediasource_webm", "mediasource_mp4", "webspeech_synth",
    "storage_quota_mb", "dark_theme",
}


@pytest.mark.unit
def test_forge_sample_returns_dict():
    """FS1 [HAPPY]: sample(42) returns a non-empty dict."""
    out = sample(42)
    assert isinstance(out, dict) and out


@pytest.mark.unit
def test_forge_sample_has_every_expected_key():
    """FS2 [ECP]: every locked + sampled key is present in the bundle."""
    out = sample(42)
    missing = _EXPECTED_KEYS - set(out.keys())
    assert not missing, f"missing keys: {missing}"


@pytest.mark.unit
def test_forge_sample_field_types():
    """FS3 [ECP]: int/float/bool fields have the right Python types."""
    out = sample(42)
    assert isinstance(out["screen_w"], int)
    assert isinstance(out["screen_h"], int)
    assert isinstance(out["dpr"], float)
    assert isinstance(out["hw_concurrency"], int)
    assert isinstance(out["webdriver"], bool)
    assert isinstance(out["av1_enabled"], bool)
    # max_touch_points is NOT a sampler field: it is a declared constant on
    # HardwareProfile since 2026-08-08. It was asserted here because a stale
    # copy in _sampler._LOCKED still injected it, which is exactly the
    # shadowing this test would have hidden rather than caught - the value
    # travelled but the constant that was supposed to own it was dead.


@pytest.mark.unit
def test_forge_sample_deterministic_per_seed():
    """FS4 [ECP]: same seed → identical bundle."""
    assert sample(42) == sample(42)


@pytest.mark.unit
def test_forge_sample_varies_across_seeds():
    """FS5 [ECP]: distinct seeds → at least one varying field across N seeds."""
    bundles = [sample(s) for s in range(8)]
    renderers = {b["webgl_renderer"] for b in bundles}
    assert len(renderers) > 1


@pytest.mark.unit
def test_forge_sample_locked_identity_fields_match_locked_table():
    """FS6 [ECP]: every field in _LOCKED is echoed verbatim in the bundle."""
    out = sample(42)
    for k, v in _LOCKED.items():
        assert out[k] == v


@pytest.mark.unit
def test_forge_constructor_equivalent_to_sample_helper():
    """FS7 [ECP]: Forge(seed).sample() == sample(seed)."""
    assert Forge(42).sample() == sample(42)


@pytest.mark.unit
def test_forge_sample_avail_h_defaults_to_h_minus_the_taskbar_when_missing(monkeypatch):
    """FS8 [ECP]: with no 'ah' key, screen_avail_h defaults to screen_h minus
    the taskbar height. Real CPT data always provides 'ah', so the network is
    monkeypatched to return a synthetic bundle.

    The number was 40 here and in all 93 data rows until 2026-07-27, against the
    engine's 48 (`nsScreen.cpp:114`, "Windows default taskbar at 100% DPI"), so
    every profile REPORTED a taskbar no browser would produce. The name said 40
    for a while after the value said 48 - which is its own small lie, and the
    reason this is renamed rather than just re-numbered."""
    fake_bundle = {
        "gpu": {"renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11)",
                "vendor": "Google Inc."},
        "gpu_class": "integrated_modern",
        "intra_tier": "standard",
        "screen": {"w": 1920, "h": 1080, "dpr": 1.0},  # no aw, no ah
        "screen_tier": "1080p",
        "hw_concurrency": 8,
        "msaa_samples": 4,
        "codec": {"av1_enabled": True, "webm_encoder_enabled": True,
                  "mediasource_webm": True, "mediasource_mp4": True,
                  "webspeech_synth": True},
        "storage_quota_mb": 256000,
        "audio": {"rate": 48000, "latency": 20, "channels": 2},
        "dark_theme": 0,
    }
    monkeypatch.setattr(_sampler._NETWORK, "sample", lambda _rng: fake_bundle)
    out = Forge(42).sample()
    assert out["screen_avail_w"] == 1920    # falls back to w
    assert out["screen_avail_h"] == 1080 - 48


@pytest.mark.unit
def test_forge_seed_coercion_to_int():
    """FS extra: Forge(seed) coerces seed to int (e.g. float 42.7 → 42)."""
    f = Forge(42.7)
    assert f.seed == 42
