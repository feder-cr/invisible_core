"""VP8, VP9 and AV1 hardware decoding follows the GPU the page sees.

The declared media tables answered "hardware" for these codecs on every
identity, also on the Windows personas whose WebGL renderer reads as Direct3D 10
class (shader model vs_4_0 / vs_4_1, GeForce 8800 GTX, Intel 945GM). That
hardware has no VP8, VP9 or AV1 decoder, so a page that reads
WEBGL_debug_renderer_info and then decodingInfo saw two answers that cannot both
be true. Those personas now answer software only, in the decode-support table
(WebRTC decodingInfo) and in powerEfficient of the decodingInfo table (files).

The other renderer names Firefox reports each cover GPU generations with and
without these decoders (SanitizeRenderer.cpp: "GeForce GTX 980" is every
GeForce 900 and later), so the page cannot tell and they keep retail Windows's
answer.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import invisible_core
from invisible_core.prefs import _apply_codecs

pytestmark = pytest.mark.unit

POOL = Path(invisible_core.__file__).parent / "_fpforge" / "data" / "webgl_gpu_pool.json"
NEWER = {"vp8", "vp9", "av1", "vp09", "av01"}


def tables(renderer, av1=True):
    codec = SimpleNamespace(av1_enabled=av1, webm_encoder_enabled=True,
                            mediasource_webm=True, mediasource_mp4=True)
    prefs = {}
    _apply_codecs(prefs, SimpleNamespace(codec=codec, gpu=SimpleNamespace(renderer=renderer)))
    support = dict(l.split("|", 1) for l in prefs["zoom.stealth.media.decode_support"].split(chr(10)))
    info = {tuple(l.split("|")[:2]): l.split("|")[2]
            for l in prefs["zoom.stealth.media.decoding_info"].split(chr(10))}
    return support, info


def windows_renderers():
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    return sorted({e["prefs"]["zoom.stealth.webgl.renderer"] for e in pool["win"]})


def d3d10(renderer):
    return any(s in renderer for s in ("vs_4_0", "vs_4_1", "GeForce 8800 GTX", "Intel 945GM"))


def test_the_pool_has_both_kinds_so_this_test_means_something():
    kinds = {d3d10(r) for r in windows_renderers()}
    assert kinds == {True, False}


@pytest.mark.parametrize("renderer", windows_renderers())
def test_newer_codecs_have_hardware_only_on_a_gpu_that_can(renderer):
    support, info = tables(renderer)
    old = d3d10(renderer)
    for mime in ("video/vp8", "video/vp9", "video/av1"):
        assert support[mime] == ("sw" if old else "swhw"), (renderer, mime)
    for (container, family), digits in info.items():
        if family in NEWER and digits[0] == "1":
            assert digits[2] == ("0" if old else digits[2]), (renderer, container, family)
    # H.264 stays: these GPUs mostly decode it, and the population is mixed.
    assert support["video/avc"] == "swhw"
    assert info[("video/mp4", "avc1")] == "111"


def test_a_modern_renderer_keeps_retail_windows_answers():
    support, info = tables("ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0, D3D11)")
    assert support["video/vp9"] == "swhw" and info[("video/mp4", "av01")] == "111"


def test_av1_off_stays_no_on_any_gpu():
    support, _ = tables("ANGLE (Intel, Intel 945GM Direct3D11 vs_4_0 ps_4_0, D3D11)", av1=False)
    assert support["video/av1"] == "no"
