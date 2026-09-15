"""Empirically-calibrated WebGL GPU personas for Windows ANGLE D3D11.

We expose a FALSE GPU (this is a multi-user tool - never leak each host's real GPU),
chosen deterministically per seed from a small set of renderer-string "buckets" that
Firefox's SanitizeRenderer emits and that FP Pro's tampering_ml scores as CLEAN.

## What actually gates a persona (calibrated 2026-06-14, supersedes the old theory)

The blocker is NOT anti_detect and NOT a "render-vs-renderer" check. It is FP Pro's
**tampering_ml** (gate <=0.5), a holistic ML coherence score. We reverse-engineered its
GPU sensitivity with single-variable A/Bs on demo.fingerprint.com (deterministic per
(seed, renderer, IP); tools in tests/probes/gpu_webgl/: _gpu_isolate.py, _gpu_landscape.py,
_gpu_sweep.py, _gpu_sweep2.py, _gpu_persona_pure.py). Findings:

  1. tampering_ml = f(renderer STRING, seed baseline = canvas/audio). The renderer string
     carries a STABLE per-bucket penalty; the seed sets the floor it adds to.
  2. gpu_class is IRRELEVANT to tampering_ml (nv_980 scored identically on mid_range /
     high_end / premium / workstation). So pairing a fake GPU with a "matching" hardware
     tier does NOT help the score (we still set a coherent class - see gpu_class below -
     for OTHER detectors that cross-check cores/screen, just not for this).
  3. It is NOT render-consistency: a cross-vendor AMD string is CLEAN on our Intel-Arc
     host. So the real silicon's pixels are not the dominant signal; falsifying to a
     different vendor works - IF the string is one FP Pro scores low.

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
    ~0.35) only stay clean on the 2 best buckets. Lowering that floor (an fpforge CPT fix -
    candidate: 8-channel audio + 1TB storage emitted on a mid_range profile) would unlock
    amd_hd5850 / intel_hd for more seeds => up to ~5 names. Follow-up, not done yet.

## Load-bearing format requirements (unchanged, still true)
 - renderer MUST end ", D3D11)" (full ANGLE wire format) or SanitizeRenderer returns
   "Generic Renderer" (a tell). The C++ passes our string through SanitizeRenderer, which
   buckets "AMD Radeon R9 200 Series" -> "Radeon R9 200 Series" and "Arc A750" -> itself.
 - the forced extension list MUST be the EXACT NATIVE ORDER getSupportedExtensions returns.
   The set+order is fixed by Firefox+ANGLE on D3D11 FL11_0 (VENDOR-INDEPENDENT - verified
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
# The personas are now sourced from `_fpforge/data/webgl_gpu_pool.json` - an OFFLINE extract
# of camoufox's real-Firefox WebGL telemetry DB (17 Windows GPUs with their REAL per-OS
# prevalence AND the full coherent WebGL fingerprint: renderer + vendor + extensions +
# ~100 getParameter values + shader-precision formats). prefs.py applies ALL of these, not
# just the renderer string. The linchpin A/B (2026-06-18) proved that the OLD "NVIDIA 0/10"
# verdict was an artifact of spoofing the renderer string over the host's REAL (Arc) params:
# FP Pro cross-checks renderer<->params, so a GTX 980 string over Arc params mismatched
# (~0.7-0.85). Injecting camoufox's REAL GTX 980 params makes it coherent (tml median 0.333,
# flags clean). So the params are NOT vendor-independent (the old assumption) and per-GPU
# real data is what unlocks the full real GPU mix - including NVIDIA (~47% of real FF-Win),
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


#: Knuth's 32-bit golden-ratio multiplier, and a prime modulus. FROZEN: they are
#: part of the published seed -> identity contract, not tuning knobs. Changing
#: either remaps 100% of identities.
#:
#: Named because they appeared as bare literals in BOTH functions below, and the
#: two MUST use the same multiplier: select_persona picks the GPU and
#: render_noise_seed picks the canvas-noise seed, and if they stop agreeing the
#: two halves of one identity decorrelate silently - a seed's GPU and its render
#: hash would no longer be drawn from the same stream.
_IDENTITY_MULTIPLIER = 2654435761
_IDENTITY_MODULUS = 1_000_003


def _draw(pool: List[Dict], seed: int) -> Dict:
    """The prevalence-weighted draw, over whatever slice of the pool it is given.

    Factored out of `select_persona` so that a class-restricted choice walks the
    SAME cumulative arithmetic instead of a second copy of it. It must stay the
    only place that walks a cumulative weight: two copies of this loop is how a
    seed starts landing on one entry here and another one there."""
    total = sum(p["weight"] for p in pool) or 1.0
    h = (((int(seed) * _IDENTITY_MULTIPLIER) % _IDENTITY_MODULUS)
         / float(_IDENTITY_MODULUS) * total)
    cum = 0.0
    for p in pool:
        cum += p["weight"]
        if h < cum:
            return p
    return pool[-1]


def select_persona(seed: int) -> Optional[Dict]:
    """Deterministic, prevalence-weighted GPU persona for this seed - on EVERY host.

    Same seed -> same persona (fppro_consistency: identity stable per seed). Different seeds
    spread across the REAL Windows GPU mix by prevalence. Returns the Windows-ANGLE persona on
    Linux/Mac too: we must always look Windows, and the C++ WebGL override (SanitizeRenderer +
    pref-driven params/extensions) is platform-independent, so the same Windows GPU is presented
    on any host without consulting the real GL backend (no more Linux "Generic Renderer").

    This is the answer when nothing is pinned. `choose_persona` is the general
    one and the only entry point a caller should reach for; this stays because
    the seed-only draw is also the baseline the general one is defined against.
    """
    if not _ENABLED:
        return None
    pool = _gpu_pool()
    if not pool:
        return None
    return _draw(pool, seed)


def _matches(entry: Dict, renderer: Optional[str], vendor: Optional[str]) -> bool:
    return ((renderer is None or entry["renderer"] == renderer)
            and (vendor is None or entry["vendor"] == vendor))


def choose_persona(seed: int, pin: Optional[Dict] = None) -> Optional[Dict]:
    """THE answer to "which validated GPU persona does this session present?".

    One function, because that question used to be answered in six places that
    could not see one another: the wrapper's `launcher` and `async_api`, the
    core's `config`, then `eff_class` and `_persona` inside `generate_profile`,
    and then again from scratch in `prefs._apply_gpu_persona`. They agreed for
    as long as the only input was the seed, and diverged the moment a `pin`
    arrived, because a pin reached some of them and none of the ones that decide
    what the browser is told. Measured 2026-09-15 on seed 1561645783:

      pin={"gpu.renderer": "ANGLE (AMD, AMD Radeon RX 7900 XTX Direct3D11)"}
        -> Profile.gpu.renderer = the AMD string
        -> zoom.stealth.webgl.renderer = the seed's NVIDIA GTX 980, unchanged
      pin={"gpu.class_tier": "high_end"}
        -> the bundle conditioned on high_end
        -> the page still shown a low_end GPU (and no high_end persona exists)

    So all three `gpu.*` pin keys were decorative with respect to the page, while
    the Profile object reported the pin back to whoever asked. The label lied and
    the browser was never told.

    A renderer string ALONE can never be honoured, which is why a pin SELECTS
    from the validated pool rather than setting a free string: the ~81
    getParameter values, the shader precisions and the extension list travel with
    the name, and a name over foreign params is the name<->params mismatch FP Pro
    scores (~0.70 - see `prefs._apply_gpu_persona`). The domain here is finite
    and known (the pool), so a value the pool cannot present is REFUSED rather
    than quietly dropped.

    Every existing identity stays exactly where it was: a restriction is applied
    only when it would change the outcome, so the unpinned answer, and a class
    that already matches the seed's own draw, both return the plain draw.
    """
    if not _ENABLED:
        return None
    pool = _gpu_pool()
    if not pool:
        return None
    base = _draw(pool, seed)
    pin = pin or {}

    renderer = pin.get("gpu.renderer")
    vendor = pin.get("gpu.vendor")
    if renderer is not None or vendor is not None:
        if _matches(base, renderer, vendor):
            return base
        candidates = [p for p in pool if _matches(p, renderer, vendor)]
        if not candidates:
            raise ValueError(
                "pin gpu.renderer/gpu.vendor "
                f"({renderer!r}, {vendor!r}) names no validated GPU persona. "
                "A renderer string carries its getParameter values and extension "
                "list with it, so only a persona from the pool can be presented "
                "coherently. Available renderers: "
                + ", ".join(repr(r) for r in sorted({p["renderer"] for p in pool}))
            )
        # Entries that share a renderer AND a vendor carry identical `prefs`
        # (asserted in tests), so which of them is returned cannot change what
        # the page sees; the first wins, and pool order is frozen, so the choice
        # is stable across runs.
        return candidates[0]

    wanted = pin.get("gpu.class_tier")
    if wanted and base["gpu_class"] != wanted:
        candidates = [p for p in pool if p["gpu_class"] == wanted]
        if not candidates:
            raise ValueError(
                f"pin gpu.class_tier={wanted!r} has no validated GPU persona. "
                "Conditioning the bundle on a class the pool cannot present is "
                "the internal contradiction the per-GPU pool exists to remove. "
                "Available classes: "
                + ", ".join(repr(c) for c in sorted({p["gpu_class"] for p in pool}))
            )
        return _draw(candidates, seed)
    return base


def persona_for(renderer: str, vendor: str) -> Optional[Dict]:
    """The pool entry a Profile's GPU label came from - a READ, not a decision.

    `generate_profile` writes the chosen persona's renderer and vendor onto the
    Profile, so this reads that decision back out. It is what `prefs` uses, and
    the reason `prefs` no longer re-runs the draw: the persona a session presents
    is chosen once, when the profile is generated, and everything downstream must
    read it rather than reconstruct it from the seed. Entries sharing a
    renderer+vendor carry identical `prefs`, so this cannot return a different
    override from the one that was chosen."""
    if not _ENABLED:
        return None
    for p in _gpu_pool():
        if p["renderer"] == renderer and p["vendor"] == vendor:
            return p
    return None


def forced_gpu_class(seed: int) -> Optional[str]:
    """The gpu_class of the persona this seed presents. A QUERY, not a knob.

    ⛔ IT IS NO LONGER AN ARGUMENT TO ANYTHING, and that is the whole of what
    changed on 2026-09-15. `generate_profile` used to take a `fixed_gpu_class=`
    beside its `pin`, which `choose_persona` treated as a synonym for
    `pin["gpu.class_tier"]`: two spellings of one request, five call sites, and
    three of them disagreeing with the other two about whether to pass it. The
    argument is gone and the class is derived from the persona, so there is one
    way to ask.

    The function stays because it asks a different kind of question - what class
    does this seed land on, without building a profile - and because
    `invisible_playwright._webgl_personas` re-exports it as one of the import
    shapes that survived the 2026-07-03 package split
    (`tests/test_backcompat.py` in the wrapper names those as shipped in
    downstream PRs). Reading it costs nothing and commits nobody: it derives
    from `select_persona` and owns no state.
    """
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
# across the population - real GPUs cluster to few canvas hashes anyway); identity
# stays per-seed (the rest of the fingerprint differs). Same seed -> same render seed
# (fppro_consistency holds).
# CAVEAT: the render hash = f(host GPU render, gamma), so this pool is calibrated on
# the Intel-Arc host. On other GPUs the clean set may differ (host-independence open,
# same as the personas) - Option B (substitution = GPU-independent render hash) would
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
# - it only supplies per-session diversity. A 2026-06-21 attempt to re-calibrate it per-host FAILED
# cross-OS: hw_seed clean on Windows went dirty on the Linux GL backend (b008 0.034->0.839; Win-dirty
# {7,11,20,27} = Linux-clean and vice-versa; + identity×hw_seed interaction on Linux). That proved
# calibration can't work cross-host → substitution replaces it. Kept the original diverse 9-set.
# 2026-07-26: 0 REMOVED. It is genuinely clean for the render hash - that is not
# in dispute - but this value does not only seed the canvas noise: it is written
# to zoom.stealth.fpp.hw_seed, and three C++ sites gate on it being > 0.
#   dom/base/Navigator.cpp:917            maxTouchPoints
#   layout/style/nsMediaFeatures.cpp:423  pointer / hover
#   dom/media/webaudio/AnalyserNode.cpp:228,273  audio noise
# So a session mapping to 0 got a clean render AND silently reverted to the
# host's real touch, pointer and audio behaviour. Measured before the change:
# 223 of 2000 seeds, 11.2% of identities, and on touch-capable Windows hardware
# that is a visible capability appearing where the persona says it should not.
# A value cannot be both a seed and an off-switch; the pool keeps the eight that
# are only seeds.
# The 0 slot is REPLACED, not removed, and the length stays 9 on purpose.
# render_noise_seed indexes with `% len(...)`, so shrinking the list to 8
# remaps EVERY identity: measured, 445 of 500 seeds got a different hw_seed and
# therefore a different canvas render hash, for a defect that affected 11%.
# Duplicating an existing calibrated value keeps every other index exactly where
# it was, so only the seeds that used to draw 0 move - which is the whole
# intended blast radius.
CLEAN_RENDER_SEEDS = [5, 5, 6, 9, 11, 16, 19, 20, 28]


def render_noise_seed(seed: int) -> int:
    """Deterministic clean render-noise seed for hw_seed (decoupled from identity).

    Maps the identity seed into CLEAN_RENDER_SEEDS so every session gets a calibrated
    clean canvas/WebGL render hash while keeping per-user diversity. Stable per seed."""
    return CLEAN_RENDER_SEEDS[(int(seed) * _IDENTITY_MULTIPLIER)
                             % len(CLEAN_RENDER_SEEDS)]
