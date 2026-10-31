"""The WebGPU adapter a page sees is declared, never the host's.

Firefox hands a page the features and limits wgpu reports for the real adapter:
D3D12 on Windows, Vulkan on Linux, and on a Linux machine without a GPU a CPU
adapter. Measured 2026-10-31 with firefox-54 on Linux: isFallbackAdapter true,
an extra "clip-distances" and 14 limits away from retail Firefox 159 for Windows.
Retail's values were the same for an RTX 5080, an Intel iGPU and WARP: constants
of the D3D12 backend, declared once. A D3D10-class GPU has no D3D12 adapter.

The engine (dom/webgpu/StealthWebGPUDeclaration.cpp) refuses a limit list that
misses one of its limits; the names are read from its Adapter.cpp here, so a
Firefox that adds a limit fails in this repo first.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import invisible_core
from _engine_source import EngineSource
from invisible_core.prefs import _apply_codecs

pytestmark = pytest.mark.unit
_SOURCE = EngineSource.pinned()
GPU_POOL = Path(invisible_core.__file__).parent / "_fpforge" / "data" / "webgl_gpu_pool.json"

#: Retail Firefox 159 for Windows, headed, 2026-10-31: navigator.gpu.requestAdapter()
#: features, without "core-features-and-limits", which Firefox adds itself.
RETAIL_FEATURES = [
    "bgra8unorm-storage", "depth-clip-control", "depth32float-stencil8", "dual-source-blending",
    "float32-blendable", "float32-filterable", "indirect-first-instance", "rg11b10ufloat-renderable",
    "shader-f16", "texture-compression-bc", "texture-compression-bc-sliced-3d", "timestamp-query",
]

#: The limits the Linux build answered differently from retail on 2026-10-31.
RETAIL_DIFFERED_ON_LINUX = {
    "maxStorageBufferBindingSize": 2147483644, "minUniformBufferOffsetAlignment": 256,
    "maxBindingsPerBindGroup": 4294967295, "maxDynamicUniformBuffersPerPipelineLayout": 8,
    "maxTextureDimension3D": 2048, "maxComputeWorkgroupSizeZ": 64,
    "maxDynamicStorageBuffersPerPipelineLayout": 4, "maxVertexAttributes": 30,
}


def prefs_for(renderer):
    codec = SimpleNamespace(av1_enabled=True, webm_encoder_enabled=True,
                            mediasource_webm=True, mediasource_mp4=True)
    prefs = {}
    _apply_codecs(prefs, SimpleNamespace(codec=codec, gpu=SimpleNamespace(renderer=renderer)))
    return prefs


def limits(prefs):
    return {k: int(v) for k, v in (l.split("=") for l in prefs["zoom.stealth.webgpu.limits"].split(chr(10)))}


def windows_renderers():
    pool = json.loads(GPU_POOL.read_text(encoding="utf-8"))
    return sorted({e["prefs"]["zoom.stealth.webgl.renderer"] for e in pool["win"]})


def d3d10(renderer):
    return any(s in renderer for s in ("vs_4_0", "vs_4_1", "GeForce 8800 GTX", "Intel 945GM"))


@pytest.mark.parametrize("renderer", windows_renderers())
def test_every_windows_persona_declares_the_adapter_its_gpu_has(renderer):
    prefs = prefs_for(renderer)
    assert prefs["zoom.stealth.webgpu.adapter"] == ("none" if d3d10(renderer) else "d3d12")
    assert prefs["zoom.stealth.webgpu.features"].split(chr(10)) == RETAIL_FEATURES
    assert len(limits(prefs)) == 35


def test_the_limits_are_retail_where_linux_differed():
    got = limits(prefs_for("ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0, D3D11)"))
    assert {k: got[k] for k in RETAIL_DIFFERED_ON_LINUX} == RETAIL_DIFFERED_ON_LINUX


def test_no_feature_is_one_retail_windows_does_not_report():
    # clip-distances is the one the Linux build added.
    assert "clip-distances" not in prefs_for("ANGLE (Intel, Intel(R) HD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)")[
        "zoom.stealth.webgpu.features"]


@pytest.mark.skipif(_SOURCE.unavailable() is not None, reason=_SOURCE.unavailable() or "")
def test_the_limit_names_are_the_engines():
    text = _SOURCE.show("dom/webgpu/Adapter.cpp")
    assert text, f"Adapter.cpp not in {_SOURCE.commit}"
    body = text[text.index("static std::string_view ToJsKey(const Limit limit) {"):]
    body = body[:body.index("\n}\n")]
    engine = set(re.findall(r'return "(\w+)";', body))
    assert len(engine) >= 30, f"ToJsKey moved: {sorted(engine)}"
    assert engine == set(limits(prefs_for("x")))


@pytest.mark.skipif(_SOURCE.unavailable() is not None, reason=_SOURCE.unavailable() or "")
def test_the_engine_reads_the_declaration():
    gate = _SOURCE.show("gfx/thebes/StealthDeclarationGate.cpp") or ""
    for pref in ("zoom.stealth.webgpu.adapter", "zoom.stealth.webgpu.features", "zoom.stealth.webgpu.limits"):
        assert f'"{pref}"' in gate, f"{pref} is not in the engine's declaration gate at {_SOURCE.commit}"
