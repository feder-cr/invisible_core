"""The WebGL pool declares what the GPU is, never what a context is doing.

getParameter answers two kinds of value. An implementation constant (a MAX_*,
a range, MAX_SAMPLES) belongs to the GPU and is the persona's to declare. Context
state (DEPTH_FUNC, the stencil masks, PACK_ALIGNMENT, ACTIVE_TEXTURE, the bound
framebuffer's bits) starts at the value the specification fixes and moves with
every call the page makes; Firefox answers it from the live context, the same
on every host.

The pool carried 63 pieces of state from the telemetry it was extracted from,
and the engine answers a declared value in place of Firefox's. So
after gl.depthFunc(gl.ALWAYS), getParameter(DEPTH_FUNC) still answered 513
(LESS, the initial value): whatever the page set, the declared value won. And
the stencil masks, whose initial value is 0xFFFFFFFF, had been stored clamped to
int32: every Windows persona answered 2147483647 where retail 159 answers
4294967295 (host gate, 2026-10-31).

One piece of state was also a capability: two personas answered SAMPLES 0,
a GPU without multisampling. That is declared the way Firefox itself knows it,
webgl.msaa-samples 0, from which Firefox turns antialias off and answers
SAMPLES and SAMPLE_BUFFERS for whatever the page binds.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import invisible_core

pytestmark = pytest.mark.unit

POOL = Path(invisible_core.__file__).parent / "_fpforge" / "data" / "webgl_gpu_pool.json"
PARAMS = ("zoom.stealth.webgl.int_params", "zoom.stealth.webgl.int2_params",
          "zoom.stealth.webgl.float_params")

#: Every getParameter enum whose answer is fixed by the implementation.
CONSTANTS = {
    0x0D33: "MAX_TEXTURE_SIZE", 0x0D3A: "MAX_VIEWPORT_DIMS", 0x0D50: "SUBPIXEL_BITS",
    0x8073: "MAX_3D_TEXTURE_SIZE", 0x80E8: "MAX_ELEMENTS_VERTICES", 0x80E9: "MAX_ELEMENTS_INDICES",
    0x846D: "ALIASED_POINT_SIZE_RANGE", 0x846E: "ALIASED_LINE_WIDTH_RANGE",
    0x84E8: "MAX_RENDERBUFFER_SIZE", 0x84FD: "MAX_TEXTURE_LOD_BIAS", 0x851C: "MAX_CUBE_MAP_TEXTURE_SIZE",
    0x8824: "MAX_DRAW_BUFFERS", 0x8869: "MAX_VERTEX_ATTRIBS", 0x8872: "MAX_TEXTURE_IMAGE_UNITS",
    0x88FF: "MAX_ARRAY_TEXTURE_LAYERS", 0x8904: "MIN_PROGRAM_TEXEL_OFFSET", 0x8905: "MAX_PROGRAM_TEXEL_OFFSET",
    0x8A2B: "MAX_VERTEX_UNIFORM_BLOCKS", 0x8A2D: "MAX_FRAGMENT_UNIFORM_BLOCKS",
    0x8A2E: "MAX_COMBINED_UNIFORM_BLOCKS", 0x8A2F: "MAX_UNIFORM_BUFFER_BINDINGS",
    0x8A30: "MAX_UNIFORM_BLOCK_SIZE", 0x8A31: "MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS",
    0x8A33: "MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS", 0x8A34: "UNIFORM_BUFFER_OFFSET_ALIGNMENT",
    0x8B49: "MAX_FRAGMENT_UNIFORM_COMPONENTS", 0x8B4A: "MAX_VERTEX_UNIFORM_COMPONENTS",
    0x8B4B: "MAX_VARYING_COMPONENTS", 0x8B4C: "MAX_VERTEX_TEXTURE_IMAGE_UNITS",
    0x8B4D: "MAX_COMBINED_TEXTURE_IMAGE_UNITS", 0x8C80: "MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS",
    0x8C8A: "MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS", 0x8C8B: "MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS",
    0x8CDF: "MAX_COLOR_ATTACHMENTS", 0x8D57: "MAX_SAMPLES", 0x8D6B: "MAX_ELEMENT_INDEX",
    0x8DFB: "MAX_VERTEX_UNIFORM_VECTORS", 0x8DFC: "MAX_VARYING_VECTORS",
    0x8DFD: "MAX_FRAGMENT_UNIFORM_VECTORS", 0x9111: "MAX_SERVER_WAIT_TIMEOUT",
    0x9122: "MAX_VERTEX_OUTPUT_COMPONENTS", 0x9125: "MAX_FRAGMENT_INPUT_COMPONENTS",
    0x9247: "MAX_CLIENT_WAIT_TIMEOUT_WEBGL", 0x9631: "MAX_VIEWS_OVR",
}


def pool():
    return json.loads(POOL.read_text(encoding="utf-8"))


def every_persona():
    return [(os_, i, p) for os_ in ("win", "mac", "lin") for i, p in enumerate(pool()[os_])]


def params(prefs, key="zoom.stealth.webgl.int_params"):
    return {int(e.split("|", 1)[0]): e.split("|", 1)[1] for e in (prefs.get(key) or "").split(",") if "|" in e}


@pytest.mark.parametrize("os_,i,persona", every_persona(), ids=lambda v: str(v)[:12])
def test_a_persona_declares_constants_and_no_context_state(os_, i, persona):
    state = {"%s/0x%04X" % (key.rsplit(".", 1)[1], enum)
             for key in PARAMS for enum in params(persona["prefs"], key) if enum not in CONSTANTS}
    assert state == set(), "%s[%d] declares context state" % (os_, i)


def test_a_gpu_without_multisampling_is_declared_to_firefox():
    no_msaa = [p["prefs"] for p in pool()["win"] if p["prefs"].get("webgl.msaa-samples") == 0]
    assert len(no_msaa) == 2
    for p in pool()["win"]:
        assert p["prefs"].get("webgl.msaa-samples", 4) in (0, 4)


@pytest.mark.parametrize("i,persona", list(enumerate(pool()["win"])), ids=lambda v: str(v)[:12])
def test_the_vertex_uniform_vectors_follow_angle(i, persona):
    """Not a leak, though the host gate saw 4095 against retail's 4096: ANGLE's
    D3D11 backend gives up vertex constant register zero on NVIDIA only
    (skipVSConstantRegisterZero, renderer11_utils.cpp in Firefox 159), and the
    retail machine is not NVIDIA. The components follow the vectors."""
    nvidia = re.search(r"\bNVIDIA\b", persona["prefs"]["zoom.stealth.webgl.renderer"])
    got = params(persona["prefs"])
    vectors = 4095 if nvidia else 4096
    assert int(got[0x8DFB]) == vectors
    if 0x8B4A in got:
        assert int(got[0x8B4A]) == vectors * 4
