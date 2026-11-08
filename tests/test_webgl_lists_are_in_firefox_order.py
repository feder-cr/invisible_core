"""Every declared WebGL extension list is in Firefox's own order.

Firefox lists getSupportedExtensions() in the order of its WebGLExtensionID
enum (dom/canvas/WebGLTypes.h, Firefox 159). Retail can produce no other order,
so a declared list in any other order is a list no Windows Firefox gives.
Known-bad: the default WebGL 1 list had EXT_sRGB before EXT_shader_texture_lod.
The engine also enumerates in this order since the extension list became the
host's one decision; the data is kept right at the source anyway.
"""
from __future__ import annotations

import pytest

from invisible_core import _webgl_personas, prefs

pytestmark = pytest.mark.unit

#: WebGLExtensionID, in declaration order (Firefox 159).
FIREFOX_ORDER = (
    "ANGLE_instanced_arrays",
    "EXT_blend_minmax",
    "EXT_clip_control",
    "EXT_color_buffer_float",
    "EXT_color_buffer_half_float",
    "EXT_depth_clamp",
    "EXT_disjoint_timer_query",
    "EXT_float_blend",
    "EXT_frag_depth",
    "EXT_polygon_offset_clamp",
    "EXT_shader_texture_lod",
    "EXT_sRGB",
    "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic",
    "EXT_texture_mirror_clamp_to_edge",
    "EXT_texture_norm16",
    "MOZ_debug",
    "OES_draw_buffers_indexed",
    "OES_element_index_uint",
    "OES_fbo_render_mipmap",
    "OES_standard_derivatives",
    "OES_texture_float",
    "OES_texture_float_linear",
    "OES_texture_half_float",
    "OES_texture_half_float_linear",
    "OES_vertex_array_object",
    "OVR_multiview2",
    "WEBGL_color_buffer_float",
    "WEBGL_compressed_texture_astc",
    "WEBGL_compressed_texture_etc",
    "WEBGL_compressed_texture_etc1",
    "WEBGL_compressed_texture_pvrtc",
    "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders",
    "WEBGL_depth_texture",
    "WEBGL_draw_buffers",
    "WEBGL_explicit_present",
    "WEBGL_lose_context",
    "WEBGL_provoking_vertex",
)


def _lists():
    yield "prefs WebGL 1", prefs._WEBGL1_EXTENSIONS
    yield "prefs WebGL 2", prefs._WEBGL2_EXTENSIONS
    yield "persona WebGL 1", _webgl_personas._EXT1
    yield "persona WebGL 2", _webgl_personas._EXT2


@pytest.mark.parametrize("name, value", list(_lists()))
def test_the_list_is_in_firefox_s_order(name, value):
    names = [n for n in value.split(",") if n]
    assert all(n in FIREFOX_ORDER for n in names), [n for n in names if n not in FIREFOX_ORDER]
    positions = [FIREFOX_ORDER.index(n) for n in names]
    assert positions == sorted(positions), name
