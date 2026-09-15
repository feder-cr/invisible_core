"""Every pinnable key reaches the browser, or says in writing why it does not.

This exists because of the class of defect [B206] belongs to: a field the core
DECLARES that something downstream silently overrides or never reads. It is not
a cosmetic problem. The two cases measured on 2026-09-15:

  * all three `gpu.*` keys set a label on the Profile while `prefs` re-derived
    the persona from the seed, so a pinned GPU never reached the page;
  * `webgl.msaa_samples` was honoured on Linux and overridden with 4 on Windows,
    so the two builds emitted a DIFFERENT value for the same seed on seven of
    eight seeds, and across 200 seeds Linux spread over {0, 2, 4, 8} while
    Windows was 4 every time - on a value whose own code comment says variation
    is detectable.

Both were invisible to the suite because every test asserted on the Profile
object or on `to_prefs_dict()`, which is the sampler's bookkeeping, rather than
on the prefs the browser is launched with.

The gate EXECUTES rather than reads: it pins each key to a different value and
diffs the emitted prefs. A key that moves nothing must be named in
`DELIBERATELY_NOT_EMITTED` with the reason, so the next one cannot appear in
silence.
"""
from __future__ import annotations

import sys

import pytest

from invisible_core._fpforge.profile import (_PIN_GROUPS, _PIN_TOP,
                                             generate_profile)
from invisible_core._webgl_personas import _gpu_pool
from invisible_core.prefs import translate_profile_to_prefs

pytestmark = pytest.mark.unit

SEED = 1561645783

#: Keys that legitimately emit no pref of their own. Each one needs a reason a
#: reader can check, not a note that it is known.
DELIBERATELY_NOT_EMITTED = {
    "screen.avail_width": (
        "The engine DERIVES the available rect from width/height minus "
        "taskbar_px, so emitting it as well would be a second number for one "
        "fact. Pinning it moves the Profile field only; what a page reads stays "
        "coherent because it comes from the width that IS emitted."),
    "screen.avail_height": (
        "Same as avail_width. `generate_profile` re-derives it when taskbar_px "
        "is pinned and avail_height is not, which is the coherence that matters."),
}


def _other_persona(profile):
    return next(p for p in _gpu_pool() if p["renderer"] != profile.gpu.renderer)


def _perturb(key, value, profile):
    """A different but plausible value for this key."""
    if key == "gpu.renderer":
        return _other_persona(profile)["renderer"]
    if key == "gpu.vendor":
        return _other_persona(profile)["vendor"]
    if key == "gpu.class_tier":
        classes = sorted({p["gpu_class"] for p in _gpu_pool()})
        return next(c for c in classes if c != value)
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return int(value) + 7
    if isinstance(value, float):
        return round(float(value) + 0.25, 4)
    if isinstance(value, str):
        return (value + "-X") if value else "X"
    if isinstance(value, tuple):
        return tuple(list(value)[1:] + [list(value)[0]])
    return value


def _all_keys():
    return sorted(_PIN_TOP) + sorted(
        "%s.%s" % (group, field)
        for group, fields in _PIN_GROUPS.items() for field in fields)


def _current(profile, key):
    if key in _PIN_TOP:
        return getattr(profile, key)
    group, field = key.split(".", 1)
    return getattr(getattr(profile, group), field)


def _prefs_moved_by(key):
    base_profile = generate_profile(SEED)
    base = translate_profile_to_prefs(base_profile)
    before = _current(base_profile, key)
    after = _perturb(key, before, base_profile)
    assert after != before, "the probe failed to change %s" % key
    pinned = translate_profile_to_prefs(generate_profile(SEED, pin={key: after}))
    return sorted(k for k in set(base) | set(pinned) if base.get(k) != pinned.get(k))


@pytest.mark.parametrize("key", _all_keys())
def test_every_pinnable_key_reaches_the_browser_or_says_why(key):
    moved = _prefs_moved_by(key)
    if key in DELIBERATELY_NOT_EMITTED:
        assert not moved, (
            "%s is listed as deliberately not emitted but it moved %s. Remove it "
            "from DELIBERATELY_NOT_EMITTED rather than leaving a stale excuse."
            % (key, moved))
        return
    assert moved, (
        "pin %r changes NOTHING the browser is told, so it is decorative: a "
        "caller who pins it gets a Profile that reports their value and a page "
        "that never sees it. Either make it reach the prefs, or take it out of "
        "_PIN_GROUPS, or add it to DELIBERATELY_NOT_EMITTED with a reason."
        % key)


def test_the_exception_list_has_no_entries_for_keys_that_are_gone():
    """A stale excuse is worse than none: it reads as a decision that still holds."""
    unknown = sorted(set(DELIBERATELY_NOT_EMITTED) - set(_all_keys()))
    assert not unknown, (
        "DELIBERATELY_NOT_EMITTED names keys that are no longer pinnable: %s"
        % unknown)


def test_msaa_is_the_same_on_both_builds(monkeypatch):
    """The two builds must emit the SAME sample count, and it must not vary.

    Until 2026-09-15 Windows emitted 4 while Linux emitted the sampled value:
    seven of eight seeds diverged, and Linux spread over {0, 2, 4, 8} across 200
    seeds. The remedy already existed on Windows and had simply never been
    carried to Linux. The direction is fixed by the target, not by taste - the
    judge is retail Windows, so Linux moves onto Windows."""
    seen = {}
    for platform in ("win32", "linux"):
        monkeypatch.setattr(sys, "platform", platform)
        seen[platform] = [
            translate_profile_to_prefs(generate_profile(seed))["webgl.msaa-samples"]
            for seed in (0, 7, 42, 1000, 1561645783, 555, 99, 12345)
        ]
    assert seen["win32"] == seen["linux"], (
        "the two builds emit different MSAA sample counts for the same seeds: %s"
        % seen)
    assert set(seen["win32"]) == {4}, (
        "gl.SAMPLES must be constant across sessions, got %s" % sorted(set(seen["win32"])))


def test_msaa_force_follows_the_emitted_count(monkeypatch):
    """The force flag is derived from the count, so it cannot contradict it."""
    for platform in ("win32", "linux"):
        monkeypatch.setattr(sys, "platform", platform)
        prefs = translate_profile_to_prefs(generate_profile(42))
        assert prefs["webgl.msaa-force"] is (prefs["webgl.msaa-samples"] > 0)


def test_msaa_samples_is_no_longer_offered_as_a_pin():
    """A key that cannot be honoured must refuse, not look like it worked."""
    with pytest.raises(ValueError) as excinfo:
        generate_profile(42, pin={"webgl.msaa_samples": 8})
    assert "webgl" in str(excinfo.value)
