# -*- coding: utf-8 -*-
"""stealth_forge — Bayesian fingerprint generator for Firefox 150 Windows.

Everything the Firefox build exposes to JS (screen, hardwareConcurrency,
WebGL, audio, MSAA, theme, media codecs) is sampled from a Bayesian network
with coherent cross-field dependencies. Identity (userAgent, platform,
oscpu, webdriver=false, maxTouchPoints=0) is locked by the compiled build.

Graph:

    gpu (root, 474 real Windows ANGLE renderers)
     │
     └─> gpu_class (deterministic classifier, 6 classes)
          ├─> hw_concurrency       (CPT per class)
          ├─> screen (w/h/dpr/av)  (CPT per class)
          └─> msaa_samples         (CPT per class)

    audio (root, joint rate+latency+channels — marginal)
    dark_theme                     (marginal)
    av1_enabled                    (marginal)
    webm_encoder_enabled           (marginal)

    font_exclude  ← deterministic hash of stealth_seed (seed-derived)

CPTs live in `data/*.json` (easy to tune without code changes).
Sampling is deterministic per stealth_seed via a private random.Random.
"""
import json
import os
import re
from typing import Any, Dict, Optional

from ._network import Network, Node

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(filename: str) -> Any:
    with open(os.path.join(_HERE, "data", filename), "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════
#  LOCKED IDENTITY (compiled into our Firefox 150 build — never varies)
# ═══════════════════════════════════════════════════════════════════════
_LOCKED: Dict[str, Any] = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) "
        "Gecko/20100101 Firefox/150.0.1"
    ),
    "platform": "Win32",
    "oscpu": "Windows NT 10.0; Win64; x64",
    "app_code_name": "Mozilla",
    "app_version": "5.0 (Windows)",
    "product_sub": "20100101",
    "webdriver": False,
    "max_touch_points": 0,
}


# ═══════════════════════════════════════════════════════════════════════
#  DATA
# ═══════════════════════════════════════════════════════════════════════
_GPU_POOL = _load("webgl_renderer_pool.json")["entries"]
# hwc/screen/storage now keyed on (gpu_class, intra_tier) for triangulation
_CPT_HWC = _load("cpt_hwc_given_class_tier.json")["table"]
_CPT_SCREEN = _load("cpt_screen_given_class_tier.json")["table"]
_CPT_STORAGE = _load("cpt_storage_given_class_tier.json")["table"]
# Hidden tier variable that makes hwc/screen/storage jointly coherent
_CPT_INTRA_TIER = _load("cpt_intra_tier_given_class.json")["table"]
# MSAA depends on (gpu_class, screen_tier) — 4K gaming → MSAA=0, 1080p+GPU → MSAA=4
_CPT_MSAA = _load("cpt_msaa_given_class_screen.json")["table"]
# Codec unchanged
_CPT_CODEC = _load("cpt_codec_given_class.json")["table"]
# Audio now conditional on gpu_class (workstation → pro audio, old → 44.1kHz onboard)
_CPT_AUDIO = _load("cpt_audio_given_class.json")["table"]
_INDEP = _load("priors_independent.json")
_FONT_POOL = _load("font_pool.json")
# hardwareConcurrency: grounded in the REAL Windows marginal (browserforge Windows UAs).
# cores are OS-level, ~independent of GPU given the OS (browserforge confirms), so this is a
# root marginal — NOT conditioned on gpu_class/intra_tier. Fixes the old CPT over-representing
# 6 cores (~28% vs real ~2%). NB: screen size + dpr are intentionally LEFT on their existing
# nodes (user 2026-06-18: "non modificare dpr e le size degli screen, rompono sempre").
_CORES_MARGINAL = [
    {"value": int(e["value"]), "prob": e["prob"]}
    for e in _load("win_hw_marginals.json")["cores"]
    if 2 <= int(e["value"]) <= 64 and e["prob"] >= 0.004
]
# Each entry is a dict {"name": "<lowercase family>", "factor": float}.
# - name: the font family advertised to the page.
# - factor: per-family width scale used by the consumer to make the family
#   detectable by width-diff probes.
# Core = always-included; Optional = sampled with P(font | gpu_class).
_FONT_CORE: list = _FONT_POOL["core"]
_FONT_OPTIONAL: list = _FONT_POOL["optional"]
_CPT_FONTS_OPT = _load("cpt_fonts_optional_given_class.json")["table"]  # legacy (per-font sampling, superseded by profiles)
# Realistic Windows font PROFILES (2026-06-18): each = a real machine's optional-font set
# (validated to NOT over-claim on FP Pro). Profile-level variation (machines differ in
# Office/extra fonts) instead of per-font random sampling, which produced unrealistic
# combinations (exotic fonts -> FP Pro over-detection -> tampering_ml tell).
_FONT_PROFILES: list = _FONT_POOL.get("profiles", [])
_OPT_BY_NAME = {e["name"]: e for e in _FONT_OPTIONAL}
# Browsing-history pool + CPT (per-class probabilities for visited sites).
# Drives _recaptcha_seed's cookie pre-seed: each persona ends up with a
# coherent list of ~15-30 visited sites whose categories correlate with
# gpu_class (workstation → dev-heavy, integrated_old → shop+news-heavy).
_BROWSING_POOL: list = _load("browsing_pool.json")["entries"]
_CPT_BROWSING = _load("cpt_browsing_given_class.json")["table"]


