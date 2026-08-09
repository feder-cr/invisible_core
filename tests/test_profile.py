"""Unit tests for `_fpforge/profile.py`.

Covers `_validate_pin_key`, `_apply_pins_to_raw`, and `generate_profile`.
Test cases derived via ECP/BVA/error guessing.
"""
# MOVED FROM invisible_playwright/tests/ ON 2026-07-27.
#
# _fpforge is this package's fingerprint generator. Its tests reached it through
# a back-compat shim in the wrapper, which is how coverage for a module ends up
# in a suite that belongs to another package on another release cadence - see
# the measurement in invisible_playwright/tests/test_suite_boundaries.py.
from dataclasses import FrozenInstanceError

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core._fpforge.profile import (
    Profile,
    _PIN_GROUPS,
    _PIN_TO_RAW,
    _apply_pins_to_raw,
    _validate_pin_key,
)


# ─────────────────────────────────────────────────────────────────────
#  _validate_pin_key
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_validate_pin_key_top_level_dark_theme():
    """VK2 - `dark_theme` is a known top-level key."""
    _validate_pin_key("dark_theme")


@pytest.mark.unit
def test_validate_pin_key_dotted_screen_width():
    """VK3 - valid dotted path `screen.width`."""
    _validate_pin_key("screen.width")


@pytest.mark.unit
def test_validate_pin_key_dotted_gpu_renderer():
    """VK4 - valid dotted path `gpu.renderer`."""
    _validate_pin_key("gpu.renderer")


@pytest.mark.unit
def test_validate_pin_key_dotted_webgl_msaa_samples():
    """VK5 - valid dotted path `webgl.msaa_samples`."""
    _validate_pin_key("webgl.msaa_samples")


@pytest.mark.unit
def test_validate_pin_key_no_dot_not_top_level_raises():
    """VK6 - bare key not in top-level set raises with hint."""
    with pytest.raises(ValueError, match="group.field"):
        _validate_pin_key("bogus")


@pytest.mark.unit
def test_validate_pin_key_unknown_group_raises():
    """VK7 - unknown group prefix."""
    with pytest.raises(ValueError, match="unknown group"):
        _validate_pin_key("network.port")


@pytest.mark.unit
def test_validate_pin_key_unknown_field_in_valid_group_raises():
    """VK8 - known group, unknown field."""
    with pytest.raises(ValueError, match="unknown field"):
        _validate_pin_key("screen.brightness")


@pytest.mark.unit
def test_validate_pin_key_empty_string_raises():
    """VK9 - empty key fails the dotted-form check."""
    with pytest.raises(ValueError):
        _validate_pin_key("")


@pytest.mark.unit
@pytest.mark.parametrize("group,fields", sorted(_PIN_GROUPS.items()))
def test_validate_pin_key_all_groups_first_field(group, fields):
    """VK10 - every defined group accepts its sorted-first field."""
    first = sorted(fields)[0]
    _validate_pin_key(f"{group}.{first}")


# ─────────────────────────────────────────────────────────────────────
#  _apply_pins_to_raw
# ─────────────────────────────────────────────────────────────────────

def _raw_baseline():
    """A minimal raw dict for pin tests - only the keys we care about."""
    return {
        "screen_w": 1920,
        "screen_h": 1080,
        "webgl_vendor": "Google Inc. (Intel)",
        "webgl_renderer": "ANGLE (Intel)",
        "dark_theme": 0,
    }


@pytest.mark.unit
def test_apply_pins_to_raw_screen_width():
    """AP1 - `screen.width` rewrites `screen_w` in raw."""
    out = _apply_pins_to_raw(_raw_baseline(), {"screen.width": 2560})
    assert out["screen_w"] == 2560


@pytest.mark.unit
def test_apply_pins_to_raw_multiple_pins():
    """AP6 - multiple pins all land in raw."""
    pin = {"gpu.vendor": "X", "gpu.renderer": "Y"}
    out = _apply_pins_to_raw(_raw_baseline(), pin)
    assert out["webgl_vendor"] == "X"
    assert out["webgl_renderer"] == "Y"


@pytest.mark.unit
def test_apply_pins_to_raw_returns_copy_not_mutation():
    """AP7 - input dict is not mutated."""
    raw = _raw_baseline()
    snapshot = dict(raw)
    _apply_pins_to_raw(raw, {"screen.width": 9999})
    assert raw == snapshot


@pytest.mark.unit
def test_apply_pins_to_raw_unknown_key_silent():
    """AP8 - key not in `_PIN_TO_RAW` is ignored.

    Validation happens upstream in `generate_profile`; the inner helper
    guards defensively but does not raise.
    """
    raw = _raw_baseline()
    out = _apply_pins_to_raw(raw, {"some.unknown": 123})
    # No change to known fields
    assert out["screen_w"] == raw["screen_w"]
    # No new key added
    assert "some.unknown" not in out


