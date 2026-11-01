"""The gfx blocklist outcome is declared per real device, not asked of the host adapter.

A pool renderer is a SanitizeRenderer bucket, and inside three buckets official
Firefox 159 answers GetFeatureStatus differently per real device (video decoder
reuse, zero-copy decode, hardware encode, native GL WebGL). So every Windows persona
declares one real device of its bucket and that device's 63 outcomes, measured on the
retail build with Mozilla's own MOZ_GFX_SPOOF_* variables (coord/gfxinfo):
`zoom.stealth.gfx.feature_status` = "NAME|STATUS[|failureId],...".

The five Direct3D 10 class slots (shader model 4: Intel HD Graphics vs_4_0 and
vs_4_1 x2, GeForce 8800 GTX, Intel 945GM) were personas that do not exist on
Windows 11, which has no driver for them. They were replaced in place by five
real Windows 11 GPUs (same position and probability), so only the seeds that drew
them changed persona.
"""
from __future__ import annotations

import json
import re
import sys

import pytest

from invisible_core import _webgl_personas as W
from invisible_core._fpforge import generate_profile
from invisible_core.prefs import translate_profile_to_prefs

from _engine_source import EngineSource

pytestmark = pytest.mark.unit

_DATA = json.loads(W._DEVICES_PATH.read_text(encoding="utf-8"))
_FEATURES = _DATA["_meta"]["features"]
_POOL = json.loads(W._POOL_PATH.read_text(encoding="utf-8"))["win"]
# the slot probabilities before the replacement: the in-place rule is that none moved
_PROBS_BEFORE = [0.470921, 0.195745, 0.160284, 0.086524, 0.053901, 0.012766, 0.007092,
                 0.004255, 0.001419, 0.001419, 0.001419, 0.001419, 0.001419, 0.001419]
_SOURCE = EngineSource.pinned()


def _bucket(name: str) -> str:
    """dom/canvas/SanitizeRenderer.cpp ChooseDeviceReplacement, for the vendors the
    Windows pool uses (Radeon names, GeForce, Intel), as the ANGLE renderer it yields."""
    m = re.search(r"Radeon.*?((R[579X]|HD) )?([0-9][0-9][0-9]+)", name)
    if m:
        if m.group(2) == "HD":
            return "Radeon HD 5850" if int(m.group(3)) >= 5000 else "Radeon HD 3200 Graphics"
        return "Radeon R9 200 Series"
    if "AMD " in name or "Radeon" in name:
        return "Radeon HD 3200 Graphics"
    m = re.search(r"GeForce.*?([0-9][0-9][0-9]+)", name)
    if m:
        n = int(m.group(1))
        return "NVIDIA GeForce " + ("8800 GTX" if n >= 8000 else "GTX 980" if n >= 900
                                    else "GTX 480" if n >= 400 else "8800 GTX")
    if "Intel(R) Arc(TM)" in name:
        return "Intel(R) Arc(TM) A750 Graphics"
    m = re.search(r"Intel.*Graphics( P?([0-9][0-9][0-9]+))?", name)
    if m:
        if not m.group(1):
            return "Intel(R) HD Graphics"
        n = int(m.group(2))
        return "Intel(R) HD Graphics 400" if n >= 5000 or n < 1000 else "Intel(R) HD Graphics"
    return "Intel 945GM"


def test_no_direct3d10_class_persona_left():
    for e in _POOL:
        assert "vs_4_" not in e["prefs"]["zoom.stealth.webgl.renderer"], e["renderer_out"]


def test_replaced_in_place():
    # same probabilities in the same order: every other slot's cumulative boundary is unchanged
    assert [e["prob"] for e in _POOL] == _PROBS_BEFORE


def test_every_persona_has_a_real_device():
    for e in _POOL:
        renderer = e["prefs"]["zoom.stealth.webgl.renderer"]
        labels = [d["label"] for d in _DATA["by_renderer"].get(renderer, [])]
        assert labels, renderer
        if e.get("device"):
            assert e["device"] in labels


@pytest.mark.parametrize("renderer", sorted(_DATA["by_renderer"]))
def test_every_device_sanitizes_to_its_bucket(renderer):
    # the page sees the bucket; a device from another bucket would contradict its renderer
    for d in _DATA["by_renderer"][renderer]:
        assert f", {_bucket(d['windows_name'])} Direct3D11 vs_5_0" in renderer, d["label"]


def test_feature_status_lists_every_live_feature():
    assert len(_FEATURES) == 63
    for devs in _DATA["by_renderer"].values():
        for d in devs:
            parts = [p.split("|") for p in d["feature_status"].split(",")]
            assert [p[0] for p in parts] == _FEATURES, d["label"]
            for p in parts:
                assert 1 <= int(p[1]) <= 11, (d["label"], p)
                # a failureId only when the status is not OK, as GetFeatureStatus gives it
                assert len(p) == (2 if p[1] == "1" else 3), (d["label"], p)


def test_webgl_is_never_blocked():
    # retail gives OK on every device measured: the declaration must keep it so
    for devs in _DATA["by_renderer"].values():
        for d in devs:
            st = dict(p.split("|")[:2] for p in d["feature_status"].split(","))
            for f in ("WEBGL", "WEBGL2", "WEBGL_ANGLE", "WEBGL_USE_HARDWARE", "DIRECT3D_11_ANGLE", "WEBGPU"):
                assert st[f] == "1", (d["label"], f)