# ═══════════════════════════════════════════════════════════════════════
#  GPU CLASSIFIER (deterministic function of gpu → gpu_class)
# ═══════════════════════════════════════════════════════════════════════
_GPU_CLASSES = (
    "integrated_old", "integrated_modern", "low_end",
    "mid_range", "high_end", "workstation",
)


def classify_gpu(gpu_value: Dict[str, str]) -> str:
    """Deterministic: maps (renderer, vendor) dict to one of 6 classes.

    See data/cpt_*.json — each CPT table has an entry for every class.
    """
    r = gpu_value.get("renderer", "")

    if re.search(r"Intel.*HD Graphics (3000|4000|2500)", r):
        return "integrated_old"
    # Discrete Intel Arc DESKTOP/dGPU cards (A-series / B-series, e.g. A750,
    # A770, B580) are discrete GPUs (~RTX 3060 tier for A7xx), NOT the
    # integrated "Arc 130T/140T/Graphics" iGPUs in Core Ultra chips. Route the
    # discrete SKUs to a coherent discrete-GPU class so the conditioned bundle
    # (cores, screen, storage) matches a real discrete-GPU machine; A3xx are
    # entry discrete -> low_end, A5xx/A7xx/Bxxx -> mid_range. Bare "Arc 1x0(T/V)"
    # integrated names do NOT match and fall through to integrated_modern below.
    m = re.search(r"Intel.*\bArc(?:\(TM\))?\s+([AB])(\d)\d\d\b", r)
    if m:
        return "low_end" if m.group(2) == "3" else "mid_range"
    if re.search(
        r"Intel.*(HD Graphics (4[56]|5\d\d|6\d\d)|UHD Graphics|Graphics Family|Iris|Arc)",
        r,
    ):
        return "integrated_modern"
    if re.search(
        r"AMD.*(Radeon(\(TM\))? (Graphics|6\d\dM|7\d\dM|8\d\dM)|Vega [0-9]|"
        r"Renoir|Rembrandt|TM Graphics)",
        r, re.IGNORECASE,
    ):
        return "integrated_modern"

    # NVIDIA: Firefox SanitizeRenderer.cpp collapses every GeForce into one of
    # 3 vintage buckets (8800 GTX / GTX 480 / GTX 980). The renderer string
    # exposed to JS is therefore vintage; pairing it with modern cores/screen
    # creates an internal mismatch that FP Pro's tampering_ml flags. We pick
    # `low_end` for all 3 buckets so cores stay 4-12 and screen 1080-1440p,
    # consistent with what a real user with each of those (vintage) cards
    # would have. Workstation overrides keep their high-tier classification.
    if re.search(
        r"(GeForce (8\d\d\d?|9\d\d\d?|GTX 980|GTX 480|GT 1030|GT 710|GT 730|"
        r"GT 220|GT 240|210|310)|Quadro K\d|Radeon HD [1234]\d\d\d)", r,
    ):
        return "low_end"

    # NVIDIA discrete (any other GeForce — should be rare after the pool was
    # collapsed to the 3 sanitize buckets, but kept as a safety net).
    m = re.search(r"GeForce\s+(?:GTX\s+|RTX\s+)?(\d{3,4})", r)
    if m:
        if "Quadro" in r or "Workstation" in r:
            return "workstation"
        # Anything that survives the sanitize collapse stays low_end to avoid
        # the modern-cores/vintage-renderer pairing.
        return "low_end"

    # AMD discrete
    m = re.search(r"Radeon[^0-9]*(\d{3,4})", r)
    if m:
        n = int(m.group(1))
        if "FirePro" in r or "Radeon Pro" in r:
            return "workstation"
        if n >= 5700:
            return "high_end"
        if 5500 <= n <= 5600 or 580 <= n <= 590:
            return "mid_range"
        return "low_end"

    # Fallback
    return "mid_range"