# ─────────────────────────────────────────────────────────────────────
#  generate_profile
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_generate_profile_happy_path():
    """GP1 - returns a fully populated Profile."""
    p = generate_profile(seed=42)
    assert isinstance(p, Profile)
    assert p.seed == 42
    assert p.gpu.vendor
    assert p.gpu.renderer
    assert p.gpu.class_tier in _PIN_GROUPS["gpu"].union({"low_end", "mid_range",
        "high_end", "integrated_old", "integrated_modern", "workstation"})
    assert p.screen.width > 0
    assert p.screen.height > 0
    assert p.hardware.concurrency > 0
    assert p.audio.sample_rate > 0


@pytest.mark.unit
def test_generate_profile_deterministic():
    """GP2 - same seed → identical Profile (equality on frozen dataclass)."""
    a = generate_profile(seed=42)
    b = generate_profile(seed=42)
    assert a == b


@pytest.mark.unit
def test_generate_profile_seed_float_coerced():
    """GP3 - float seed is coerced to int (truncated)."""
    a = generate_profile(seed=42.7)
    b = generate_profile(seed=42)
    assert a == b


@pytest.mark.unit
def test_generate_profile_seed_string_coerced():
    """GP4 - numeric string seed works via int() coercion."""
    a = generate_profile(seed="42")
    b = generate_profile(seed=42)
    assert a == b


@pytest.mark.unit
def test_generate_profile_no_pin_samples_freely():
    """GP5 - no pin: every field is sampler-derived (sanity: 2 seeds differ)."""
    a = generate_profile(seed=1)
    b = generate_profile(seed=2)
    assert a != b


@pytest.mark.unit
def test_generate_profile_pin_overrides_screen_width():
    """GP6 - pinned width visible on the Profile dataclass."""
    p = generate_profile(seed=42, pin={"screen.width": 9999})
    assert p.screen.width == 9999


@pytest.mark.unit
def test_generate_profile_pin_visible_in_prefs_dict():
    """GP7 - pinned values flow through to to_prefs_dict()."""
    p = generate_profile(seed=42, pin={"screen.width": 9999})
    assert p.to_prefs_dict()["screen_w"] == 9999


@pytest.mark.unit
def test_generate_profile_invalid_pin_raises():
    """GP8 - bad pin key surfaces ValueError from validation."""
    with pytest.raises(ValueError):
        generate_profile(seed=42, pin={"bogus": 1})


@pytest.mark.unit
def test_generate_profile_empty_pin_equals_no_pin():
    """GP9 - empty pin dict is a no-op."""
    a = generate_profile(seed=42, pin={})
    b = generate_profile(seed=42)
    assert a == b


@pytest.mark.unit
def test_generate_profile_is_frozen():
    """GP10 - Profile dataclass is immutable."""
    p = generate_profile(seed=42)
    with pytest.raises(FrozenInstanceError):
        p.seed = 99  # type: ignore[misc]


@pytest.mark.unit
def test_generate_profile_to_prefs_dict_flat_and_matches_raw():
    """GP12 - to_prefs_dict() returns a flat dict containing core sampler keys."""
    p = generate_profile(seed=42)
    d = p.to_prefs_dict()
    assert isinstance(d, dict)
    for key in ("screen_w", "screen_h", "webgl_vendor", "webgl_renderer",
                "hw_concurrency", "stealth_seed"):
        assert key in d


@pytest.mark.unit
def test_generate_profile_seed_zero():
    """GP13 - seed=0 is a valid lowest-value boundary."""
    p = generate_profile(seed=0)
    assert p.seed == 0


@pytest.mark.unit
def test_generate_profile_seed_max_int31():
    """GP14 - seed at int31 upper bound works."""
    seed = (1 << 31) - 1
    p = generate_profile(seed=seed)
    assert p.seed == seed


@pytest.mark.unit
def test_generate_profile_dark_theme_is_bool():
    """GP15 - dark_theme is coerced to bool on the dataclass."""
    p = generate_profile(seed=42)
    assert isinstance(p.dark_theme, bool)


# ─────────────────────────────────────────────────────────────────────
#  Additional pin coverage (recheck pass)
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_generate_profile_pin_dark_theme_true():
    """Pinning dark_theme=True flows through coercion to bool."""
    p = generate_profile(seed=42, pin={"dark_theme": True})
    assert p.dark_theme is True


@pytest.mark.unit
def test_generate_profile_pin_dark_theme_false():
    p = generate_profile(seed=42, pin={"dark_theme": False})
    assert p.dark_theme is False


