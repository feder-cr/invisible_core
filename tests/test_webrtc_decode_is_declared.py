"""Every video codec WebRTC can decode has a declared decode-support entry, so a
page's mediaCapabilities.decodingInfo({type: "webrtc"}) never reads the host's GPU.

From Firefox 159 the WebRTC branch of decodingInfo asks
WebrtcMediaDataDecoder::Supports -> PDMFactory::SupportsAsync, and powerEfficient
is the HardwareDecode bit of the answer. The engine hooks SupportsAsync with
zoom.stealth.media.decode_support, but a track mime type the core does not
declare falls through to the platform modules. _WIN_DECODE_SUPPORT declared
video/avc and video/hevc only, so for VP8, VP9 and AV1 the bit came from the
machine the browser runs on.

The list of codecs is read from the engine (MediaDataCodec.cpp maps each WebRTC
codec to its track mime type), so a Firefox that adds one fails here; the four
known today are also checked without the engine.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from _engine_source import EngineSource
from invisible_core.prefs import _apply_codecs

pytestmark = pytest.mark.unit
_SOURCE = EngineSource.pinned()

#: Firefox's WebRTC video codecs as track mime types, as of Firefox 159.
WEBRTC_VIDEO = {"video/avc", "video/vp8", "video/vp9", "video/av1"}


#: The commonest Windows persona's renderer (webgl_gpu_pool.json).
MODERN = "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0, D3D11)"


def composed(av1_enabled=True, renderer=MODERN):
    """The prefs _apply_codecs writes for an identity with AV1 on or off."""
    codec = SimpleNamespace(av1_enabled=av1_enabled, webm_encoder_enabled=True,
                            mediasource_webm=True, mediasource_mp4=True)
    prefs = {}
    _apply_codecs(prefs, SimpleNamespace(codec=codec, gpu=SimpleNamespace(renderer=renderer)))
    return prefs


def declared(av1_enabled=True, renderer=MODERN):
    """track mime type -> flags, as the engine parses the pref."""
    spec = composed(av1_enabled, renderer)["zoom.stealth.media.decode_support"]
    return dict(line.split("|", 1) for line in spec.split(chr(10)) if "|" in line)


@pytest.mark.parametrize("av1", [True, False])
def test_every_webrtc_video_codec_is_declared(av1):
    missing = WEBRTC_VIDEO - set(declared(av1))
    assert not missing, f"the host's hardware-decode bit reaches decodingInfo for {sorted(missing)}"


@pytest.mark.parametrize("av1", [True, False])
def test_every_declared_entry_is_a_token_the_engine_accepts(av1):
    # Anything else falls through to the real decoders (StealthDeclaredDecodeSupport).
    assert set(declared(av1).values()) <= {"no", "sw", "hw", "swhw"}


def test_the_values_are_retail_159_for_windows():
    # Measured 2026-10-31, headed: [supported, smooth, powerEfficient] all true.
    for mime in WEBRTC_VIDEO:
        assert declared(True)[mime] == "swhw", mime


def test_av1_follows_the_identitys_av1_pref():
    # media.av1.enabled off: every retail decoder declines AV1, the set is empty.
    # Hardware there would contradict the canPlayType "" the same pref gives.
    assert declared(False)["video/av1"] == "no"


def test_linux_keeps_the_vp8_hardware_bit_windows_reports():
    # AdjustWebrtcDecodeSupportVP8 (GTK only) drops it unless this pref is on.
    assert composed()["media.navigator.mediadatadecoder_vp8_hardware_enabled"] is True


@pytest.mark.skipif(_SOURCE.unavailable() is not None, reason=_SOURCE.unavailable() or "")
def test_every_codec_the_engine_gives_webrtc_is_declared():
    text = _SOURCE.show("dom/media/webrtc/libwebrtcglue/MediaDataCodec.cpp")
    assert text, f"MediaDataCodec.cpp not in {_SOURCE.commit}"
    engine = set(re.findall(r'return nsDependentCString\("(video/[\w.-]+)"\)', text))
    assert engine >= {"video/vp8", "video/avc"}, f"the mapping moved: found {sorted(engine)}"
    assert engine - set(declared(True)) == set()
    assert engine - set(declared(False)) == set()