# ═══════════════════════════════════════════════════════════════════════
#  NETWORK CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════
# Build once at import — the network is stateless, only the RNG varies.

def _gpu_marginal():
    """Build marginal distribution over GPU pool (uniform for now)."""
    n = len(_GPU_POOL)
    p = 1.0 / n
    return [{"value": g, "prob": p} for g in _GPU_POOL]


def _cpt_from_table(table: Dict[str, Any]) -> Dict[str, list]:
    """CPT for conditional nodes: `{class_name: [{value, prob}, ...]}`."""
    return dict(table)


def _screen_tier(ctx):
    """Classify screen width into tier for (gpu_class, screen_tier) CPTs."""
    s = ctx.get("screen", {}) or {}
    w = int(s.get("w", 1920))
    h = int(s.get("h", 1080))
    # Ultrawide: aspect ratio > 2.1 (e.g. 3440x1440, 5120x1440)
    if h > 0 and (w / h) > 2.1:
        return "ultrawide"
    if w <= 1920:
        return "1080p"
    if w <= 2560:
        return "1440p"
    if w <= 3840:
        return "2160p"
    return "ultrawide"


_NETWORK = Network([
    Node("gpu", parents=[], cpt=_gpu_marginal()),
    Node("gpu_class", parents=["gpu"], classifier=lambda ctx: classify_gpu(ctx["gpu"])),
    # Hidden variable: within a gpu_class, user's OTHER components (RAM, SSD,
    # cores, screen) correlate — a 'premium' mid_range user has more cores,
    # larger SSD, higher-res screen than a 'budget' mid_range user. Without
    # this, hwc/screen/storage would be independent given gpu_class (noisy).
    Node("intra_tier", parents=["gpu_class"], cpt=_cpt_from_table(_CPT_INTRA_TIER)),
    # hw_concurrency: REAL Windows marginal (root, OS-level, not GPU-conditioned). screen +
    # storage stay jointly coherent via (gpu_class, intra_tier) — screen size deliberately
    # unchanged (user: dpr + screen sizes break things; leave them).
    Node("hw_concurrency", parents=[], cpt=_CORES_MARGINAL),
    Node("screen", parents=["gpu_class", "intra_tier"],
         cpt=_cpt_from_table(_CPT_SCREEN)),
    # Derive screen_tier from screen for msaa parent lookup.
    Node("screen_tier", parents=["screen"], classifier=_screen_tier),
    # MSAA: realistic combo (4K + high_end GPU → MSAA=0 due to perf cost;
    # 1080p + high_end → MSAA=4 common; 1080p + integrated → MSAA=0).
    Node("msaa_samples", parents=["gpu_class", "screen_tier"],
         cpt=_cpt_from_table(_CPT_MSAA)),
    # Joint codec distribution (gpu_class only).
    Node("codec", parents=["gpu_class"], cpt=_cpt_from_table(_CPT_CODEC)),
    # Storage quota: coherent within gpu_class × intra_tier (premium workstation
    # user → 2-3TB SSD; budget workstation user → 512GB; budget integrated_old
    # → 128GB).
    Node("storage_quota_mb", parents=["gpu_class", "intra_tier"],
         cpt=_cpt_from_table(_CPT_STORAGE)),
    # Audio: pro users (workstation) → 48/96kHz 6-8ch; old onboard → 44.1kHz
    # 2ch high latency. Workstation GPU + 44.1kHz mono was previously
    # implausible; now blocked by the CPT.
    Node("audio", parents=["gpu_class"], cpt=_cpt_from_table(_CPT_AUDIO)),
    Node("dark_theme", parents=[], cpt=_INDEP["dark_theme"]["table"]),
])