@pytest.mark.unit
def test_generate_profile_pin_gpu_renderer_propagates():
    p = generate_profile(seed=42, pin={"gpu.renderer": "FORCED_RENDERER"})
    assert p.gpu.renderer == "FORCED_RENDERER"
    assert p.to_prefs_dict()["webgl_renderer"] == "FORCED_RENDERER"


@pytest.mark.unit
def test_generate_profile_pin_to_raw_keymap_complete():
    """Every dotted pin key has a `_PIN_TO_RAW` mapping.

    Guards against silently-ignored pins if someone adds a key to `_PIN_GROUPS`
    but forgets the raw-key mapping.
    """
    dotted = {f"{group}.{field}" for group, fields in _PIN_GROUPS.items()
              for field in fields}
    # 'dark_theme' is top-level and present in _PIN_TO_RAW.
    missing = dotted - set(_PIN_TO_RAW.keys())
    assert missing == set(), f"pin keys without raw mapping: {sorted(missing)}"


def test_no_profile_constant_is_shadowed_by_the_sampler_s_locked_dict():
    """Changing a declared constant must change the profile it declares.

    _LOCKED is spread into the raw dict before the setdefault() calls that seed
    the invariant constants, so a key present in BOTH silently keeps _LOCKED's
    value and the constant becomes dead. Found 2026-08-09: max_touch_points was
    in both, the two values were 0, and nothing was wrong until somebody changed
    one of them - measured by setting MAX_TOUCH_POINTS to 7 and watching the
    profile still answer 0.

    That key is no longer in this list because it no longer has a constant at
    all: on 2026-08-10 max_touch_points became a SAMPLED field, drawn from
    `cpt_touch_given_class.json`, which is the level the rest of the persona
    lives at. The property below still guards every other seeded constant.

    This asserts the property rather than the one key, so the next duplicate is
    caught the day it is added rather than the day it disagrees.
    """
    from invisible_core._fpforge import _sampler, profile as prof

    seeded = {
        "screen_color_depth": "SCREEN_COLOR_DEPTH",
        "font_ui_family": "FONT_UI_FAMILY",
        "font_ui_size": "FONT_UI_SIZE",
        "font_monospace_size": "FONT_MONOSPACE_SIZE",
        "font_alpha_ladder": "FONT_ALPHA_LADDER",
        "font_cleartype_gamma": "FONT_CLEARTYPE_GAMMA",
        "font_cleartype_contrast": "FONT_CLEARTYPE_CONTRAST",
        "font_cleartype_level": "FONT_CLEARTYPE_LEVEL",
        "font_cleartype_pixel_structure": "FONT_CLEARTYPE_PIXEL_STRUCTURE",
        "font_cleartype_rendering_mode": "FONT_CLEARTYPE_RENDERING_MODE",
        "font_freetype_gamma": "FONT_FREETYPE_GAMMA",
        "font_freetype_contrast": "FONT_FREETYPE_CONTRAST",
    }
    clashing = sorted(k for k in seeded if k in _sampler._LOCKED)
    assert not clashing, (
        f"these raw keys are seeded from a constant in profile.py AND injected "
        f"by _sampler._LOCKED: {clashing}. _LOCKED wins, so the constant is "
        f"dead and the two will disagree the first time one of them moves"
    )

def test_declared_hardware_constants_have_the_right_types():
    """The types the sampler test used to assert, asserted where they live now."""
    from invisible_core._fpforge.profile import generate_profile

    p = generate_profile(seed=42)
    assert isinstance(p.hardware.max_touch_points, int)
    assert isinstance(p.screen.color_depth, int)
    assert isinstance(p.font.cleartype_gamma, int)


def test_the_windows_claim_has_exactly_one_source_each():
    """platform, oscpu and the User-Agent must not be written out twice.

    They are three halves of the same claim and they have to agree; the way they
    stop agreeing is somebody editing one copy. USER_AGENT was already
    centralised in constants.py and the other two were not, so they were the
    pair that could drift - the same shape as the max_touch_points copy that
    silently won over its own constant on 2026-08-09.

    Asserts the values reaching BOTH consumers come from the shared constant,
    rather than that two literals happen to match today.
    """
    from invisible_core import constants
    from invisible_core._fpforge import _sampler
    from invisible_core._fpforge.profile import generate_profile
    from invisible_core.prefs import translate_profile_to_prefs

    prefs = translate_profile_to_prefs(generate_profile(seed=42))
    assert prefs["general.platform.override"] is constants.PLATFORM_OVERRIDE
    assert prefs["general.oscpu.override"] is constants.OSCPU_OVERRIDE
    assert prefs["general.useragent.override"] is constants.USER_AGENT

    bundle = _sampler.sample(42)
    assert bundle["platform"] is constants.PLATFORM_OVERRIDE
    assert bundle["oscpu"] is constants.OSCPU_OVERRIDE
    assert bundle["user_agent"] is constants.USER_AGENT
