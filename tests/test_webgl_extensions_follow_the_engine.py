"""The WebGL extension lists a persona reports must be the ones the ENGINE's
version reports on that GPU, not the ones of the Firefox the data was captured on.

The engine returns `zoom.stealth.webgl.extensions` / `webgl2.extensions`
verbatim (ClientWebGLContext::GetSupportedExtensions), so the pool's lists ARE
what a page reads. They come from an offline extract of real-Firefox telemetry
taken on older Firefox, with an older ANGLE.

Measured 2026-10-29 on this Windows 11 host (ANGLE D3D11, Intel), official
Firefox builds with a fresh profile and no automation, the same page in both:

    Firefox 151.0     webgl 27, webgl2 14, no EXT_depth_clamp
    Firefox 157.0.1   webgl 28, webgl2 15, EXT_depth_clamp in both
    Firefox 159.0     webgl 31, webgl2 19 (measured 2026-10-30, same host): +EXT_clip_control,
                      +EXT_polygon_offset_clamp, +EXT_texture_mirror_clamp_to_edge in both,
                      +EXT_texture_norm16 in WebGL2 only

The code for EXT_depth_clamp is already in 151 (`gl->IsSupported(depth_clamp)`);
what changed is ANGLE, which advertises depth clamp from the 157 engine on. Our
157 engine reported the 151 lists - 27 and 14 - on every seed: a page reading the
User-Agent saw Firefox 157 and, in the same visit, the WebGL of an older one.
Depth clamp is a D3D11 rasterizer state (DepthClipEnable) available at every
feature level the pool's personas declare, so it is not GPU-dependent. The 159
additions are measured on one GPU family (Intel HD) and given to every persona
the same way; a non-Intel official 159 measurement would sharpen them.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from _engine_source import EngineSource

pytestmark = pytest.mark.unit

_POOL = pathlib.Path(__import__("invisible_core").__file__).parent / "_fpforge" / "data" / "webgl_gpu_pool.json"
_W1, _W2 = "zoom.stealth.webgl.extensions", "zoom.stealth.webgl2.extensions"
_SOURCE = EngineSource.pinned()

#: Official Firefox 159.0, Windows 11, ANGLE (Intel, Intel(R) HD Graphics
#: Direct3D11 vs_5_0 ps_5_0), measured 2026-10-30 (the set; ordered as the 159
#: engine's WebGLExtensionID returns them).
OFFICIAL_159_INTEL_WEBGL = [
    "ANGLE_instanced_arrays", "EXT_blend_minmax", "EXT_clip_control", "EXT_color_buffer_half_float",
    "EXT_depth_clamp", "EXT_float_blend", "EXT_frag_depth", "EXT_polygon_offset_clamp",
    "EXT_shader_texture_lod", "EXT_sRGB", "EXT_texture_compression_bptc", "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic", "EXT_texture_mirror_clamp_to_edge", "OES_element_index_uint",
    "OES_fbo_render_mipmap", "OES_standard_derivatives", "OES_texture_float", "OES_texture_float_linear",
    "OES_texture_half_float", "OES_texture_half_float_linear", "OES_vertex_array_object",
    "WEBGL_color_buffer_float", "WEBGL_compressed_texture_s3tc", "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info", "WEBGL_debug_shaders", "WEBGL_depth_texture", "WEBGL_draw_buffers",
    "WEBGL_lose_context", "WEBGL_provoking_vertex",
]
OFFICIAL_159_INTEL_WEBGL2 = [
    "EXT_clip_control", "EXT_color_buffer_float", "EXT_depth_clamp", "EXT_float_blend",
    "EXT_polygon_offset_clamp", "EXT_texture_compression_bptc", "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic", "EXT_texture_mirror_clamp_to_edge", "EXT_texture_norm16",
    "OES_draw_buffers_indexed", "OES_texture_float_linear", "OVR_multiview2", "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb", "WEBGL_debug_renderer_info", "WEBGL_debug_shaders",
    "WEBGL_lose_context", "WEBGL_provoking_vertex",
]
#: The 159 additions, every one of which a 159 identity must report.
NEW_IN_159_WEBGL = ["EXT_clip_control", "EXT_polygon_offset_clamp", "EXT_texture_mirror_clamp_to_edge"]
NEW_IN_159_WEBGL2 = NEW_IN_159_WEBGL + ["EXT_texture_norm16"]

#: Official Firefox 157.0.1 (BuildID 20261005135250), Windows 11, ANGLE (Intel,
#: Intel(R) HD Graphics Direct3D11 vs_5_0 ps_5_0), measured 2026-10-29, in the
#: order the engine returns them. Kept as the record of the previous port.
OFFICIAL_157_INTEL_WEBGL = [
    "ANGLE_instanced_arrays", "EXT_blend_minmax", "EXT_color_buffer_half_float", "EXT_depth_clamp",
    "EXT_float_blend", "EXT_frag_depth", "EXT_shader_texture_lod", "EXT_sRGB", "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc", "EXT_texture_filter_anisotropic", "OES_element_index_uint",
    "OES_fbo_render_mipmap", "OES_standard_derivatives", "OES_texture_float", "OES_texture_float_linear",
    "OES_texture_half_float", "OES_texture_half_float_linear", "OES_vertex_array_object",
    "WEBGL_color_buffer_float", "WEBGL_compressed_texture_s3tc", "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info", "WEBGL_debug_shaders", "WEBGL_depth_texture", "WEBGL_draw_buffers",
    "WEBGL_lose_context", "WEBGL_provoking_vertex",
]
OFFICIAL_157_INTEL_WEBGL2 = [
    "EXT_color_buffer_float", "EXT_depth_clamp", "EXT_float_blend", "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc", "EXT_texture_filter_anisotropic", "OES_draw_buffers_indexed",
    "OES_texture_float_linear", "OVR_multiview2", "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb", "WEBGL_debug_renderer_info", "WEBGL_debug_shaders",
    "WEBGL_lose_context", "WEBGL_provoking_vertex",
]


def _windows():
    return json.loads(_POOL.read_text(encoding="utf-8"))["win"]


def _split(value):
    return value.split(",") if value else []


def test_every_windows_persona_reports_depth_clamp_as_the_157_engine_does():
    missing = []
    for e in _windows():
        p = e["prefs"]
        if "EXT_depth_clamp" not in _split(p[_W1]):
            missing.append(("webgl", p["zoom.stealth.webgl.renderer"]))
        if _split(p.get(_W2)) and "EXT_depth_clamp" not in _split(p[_W2]):
            missing.append(("webgl2", p["zoom.stealth.webgl.renderer"]))
    assert not missing, f"lists without EXT_depth_clamp, which Firefox 157's ANGLE reports: {missing}"


def test_every_windows_persona_reports_what_the_159_engine_added():
    missing = []
    for e in _windows():
        p = e["prefs"]
        missing += [("webgl", n, p["zoom.stealth.webgl.renderer"]) for n in NEW_IN_159_WEBGL if n not in _split(p[_W1])]
        if _split(p.get(_W2)):
            missing += [("webgl2", n, p["zoom.stealth.webgl.renderer"]) for n in NEW_IN_159_WEBGL2 if n not in _split(p[_W2])]
    assert not missing, f"lists without an extension Firefox 159's engine reports: {missing}"


def test_a_persona_of_the_measured_gpu_family_matches_the_official_build_exactly():
    pairs = [(_split(e["prefs"][_W1]), _split(e["prefs"].get(_W2))) for e in _windows()
             if e["prefs"]["zoom.stealth.webgl.renderer"].startswith("ANGLE (Intel, Intel(R) HD Graphics Direct3D11")]
    assert (OFFICIAL_159_INTEL_WEBGL, OFFICIAL_159_INTEL_WEBGL2) in pairs, (
        "no Intel HD persona reports what official Firefox 159 reports on that GPU family")


@pytest.mark.skipif(_SOURCE.unavailable() is not None, reason=_SOURCE.unavailable() or "")
def test_every_list_is_in_the_order_the_engine_returns_and_names_only_its_extensions():
    text = _SOURCE.show("dom/canvas/WebGLTypes.h")
    assert text, f"dom/canvas/WebGLTypes.h is not in {_SOURCE.commit}"
    body = re.search(r"enum class WebGLExtensionID[^{]*\{(.*?)\bMax\b", text, re.S).group(1)
    order = [m.group(1) for m in re.finditer(r"^\s*([A-Z][A-Za-z0-9_]*),", body, re.M)]
    wrong = []
    for e in _windows():
        for key in (_W1, _W2):
            names = _split(e["prefs"].get(key))
            unknown = [n for n in names if n not in order]
            if unknown or names != sorted(names, key=order.index):
                wrong.append((key, e["prefs"]["zoom.stealth.webgl.renderer"], unknown))
    assert not wrong, f"lists a real Firefox could not return (unknown names or engine order broken): {wrong}"