# ═══════════════════════════════════════════════════════════════════════
#  FONT LIST (Bayesian: core ∪ sampled_optional | gpu_class)
# ═══════════════════════════════════════════════════════════════════════
# The browser sees ONLY these families (everything else hidden) and renders
# them from the REAL Windows font files the binary bundles in <GRE>/fonts
# (MOZ_BUNDLED_FONTS). No fabricated widths: per-session metric uniqueness
# comes from the HarfBuzz per-glyph jitter (shared fpp.hw_seed), not here.
# Core (~112): always included — fresh Win11 + Office 2021 English.
# Optional (~40): one realistic Windows profile sampled per seed (weighted,
# deterministic) → ~3-8 optional families differ per session while staying
# centered on 'typical Windows user'.


def derive_font_prefs(gpu_class: str, rng) -> Dict[str, str]:
    """Build the session's font family list.

    Profile-based (not per-font random):
      - Core families always included (OS defaults + CSS-generic backers).
      - Optional families come from ONE realistic Windows profile picked per
        seed (weighted, deterministic).

    Returns ``{"whitelist": "arial,calibri,marlett,..."}`` — the comma-joined
    family list to advertise. The binary applies it to the native system font
    allow-list AT CONSTRUCTION and renders each family from the bundled real
    Windows file, so glyphs and widths are genuine. To add a family, just add
    an entry to font_pool.json:core/optional — no special-case code needed.
    """
    # Profile-based (2026-06-18): pick ONE realistic Windows font profile (weighted,
    # deterministic per seed). Per-font random sampling is superseded — it produced
    # unrealistic optional combinations (exotic fonts) that FP Pro over-detected
    # (detected-set 26 vs real 20 -> tampering_ml ~0.72). Profiles are validated subsets
    # of a real machine's set, so the detected-set matches a genuine Windows install.
    included: list = list(_FONT_CORE)  # core: always present (OS defaults + generic backers)
    profile = None
    if _FONT_PROFILES:
        total = sum(p.get("weight", 1) for p in _FONT_PROFILES)
        anchor = rng.random() * total
        cum = 0.0
        for p in _FONT_PROFILES:
            cum += p.get("weight", 1)
            if anchor < cum:
                profile = p
                break
        if profile is None:
            profile = _FONT_PROFILES[-1]
    if profile is not None:
        for name in profile.get("optional", []):
            entry = _OPT_BY_NAME.get(name)
            if entry is not None:
                included.append(entry)
    else:
        included.extend(_FONT_OPTIONAL)  # fallback (no profiles defined): all optional
    # Dedup by name (a profile may list a font that is also in core, e.g. after a
    # standard font is promoted core→always-present) so the list never carries a
    # duplicate family.
    _seen: set = set()
    _uniq: list = []
    for e in included:
        if e["name"] not in _seen:
            _seen.add(e["name"])
            _uniq.append(e)
    included = _uniq
    # Deterministic ordering: sort by name
    included.sort(key=lambda e: e["name"])
    whitelist = ",".join(e["name"] for e in included)
    return {"whitelist": whitelist}


# Back-compat shim: legacy callers still import derive_font_whitelist.
def derive_font_whitelist(gpu_class: str, rng) -> str:
    return derive_font_prefs(gpu_class, rng)["whitelist"]


# ═══════════════════════════════════════════════════════════════════════
#  BROWSING HISTORY (Bayesian: per-site P(visited|gpu_class))
# ═══════════════════════════════════════════════════════════════════════
def derive_browsing_history(gpu_class: str, rng) -> list:
    """Sample which sites this persona has visited recently.

    Each site in the pool has a per-class probability (CPT). We sample
    independently per-site, producing a list of dicts:
        [{"name": "github.com", "category": "dev", "cookie_profile": "ga_cf"}, ...]

    Sum of CPT probabilities per class is tuned to land ~15-30 visited sites
    on average — an established-user signature. Sorted by name for stable
    output across runs of the same seed.
    """
    cpt = _CPT_BROWSING.get(gpu_class)
    if cpt is None:
        cpt = _CPT_BROWSING["mid_range"]
    visited: list = []
    for entry in _BROWSING_POOL:
        name = entry["name"]
        p = cpt.get(name, 0.3)  # default 0.3 for missing CPT row
        if rng.random() < p:
            visited.append(dict(entry))  # copy to avoid mutating pool
    visited.sort(key=lambda e: e["name"])
    return visited


