"""The declared media tables are retail Windows's on every persona.

Each GPU name the pool presents covers generations with and without VP8, VP9
and AV1 hardware decoders (SanitizeRenderer.cpp: "GeForce GTX 980" is every
GeForce 900 and later), so the page cannot tell, and every persona keeps retail
Windows's answer. Until 2026-11-01 the pool also had Direct3D 10 class
personas (vs_4_0 / vs_4_1, GeForce 8800 GTX, Intel 945GM), whose renderer said
they had no such decoders, and those answered software only; they left the pool
(no Windows 11 driver) and the branch for them with it.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import invisible_core
from invisible_core.prefs import _WIN_DECODING_INFO, _apply_codecs

pytestmark = pytest.mark.unit

POOL = Path(invisible_core.__file__).parent / "_fpforge" / "data" / "webgl_gpu_pool.json"


def tables(renderer, av1=True):
    codec = SimpleNamespace(av1_enabled=av1, webm_encoder_enabled=True,
                            mediasource_webm=True, mediasource_mp4=True)
    prefs = {}
    _apply_codecs(prefs, SimpleNamespace(codec=codec, gpu=SimpleNamespace(renderer=renderer)))
    support = dict(l.split("|", 1) for l in prefs["zoom.stealth.media.decode_support"].split(chr(10)))
    return support, prefs["zoom.stealth.media.decoding_info"]


def windows_renderers():
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    return sorted({e["prefs"]["zoom.stealth.webgl.renderer"] for e in pool["win"]})


@pytest.mark.parametrize("renderer", windows_renderers())
def test_every_persona_keeps_retail_windows_answers(renderer):
    support, info = tables(renderer)
    for mime in ("video/avc", "video/vp8", "video/vp9", "video/av1"):
        assert support[mime] == "swhw", (renderer, mime)
    assert info == _WIN_DECODING_INFO


def test_av1_off_is_no():
    support, _ = tables("ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0, D3D11)", av1=False)
    assert support["video/av1"] == "no"
