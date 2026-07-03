"""Empirically-calibrated WebGL GPU personas for Windows ANGLE D3D11.

We expose a FALSE GPU (this is a multi-user tool — never leak each host's real GPU),
chosen deterministically per seed from a small set of renderer-string "buckets" that
Firefox's SanitizeRenderer emits and that FP Pro's tampering_ml scores as CLEAN.

## What actually gates a persona (calibrated 2026-06-14, supersedes the old theory)

The blocker is NOT anti_detect and NOT a "render-vs-renderer" check. It is FP Pro's
**tampering_ml** (gate <=0.5), a holistic ML coherence score. We reverse-engineered its
GPU sensitivity with single-variable A/Bs on demo.fingerprint.com (deterministic per
(seed, renderer, IP); tools in tests/_gpu_isolate.py / _gpu_landscape.py / _gpu_sweep.py /
_gpu_sweep2.py / _gpu_persona_pure.py). Findings:

  1. tampering_ml = f(renderer STRING, seed baseline = canvas/audio). The renderer string
     carries a STABLE per-bucket penalty; the seed sets the floor it adds to.
  2. gpu_class is IRRELEVANT to tampering_ml (nv_980 scored identically on mid_range /
     high_end / premium / workstation). So pairing a fake GPU with a "matching" hardware
     tier does NOT help the score (we still set a coherent class — see gpu_class below —
     for OTHER detectors that cross-check cores/screen, just not for this).
  3. It is NOT render-consistency: a cross-vendor AMD string is CLEAN on our Intel-Arc
     host. So the real silicon's pixels are not the dominant signal; falsifying to a
     different vendor works — IF the string is one FP Pro scores low.

Sweep over all 10 Windows SanitizeRenderer buckets x 10 seeds (clean = tml<=0.5 AND not
anti_detect), on our Intel Arc A750 host:
  - amd_r9 (Radeon R9 200 Series) ...... 10/10 clean, max tml 0.346   <- SHIP
  - intel_arc (Arc A750) ............... 10/10 clean, max tml 0.377   <- SHIP
  - amd_hd5850 ......................... 9/10 (fails the hardest seed)
  - amd_hd3200 / intel_hd .............. 6/10 (seed-dependent, risky)
  - intel_hd400 ........................ 3/10
  - ALL NVIDIA (8800/480/980) .......... 0/10 (penalized everywhere, ~0.7-0.99)
  - intel_945 (ancient Intel) .......... 0/10
So only TWO buckets are robustly clean across profiles. We ship exactly those, weighted
to real-world prevalence ("Radeon R9 200 Series" is the bucket for ALL modern AMD = a big
real slice; "Arc A750" covers Intel discrete = rarer). Cross-vendor, so the fleet is not a
single-GPU cluster. More names require lowering the seed floor first (see CAVEAT 2).

## ⚠️ CAVEATS
 1. HOST-INDEPENDENCE NOT PROVEN. Everything above was measured on ONE host (Intel Arc
    A750). The host's real render is embedded in the seed baseline, so the clean-bucket set
    *might* be host-dependent (on a real NVIDIA host, maybe nv_980 is clean and amd_r9 is
    not). This MUST be validated on a non-Arc machine before trusting it fleet-wide; if it
    turns out host-dependent, add a pre-launch host-GPU-class probe and pick a bucket per
    detected class. Until then: safe for Arc hosts (incl. the dev's), unvalidated elsewhere.
 2. DIVERSITY CEILING = 2 names because "hard" seeds (high canvas/audio floor, e.g. seed 4
    ~0.35) only stay clean on the 2 best buckets. Lowering that floor (an fpforge CPT fix —
    candidate: 8-channel audio + 1TB storage emitted on a mid_range profile) would unlock
    amd_hd5850 / intel_hd for more seeds => up to ~5 names. Follow-up, not done yet.

## Load-bearing format requirements (unchanged, still true)
 - renderer MUST end ", D3D11)" (full ANGLE wire format) or SanitizeRenderer returns
   "Generic Renderer" (a tell). The C++ passes our string through SanitizeRenderer, which
   buckets "AMD Radeon R9 200 Series" -> "Radeon R9 200 Series" and "Arc A750" -> itself.
 - the forced extension list MUST be the EXACT NATIVE ORDER getSupportedExtensions returns.
   The set+order is fixed by Firefox+ANGLE on D3D11 FL11_0 (VENDOR-INDEPENDENT — verified
   via 20-agent source study), so ONE list is correct for both personas. A reorder is caught
   (tampering_ml 0.34 -> 0.84). The lists below are the verbatim native-order Arc capture.

Calibration data + sweep tooling live in the local workbench (not shipped).
"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional

# Vendor-independent ext lists (native order, Arc host capture). Identical for every persona
# because the set+order is fixed by Firefox+ANGLE on D3D11 FL11_0, not by the GPU vendor.
_EXT1 = (
    "ANGLE_instanced_arrays,EXT_blend_minmax,EXT_color_buffer_half_float,EXT_float_blend,"
    "EXT_frag_depth,EXT_shader_texture_lod,EXT_sRGB,EXT_texture_compression_bptc,"
    "EXT_texture_compression_rgtc,EXT_texture_filter_anisotropic,OES_element_index_uint,"
    "OES_fbo_render_mipmap,OES_standard_derivatives,OES_texture_float,OES_texture_float_linear,"
    "OES_texture_half_float,OES_texture_half_float_linear,OES_vertex_array_object,"
    "WEBGL_color_buffer_float,WEBGL_compressed_texture_s3tc,WEBGL_compressed_texture_s3tc_srgb,"
    "WEBGL_debug_renderer_info,WEBGL_debug_shaders,WEBGL_depth_texture,WEBGL_draw_buffers,"
    "WEBGL_lose_context,WEBGL_provoking_vertex"
)
_EXT2 = (
    "EXT_color_buffer_float,EXT_float_blend,EXT_texture_compression_bptc,"
    "EXT_texture_compression_rgtc,EXT_texture_filter_anisotropic,OES_draw_buffers_indexed,"
    "OES_texture_float_linear,OVR_multiview2,WEBGL_compressed_texture_s3tc,"
    "WEBGL_compressed_texture_s3tc_srgb,WEBGL_debug_renderer_info,WEBGL_debug_shaders,"
    "WEBGL_lose_context,WEBGL_provoking_vertex"
)


# ── Real-Firefox GPU pool (2026-06-18, supersedes the 2-bucket sweep above) ───────────────
# The personas are now sourced from `_fpforge/data/webgl_gpu_pool.json` — an OFFLINE extract
# of camoufox's real-Firefox WebGL telemetry DB (17 Windows GPUs with their REAL per-OS
# prevalence AND the full coherent WebGL fingerprint: renderer + vendor + extensions +
# ~100 getParameter values + shader-precision formats). prefs.py applies ALL of these, not
# just the renderer string. The linchpin A/B (2026-06-18) proved that the OLD "NVIDIA 0/10"
# verdict was an artifact of spoofing the renderer string over the host's REAL (Arc) params:
# FP Pro cross-checks renderer<->params, so a GTX 980 string over Arc params mismatched
# (~0.7-0.85). Injecting camoufox's REAL GTX 980 params makes it coherent (tml median 0.333,
# flags clean). So the params are NOT vendor-independent (the old assumption) and per-GPU
# real data is what unlocks the full real GPU mix — including NVIDIA (~47% of real FF-Win),
# which we no longer gate.
_ENABLED = True
_POOL_PATH = __import__("pathlib").Path(__file__).parent / "_fpforge" / "data" / "webgl_gpu_pool.json"
_GPU_POOL_CACHE: Optional[List[Dict]] = None


def _gpu_pool() -> List[Dict]:
    """Lazy-load the Windows GPU pool (we always claim Windows). Each entry:
    {key, renderer (input form), vendor, gpu_class (via classify_gpu), prefs (full
    zoom.stealth.webgl.* override dict), weight (real per-OS prevalence)}."""
    global _GPU_POOL_CACHE
    if _GPU_POOL_CACHE is not None:
        return _GPU_POOL_CACHE
    import json
    from ._fpforge._sampler import classify_gpu  # lazy import → no module cycle
    raw = json.loads(_POOL_PATH.read_text(encoding="utf-8"))
    pool: List[Dict] = []
    for e in raw.get("win", []):
        prefs = e["prefs"]
        rend_in = prefs["zoom.stealth.webgl.renderer"]
        cls = classify_gpu({"renderer": rend_in,
                            "vendor": prefs.get("zoom.stealth.webgl.vendor", "")})
        pool.append({
            "key": e.get("renderer_out", rend_in)[:48],
            "renderer": rend_in,
            "vendor": prefs["zoom.stealth.webgl.vendor"],
            "gpu_class": cls,
            "prefs": prefs,
            "weight": float(e["prob"]),
        })
    _GPU_POOL_CACHE = pool
    return pool


def select_persona(seed: int) -> Optional[Dict]:
    """Deterministic, prevalence-weighted GPU persona for this seed — on EVERY host.

    Same seed -> same persona (fppro_consistency: identity stable per seed). Different seeds
    spread across the REAL Windows GPU mix by prevalence. Returns the Windows-ANGLE persona on
    Linux/Mac too: we must always look Windows, and the C++ WebGL override (SanitizeRenderer +
    pref-driven params/extensions) is platform-independent, so the same Windows GPU is presented
    on any host without consulting the real GL backend (no more Linux "Generic Renderer")."""
    if not _ENABLED:
        return None
    pool = _gpu_pool()
    if not pool:
        return None
    total = sum(p["weight"] for p in pool) or 1.0
    h = ((int(seed) * 2654435761) % 1_000_003) / 1_000_003.0 * total
    cum = 0.0
    for p in pool:
        cum += p["weight"]
        if h < cum:
            return p
    return pool[-1]


def forced_gpu_class(seed: int) -> Optional[str]:
    """The gpu_class the forge conditions the bundle on (== the selected GPU's class via
    classify_gpu), so cores/screen/fonts stay coherent with the GPU we expose. Does NOT
    affect FP Pro tampering_ml (proven) but matters for detectors that cross-check hardware
    tier. None on Linux."""
    p = select_persona(seed)
    return p["gpu_class"] if p else None


# ── Render-noise seed pool (canvas/WebGL gamma) ──────────────────────────────
# zoom.stealth.fpp.hw_seed drives the per-seed canvas2D + WebGL readPixels gamma
# LUT in C++. The render-image HASH it produces is the DOMINANT FP Pro tampering_ml
# driver (proven 2026-06-14: holding a fixed profile and varying ONLY hw_seed moved
# tml 0.25->0.75). The monotonic gamma preserves the GPU's render structure, so some
# hw_seeds yield a "suspicious" render hash. We therefore DECOUPLE the render-noise
# seed from the identity seed and pick from a calibrated pool of hw_seeds that score
# CLEAN even on the hardest attribute profile (sweep 1..30 vs the worst seed: these
# 14 all gave tml<=0.285). Diversity is preserved (14 distinct render hashes spread
# across the population — real GPUs cluster to few canvas hashes anyway); identity
# stays per-seed (the rest of the fingerprint differs). Same seed -> same render seed
# (fppro_consistency holds).
# CAVEAT: the render hash = f(host GPU render, gamma), so this pool is calibrated on
# the Intel-Arc host. On other GPUs the clean set may differ (host-independence open,
# same as the personas) — Option B (substitution = GPU-independent render hash) would
# remove that dependency. Validate per-host or move to B before trusting fleet-wide.
# RECALIBRATED 2026-06-18 for the real-Firefox GPU mix (incl NVIDIA, which is more
# consistency-sensitive than the old amd/arc personas). Swept hw_seed 0..30 on the hottest
# persona (NVIDIA GTX 980) through a residential exit: these 9 stayed well within the clean
# band with a wide margin to the rest. The old pool's picks scored dirty on NVIDIA (clean
# only on the retired amd/arc mix) → dropped. NVIDIA is the worst case, so these are clean on
# amd/intel too. hw_seed = the canvas/WebGL gamma render hash (the dominant consistency-score
# driver); host-calibrated.
# 2026-06-21: with WebGL Option B (zoom.stealth.webgl.substitute_pixels, ON in prefs.py) the WebGL
# render hash is hash(seed,idx) = HOST-INDEPENDENT, so this list NO LONGER needs per-host calibration
# — it only supplies per-session diversity. A 2026-06-21 attempt to re-calibrate it per-host FAILED
# cross-OS: hw_seed clean on Windows went dirty on the Linux GL backend (b008 0.034->0.839; Win-dirty
# {7,11,20,27} = Linux-clean and vice-versa; + identity×hw_seed interaction on Linux). That proved
# calibration can't work cross-host → substitution replaces it. Kept the original diverse 9-set.
CLEAN_RENDER_SEEDS = [0, 5, 6, 9, 11, 16, 19, 20, 28]


def render_noise_seed(seed: int) -> int:
    """Deterministic clean render-noise seed for hw_seed (decoupled from identity).

    Maps the identity seed into CLEAN_RENDER_SEEDS so every session gets a calibrated
    clean canvas/WebGL render hash while keeping per-user diversity. Stable per seed."""
    return CLEAN_RENDER_SEEDS[(int(seed) * 2654435761) % len(CLEAN_RENDER_SEEDS)]