# ═══════════════════════════════════════════════════════════════════════
#  PUBLIC API: Forge
# ═══════════════════════════════════════════════════════════════════════
import random


class Forge:
    """Fingerprint forge — single seed → coherent bundle."""

    def __init__(self, seed: int):
        self.seed = int(seed)
        self._rng = random.Random(self.seed)

    def sample(self, fixed_gpu_class: Optional[str] = None) -> Dict[str, Any]:
        # fixed_gpu_class pins gpu_class so the WHOLE bundle (cores/screen/fonts) is
        # drawn coherently for the WebGL persona's class we expose on Windows/mac.
        # The default (no fix) path calls _NETWORK.sample(rng) with one arg so existing
        # monkeypatches/tests keep working.
        if fixed_gpu_class:
            bundle = _NETWORK.sample(self._rng, evidence={"gpu_class": fixed_gpu_class})
        else:
            bundle = _NETWORK.sample(self._rng)
        gpu = bundle["gpu"]
        screen = bundle["screen"]
        audio = bundle["audio"]
        codec = bundle["codec"]
        return {
            # Seed tracking
            "stealth_seed": self.seed,
            # Locked identity
            **_LOCKED,
            # GPU (coherent pair from 474 pool)
            "webgl_renderer": gpu["renderer"],
            "webgl_vendor": gpu["vendor"],
            "gpu_class": bundle["gpu_class"],
            # Hidden-variable debug metadata (not a Firefox pref, just for
            # analysis / test result correlation tracking)
            "intra_tier": bundle["intra_tier"],
            "screen_tier": bundle["screen_tier"],
            # Screen (coherent with GPU class)
            "screen_w": int(screen["w"]),
            "screen_h": int(screen["h"]),
            "screen_avail_w": int(screen.get("aw", screen["w"])),
            "screen_avail_h": int(screen.get("ah", screen["h"] - 40)),
            "dpr": float(screen["dpr"]),
            # Hardware (coherent with GPU class)
            "hw_concurrency": int(bundle["hw_concurrency"]),
            # WebGL MSAA (coherent with GPU class)
            "msaa_samples": int(bundle["msaa_samples"]),
            # Audio (independent joint)
            "audio_sample_rate": int(audio["rate"]),
            "audio_output_latency_ms": int(audio["latency"]),
            "audio_max_channel_count": int(audio["channels"]),
            # Codec prefs (joint, coherent with GPU class). All 5 are
            # JS-visible: av1/webm_encoder via canPlayType/MediaRecorder,
            # mediasource_* via MediaSource.isTypeSupported, webspeech_synth
            # via 'speechSynthesis' in window (CreepJS voices probe).
            "av1_enabled": bool(codec["av1_enabled"]),
            "webm_encoder_enabled": bool(codec["webm_encoder_enabled"]),
            "mediasource_webm": bool(codec["mediasource_webm"]),
            "mediasource_mp4": bool(codec["mediasource_mp4"]),
            "webspeech_synth": bool(codec["webspeech_synth"]),
            # Storage quota MB (coherent with GPU class — workstation larger SSDs).
            "storage_quota_mb": int(bundle["storage_quota_mb"]),
            # Independent marginals
            "dark_theme": int(bundle["dark_theme"]),
            # Bayesian font prefs (coherent pair: whitelist + per-family
            # width scale metrics, both sampled from the same font_pool.json
            # and conditioned on gpu_class).
            **{
                f"font_{k}": v
                for k, v in derive_font_prefs(
                    bundle["gpu_class"], self._rng
                ).items()
            },
            # Bayesian browsing history (per-class P(visited|gpu_class)).
            # Consumed by _recaptcha_seed.py to seed coherent cookie history
            # when invisible_playwright is launched with prep_recaptcha=True.
            "browsing_history": derive_browsing_history(
                bundle["gpu_class"], self._rng
            ),
        }


def sample(seed: int, fixed_gpu_class: Optional[str] = None) -> Dict[str, Any]:
    """Convenience: `Forge(seed).sample(fixed_gpu_class)`."""
    return Forge(seed).sample(fixed_gpu_class)