@pytest.mark.skipif(_SOURCE.unavailable() is not None, reason=_SOURCE.unavailable() or "")
def test_features_are_the_engines():
    inc = _SOURCE.show("widget/GfxInfoFeatureDefs.inc")
    assert inc is not None
    live = [m.group(2) for m in re.finditer(r"^\s*(GFXINFO_FEATURE\w*)\((\w+)", inc, re.M)
            if m.group(1) != "GFXINFO_FEATURE_RETIRED" and m.group(2) not in ("ALL", "OPTIONAL")]
    assert live == _FEATURES


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_declared_from_the_profile_on_every_host(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    for seed in range(0, 400, 7):
        prof = generate_profile(seed=seed)
        persona = W.select_persona(seed)
        assert prof.gpu.renderer == persona["renderer"]  # the device moved no persona
        dev = W.gfx_device_for(prof.gpu.renderer, prof.gpu.device)
        assert dev is not None and dev == W.choose_gfx_device(persona, seed)
        prefs = translate_profile_to_prefs(prof, timezone="America/New_York")
        assert prefs["zoom.stealth.gfx.feature_status"] == dev["feature_status"]


def test_replacement_slots_declare_their_own_device():
    pool = W._gpu_pool()
    for p in pool:
        if p["device"]:
            for seed in range(50):
                assert W.choose_gfx_device(p, seed)["label"] == p["device"]


def test_every_device_of_a_bucket_is_reachable():
    by_label = {}
    for seed in range(4000):
        p = W.select_persona(seed)
        d = W.choose_gfx_device(p, seed)
        by_label.setdefault(p["renderer"], set()).add(d["label"])
    for renderer, labels in by_label.items():
        assert labels == {d["label"] for d in _DATA["by_renderer"][renderer]}, renderer


# ── the engine's contract for zoom.stealth.gfx.feature_status (2026-11-01) ───
# The engine stops at its first graphics decision when the pref is missing or breaks
# one of these, so every value the core can emit is held to them here.
_ALLOWLIST = {"HW_DECODED_VIDEO_ZERO_COPY"}   # GFXINFO_FEATURE_ALLOWLIST
_FAILURE = re.compile(r"[A-Za-z0-9_]{1,96}")


def contract_violations(value):
    out = []
    entries = value.split(",")
    if not value or any(e == "" or " " in e for e in entries):
        return ["empty entry or a space"]
    names = []
    for e in entries:
        f = e.split("|")
        if len(f) not in (2, 3) or any(x == "" for x in f):
            out.append("malformed %r" % e)
            continue
        name, st = f[0], f[1]
        names.append(name)
        if not st.isdigit() or int(st) not in (1, 3, 4, 5, 6, 7, 8, 9, 10, 11):
            out.append("%s: status %s" % (name, st))
            continue
        st = int(st)
        if name in _ALLOWLIST and st == 1:
            out.append("%s: an allowlist feature is never 1" % name)
        if name not in _ALLOWLIST and st in (8, 9, 10):
            out.append("%s: status %d is for the allowlist only" % (name, st))
        fail = f[2] if len(f) == 3 else None
        if st == 1 and fail is not None:
            out.append("%s: a failure with status 1" % name)
        if st not in (1, 8) and fail is None:
            out.append("%s: status %d needs a failure" % (name, st))
        if fail is not None and not _FAILURE.fullmatch(fail):
            out.append("%s: failure %r" % (name, fail))
    if sorted(names) != sorted(_FEATURES) or len(set(names)) != len(names):
        out.append("not each of the 63 live features exactly once")
    return out


def test_the_contract_check_refuses_what_the_engine_refuses():
    good = next(iter(_DATA["by_renderer"].values()))[0]["feature_status"]
    assert contract_violations(good) == []
    first = good.split(",")[0]
    for bad in (good + "," + first,                                   # a duplicate
                good.replace(first, first.split("|")[0] + "|2", 1),   # status 2
                good.replace(first, first.split("|")[0] + "|1|X", 1), # failure with 1
                good.replace(first, first.split("|")[0] + "|4", 1),   # 4 without failure
                ",".join(good.split(",")[1:]),                        # a feature missing
                good + ",ALL|1",                                      # ALL
                good.replace(",", ", ", 1)):                          # a space
        assert contract_violations(bad), bad


def test_every_measured_device_meets_the_contract():
    for devs in _DATA["by_renderer"].values():
        for d in devs:
            assert contract_violations(d["feature_status"]) == [], d["label"]


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_every_session_emits_a_value_the_engine_accepts(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    for seed in range(1, 600, 3):
        prefs = translate_profile_to_prefs(generate_profile(seed=seed), timezone="America/New_York")
        assert contract_violations(prefs["zoom.stealth.gfx.feature_status"]) == [], seed
        # the engine's gate also refuses an empty renderer or vendor
        assert prefs["zoom.stealth.webgl.renderer"] and prefs["zoom.stealth.webgl.vendor"], seed


def test_a_session_without_a_persona_is_refused_not_emptied():
    # until 2026-11-01 an unknown renderer emitted "" for both, and WebGL went to the host
    import dataclasses
    prof = generate_profile(seed=12)
    alien = dataclasses.replace(prof, gpu=dataclasses.replace(prof.gpu, renderer="ANGLE (Nobody, Nothing)", device=""))
    with pytest.raises(ValueError, match="no validated GPU persona"):
        translate_profile_to_prefs(alien, timezone="America/New_York")


def test_a_profile_without_a_device_label_still_declares_one():
    # a Profile made before the device existed carries "": the seed's own draw
    import dataclasses
    prof = generate_profile(seed=12)
    old = dataclasses.replace(prof, gpu=dataclasses.replace(prof.gpu, device=""))
    a = translate_profile_to_prefs(prof, timezone="America/New_York")["zoom.stealth.gfx.feature_status"]
    b = translate_profile_to_prefs(old, timezone="America/New_York")["zoom.stealth.gfx.feature_status"]
    assert a == b
