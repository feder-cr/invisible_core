"""Every video codec WebRTC can encode has a declared encode-support entry, so a
page's mediaCapabilities.encodingInfo({type: "webrtc"}) and the H.264 entries of
RTCRtpSender.getCapabilities never read the OpenH264 plugin or the host's encoders.

The engine hooks WebrtcVideoEncoderFactory::SupportsCodec with
zoom.stealth.media.encode_support, and refuses to start when it is empty (a
declaration-gate row), so a core without it cannot drive the engine at all.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from invisible_core.prefs import _apply_codecs

pytestmark = pytest.mark.unit

#: Firefox's WebRTC video codecs as track mime types, as of Firefox 159.
WEBRTC_VIDEO = {"video/avc", "video/vp8", "video/vp9", "video/av1"}

MODERN = "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0, D3D11)"
D3D10 = "ANGLE (NVIDIA, NVIDIA GeForce 8800 GTX Direct3D11 vs_4_0 ps_4_0, D3D11)"


def composed(av1_enabled=True, renderer=MODERN):
    codec = SimpleNamespace(av1_enabled=av1_enabled, webm_encoder_enabled=True,
                            mediasource_webm=True, mediasource_mp4=True)
    prefs = {}
    _apply_codecs(prefs, SimpleNamespace(codec=codec, gpu=SimpleNamespace(renderer=renderer)))
    return prefs


def table(prefs, name):
    return dict(line.split("|", 1) for line in prefs[name].split(chr(10)) if "|" in line)


@pytest.mark.parametrize("av1", [True, False])
@pytest.mark.parametrize("renderer", [MODERN, D3D10])
def test_the_table_is_never_empty(av1, renderer):
    # Empty or missing: the engine's declaration gate stops the browser.
    assert composed(av1, renderer).get("zoom.stealth.media.encode_support")


@pytest.mark.parametrize("av1", [True, False])
def test_every_webrtc_video_codec_is_declared(av1):
    missing = WEBRTC_VIDEO - set(table(composed(av1), "zoom.stealth.media.encode_support"))
    assert not missing, f"the host's encoders answer encodingInfo for {sorted(missing)}"


def test_every_entry_is_a_token_the_engine_accepts():
    # The same parser as decode_support: anything else falls through to the host.
    assert set(table(composed(), "zoom.stealth.media.encode_support").values()) <= {"no", "sw", "hw", "swhw"}


@pytest.mark.parametrize("renderer", [MODERN, D3D10])
def test_the_values_are_retail_159_for_windows(renderer):
    # Retail Windows 159 encodes all four in software, whatever the GPU.
    assert table(composed(True, renderer), "zoom.stealth.media.encode_support") == {
        "video/avc": "sw", "video/vp8": "sw", "video/vp9": "sw", "video/av1": "sw"}


def test_h264_software_gate_is_open_from_both_tables():
    # The engine opens the H.264 software gate when video/avc carries sw in decode AND encode.
    prefs = composed()
    assert "sw" in table(prefs, "zoom.stealth.media.decode_support")["video/avc"]
    assert "sw" in table(prefs, "zoom.stealth.media.encode_support")["video/avc"]
