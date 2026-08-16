"""Public dataclass surface for fpforge."""
from __future__ import annotations

from dataclasses import dataclass, field, replace as _dc_replace
from typing import Any, Dict, List, Optional

from ._sampler import sample as _sample_raw

# Top level, not deferred: `_webgl_personas` imports nothing from this package at
# module scope, so there is no cycle to work around - verified by importing the
# package, this module and `_webgl_personas` each first in a fresh interpreter.
from .._webgl_personas import forced_gpu_class as _forced_gpu_class
from .._webgl_personas import select_persona as _select_persona


@dataclass(frozen=True)
class GPUProfile:
    vendor: str
    renderer: str
    class_tier: str        # "low_end" | "mid_range" | "high_end" | "integrated_old" | "integrated_modern"


@dataclass(frozen=True)
class ScreenProfile:
    width: int
    height: int
    avail_width: int
    avail_height: int
    dpr: float
    tier: str
    #: Screen colour depth in bits. NOT sampled: 24 is what every mainstream
    #: Windows desktop reports, and a persona that varied it would be varying a
    #: thing real machines do not vary. It is a FIELD rather than a constant in
    #: the binary because of what the field-by-field audit found: without it
    #: nsScreen::PixelDepth reads nsDeviceContext::GetDepth(), the real panel,
    #: and it went unnoticed for as long as it did because both development
    #: machines are 24-bit, so it agreed cross-OS by coincidence. Pinnable and
    #: overridable like every other surface (rule 6, tutto allo stesso livello).
    color_depth: int = 24

    #: Height of the Windows taskbar, i.e. how much shorter availHeight is
    #: than height. It was the literal 48 in three places - two C++ files and
    #: this generator - kept in step by hand.
    taskbar_px: int = 48

    #: Window chrome: outerWidth - innerWidth, and outerHeight - innerHeight.
    #: They lived in the wrapper as module constants (14 and 91) where nothing
    #: could pin or inspect them, and the 14 was fabricated - measured against
    #: stock Firefox 151, a real browser reports 0 horizontal chrome. Same level
    #: as every other screen surface (rule 6), so a persona with a different
    #: toolbar layout can pin them.
    chrome_w: int = 0
    chrome_h: int = 85

    #: Where the window sits on the screen. outerWidth/outerHeight already
    #: claim a MAXIMIZED window filling the screen, and a maximized window is
    #: at the origin - but the position was never declared, so it stayed
    #: whatever the OS gave the headless widget: (4,4) on Windows, which put
    #: the right edge of a 1920-wide window at 1924 on a 1920 screen. Stock
    #: Firefox 151 reports 0,0 for this window shape (measured 2026-08-09).
    window_x: int = 0
    window_y: int = 0


@dataclass(frozen=True)
class HardwareProfile:
    concurrency: int
    storage_quota_mb: int
    #: navigator.maxTouchPoints. NOT sampled: a desktop Windows machine reports
    #: 0, and the GPU personas we ship are all desktop parts, so varying this
    #: would contradict the rest of the identity.
    #:
    #: It is a field rather than the literal it used to be. The binary already
    #: forced 0 (Navigator.cpp), which is the right VALUE, but as a constant
    #: buried in C++ it was not pinnable, not inspectable and not overridable
    #: through extra_prefs - the thing rule 6 rejects. A touchscreen persona is
    #: a legitimate future case (a Surface, a 2-in-1), and it must not require
    #: a browser rebuild.
    max_touch_points: int = 0

    #: The five speechSynthesis voices the page is handed, as the binary
    #: parses them: "name|lang|default|localService", comma separated.
    #:
    #: A field rather than a constant in _BASELINE for the usual reason, and
    #: for one specific one: they are ALWAYS the English (United States) set,
    #: whatever locale the session resolved to. A real Windows machine running
    #: in Italian reports Italian voices, so an it-IT session declaring only
    #: American voices contradicts itself - and that contradiction cannot even
    #: be expressed today, let alone fixed, because the value is not a field.
    #:
    #: The per-locale tables are NOT filled in here. They have to be measured
    #: on a real Windows install of each locale, and inventing five plausible
    #: Italian voice names would be exactly the kind of guess this codebase
    #: keeps having to undo. Moving the level is the part that can be done
    #: honestly today; the data is the next step and it needs a machine.
    voices: str = ""

    #: navigator.mediaDevices.enumerateDevices: one fake audio input and one
    #: fake video input on every host, via media.navigator.streams.fake.
    #:
    #: Measured 2026-08-08 in a secure context (about:blank is not one, and
    #: measuring there made the two hosts look like they agreed because both
    #: returned nothing): Linux enumerated 0 real devices and Windows 2. The
    #: fixed pair is the invariant we want; it was a constant in _BASELINE,
    #: which means the device count - a fingerprint surface - was not
    #: pinnable, not inspectable and not overridable.
    fake_media_devices: bool = True

    #: Whether cookies, localStorage, sessionStorage and indexedDB all work.
    #:
    #: ONE field for four booleans on purpose: they are one decision - "this
    #: browser has working storage" - and Gecko exposes them through two
    #: levers, not four. A page reads all four; a real desktop Firefox answers
    #: true to all four.
    #:
    #: They used to be true because nobody touched network.cookie.cookieBehavior
    #: or dom.storage.enabled, i.e. because the upstream defaults happened to be
    #: right - not because anything declared them. An enterprise policy, a
    #: profile carried over, or an upstream default change would have moved a
    #: fingerprint surface with nothing in this package aware of it.
    storage_enabled: bool = True

    #: Whether the machine has accessibility overrides on: reduced motion,
    #: reduced transparency, inverted colours.
    #:
    #: One field for three prefs because it is one fact about the machine, and
    #: False is what an ordinary desktop reports. They were declared by nothing
    #: at all until 2026-08-09: prefers-reduced-motion and friends are
    #: content-exposed media features that read the HOST, through structurally
    #: different code on each platform - SystemParametersInfoW on Windows,
    #: gtk-enable-animations and the DBus portal on Linux.
    #:
    #: Measured the day they were found: 0 divergences on 20 features between
    #: the two builds. That is agreement by LUCK - both machines happen to have
    #: no accessibility settings on - not coverage, and it is the distinction
    #: this codebase keeps having to relearn. A user with reduced motion on, or
    #: a CI container whose GTK theme disables animations, would diverge with
    #: nothing in this package aware of it.
    #:
    #: Costs nothing to close: Firefox already reads ui.prefersReducedMotion and
    #: its siblings BEFORE the native per-platform code (nsXPLookAndFeel), so
    #: this needs no C++ and no rebuild.
    accessibility_overrides: bool = False

    #: The CSS generic families, as "generic|lang|family" records separated by
    #: newlines. It was ten rows compiled into gfxPlatformFontList.cpp: a finite,
    #: perfectly known domain (four generics x five langgroups), so changing
    #: which face answers `font-family: serif` should not cost a browser
    #: rebuild. The x-math row is load-bearing and easy to lose: without it the
    #: western serif answer wins and every MathML glyph renders in Times New
    #: Roman, on EVERY host, which is why no cross-OS gate could see it.
    generics: str = ""


@dataclass(frozen=True)
class AudioProfile:
    sample_rate: int
    output_latency_ms: int
    max_channel_count: int


@dataclass(frozen=True)
class CodecProfile:
    av1_enabled: bool
    webm_encoder_enabled: bool
    mediasource_webm: bool
    mediasource_mp4: bool
    webspeech_synth: bool


@dataclass(frozen=True)
class WebGLProfile:
    msaa_samples: int


@dataclass(frozen=True)
class FontProfile:
    """The Windows system-font surface a page can read.

    A twin of the others in shape, but NOT in origin, and the difference is the
    point: gpu, screen and hardware are SAMPLED, because real machines vary and
    a fleet that all reported one GPU would be its own signal. This one is
    FIXED, because every Windows machine answers `font: menu` with Segoe UI at
    12px - sampling it would manufacture a diversity that does not exist in the
    world we are imitating, which is the same error in the opposite direction.

    It is a Profile field rather than a baseline constant so it is pinnable,
    inspectable and overridable like every other surface, and so the one place
    that owns "what Windows looks like" is the profile.

    `ui_size` is a STRING on purpose. nsXPLookAndFeel reads it through
    Preferences::GetFloat, which in Gecko parses float prefs from their text
    form; declared as a bare int the pref is silently ignored and the UI falls
    back to StyleFONT_MEDIUM_PX (16px). `monospace_size` is a genuine int - a
    different pref type - and the two serialise differently.
    """
    ui_family: str
    ui_size: str
    monospace_size: int
    #: The grayscale coverage levels a Windows glyph edge produces, ascending,
    #: first 0 and last 255 so a snap never moves a fully transparent or fully
    #: opaque pixel. Measured 2026-08-07 over 48 family/size/weight combos:
    #: Windows produces 9-19 distinct alpha levels per render, FreeType 193-256.
    #: A detector does not have to compare a hash to tell those apart, it can
    #: COUNT - prod on Linux renders "A" with 149 levels against Windows' 16.
    #: Empty tuple disables the snap.
    alpha_ladder: tuple
    #: The whole font manifest the binary parses: families, per-face vertical
    #: metrics, the alias table, the coverage ladder and the per-script
    #: fallback lists. A field rather than a side file so that everything the
    #: profile declares about fonts is in one object, pinnable and inspectable
    #: like every other surface. Empty string tells the binary to use the copy
    #: in its own directory.
    manifest: str
    #: The glyph rasterisation parameters, in the units the prefs expect.
    #:
    #: These six are read ONCE at startup and then stay fixed for the process,
    #: which is precisely the case invisible_core declares. They were not
    #: declared, and the shape of the gap is the one that keeps recurring here:
    #: gfxDWriteFonts::UpdateClearTypeVars went defaults -> SYSTEM -> prefs, so
    #: the machine's own ClearType tuning sat in the middle and survived
    #: whenever a pref was absent. On the development machine that produces the
    #: answer we want, which is exactly why it went unnoticed.
    #:
    #: The values are Windows canonical, not sampled: real machines vary these
    #: with the user's ClearType Tuner, but a persona that varied them would be
    #: varying something no two of our identities should disagree on.
    #:
    #: Cross-platform note that matters more than the values: Skia's glyph mask
    #: gamma defaults to LINEAR on Linux (DrawTargetSkia.cpp:1850-1856) against
    #: DWrite's 2.2. Two different coverage curves over the same outlines is a
    #: structural difference, and freetype_gamma below is what aligns them.
    #: Measured: it moves the canvas text hash on every setting, so the lever
    #: works, but it does NOT close the gap on its own - the residual is the
    #: alpha quantisation, not the curve.
    cleartype_gamma: int = 2200        # /1000 -> 2.2, DWrite's own default
    cleartype_contrast: int = 100      # /100  -> 1.0
    cleartype_level: int = 100         # /100  -> 1.0
    cleartype_pixel_structure: int = 1  # DWRITE_PIXEL_GEOMETRY_RGB
    cleartype_rendering_mode: int = 5   # CLEARTYPE_NATURAL_SYMMETRIC
    freetype_gamma: int = 220          # /100  -> 2.2, matching DWrite
    freetype_contrast: int = 100       # /100  -> 1.0


# ──────────────────────────────────────────────────────────────────────
#  Pin map: flat dotted-path -> value. Set via `pin=` on generate_profile.
#
#  Supported keys:
#      "gpu.vendor", "gpu.renderer", "gpu.class_tier"
#      "screen.width", "screen.height", "screen.avail_width",
#      "screen.avail_height", "screen.dpr", "screen.tier"
#      "hardware.concurrency", "hardware.storage_quota_mb"
#      "audio.sample_rate", "audio.output_latency_ms",
#      "audio.max_channel_count"
#      "codec.av1_enabled", "codec.webm_encoder_enabled",
#      "codec.mediasource_webm", "codec.mediasource_mp4",
#      "codec.webspeech_synth"
#      "webgl.msaa_samples"
#      "font.ui_family", "font.ui_size", "font.monospace_size",
#      "font.alpha_ladder"
#      "font.cleartype_gamma", "font.cleartype_contrast",
#      "font.cleartype_level", "font.cleartype_pixel_structure",
#      "font.cleartype_rendering_mode", "font.freetype_gamma",
#      "font.freetype_contrast"
#      "screen.color_depth"
#      "hardware.max_touch_points"
#      "dark_theme"
# ──────────────────────────────────────────────────────────────────────

_PIN_GROUPS = {
    "gpu": {"vendor", "renderer", "class_tier"},
    "screen": {"width", "height", "avail_width", "avail_height", "dpr", "tier", "taskbar_px", "chrome_w", "chrome_h", "window_x", "window_y",
               "color_depth"},
    "hardware": {"concurrency", "storage_quota_mb", "max_touch_points",
                 "voices", "fake_media_devices",
                 "storage_enabled", "generics",
                 "accessibility_overrides"},
    "audio": {"sample_rate", "output_latency_ms", "max_channel_count"},
    "codec": {
        "av1_enabled", "webm_encoder_enabled",
        "mediasource_webm", "mediasource_mp4", "webspeech_synth",
    },
    "webgl": {"msaa_samples"},
    # The seven rasterisation parameters belong here for the same reason every
    # other surface does: a declared value that cannot be pinned, inspected or
    # overridden is a constant buried in a different file, not a field. They
    # were added to FontProfile on 2026-08-08 and left out of this table, and
    # the test below is what said so.
    "font": {"ui_family", "ui_size", "monospace_size", "alpha_ladder",
             "manifest",
             "cleartype_gamma", "cleartype_contrast", "cleartype_level",
             "cleartype_pixel_structure", "cleartype_rendering_mode",
             "freetype_gamma", "freetype_contrast"},
}
_PIN_TOP = {"dark_theme"}


def _validate_pin_key(key: str) -> None:
    if key in _PIN_TOP:
        return
    if "." not in key:
        raise ValueError(
            f"pin key {key!r} is not valid. "
            f"Use 'group.field' (e.g. 'screen.width') or one of {sorted(_PIN_TOP)}."
        )
    group, field_name = key.split(".", 1)
    if group not in _PIN_GROUPS:
        raise ValueError(
            f"pin key {key!r}: unknown group {group!r}. "
            f"Known groups: {sorted(_PIN_GROUPS)}."
        )
    if field_name not in _PIN_GROUPS[group]:
        raise ValueError(
            f"pin key {key!r}: unknown field {field_name!r} in group {group!r}. "
            f"Known fields: {sorted(_PIN_GROUPS[group])}."
        )


@dataclass(frozen=True)
class Profile:
    """Coherent browser fingerprint profile sampled from a single integer seed.

    Use `generate_profile(seed)` to build one. Pin specific values at build
    time with `generate_profile(seed, pin={"screen.width": 2560, ...})`.
    """
    seed: int
    gpu: GPUProfile
    screen: ScreenProfile
    hardware: HardwareProfile
    audio: AudioProfile
    codec: CodecProfile
    webgl: WebGLProfile
    font: FontProfile
    dark_theme: bool
    # Bayesian browsing-history: list of {name, category, cookie_profile}
    # dicts sampled from data/browsing_pool.json with per-class CPT. Used
    # by _recaptcha_seed.py to build a coherent cookie pre-seed when the
    # caller opts in via Stealthfox(prep_recaptcha=True).
    browsing_history: List[Dict[str, str]] = field(default_factory=list)
    _raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_prefs_dict(self) -> Dict[str, Any]:
        """Return the flat dict of raw sampler fields, as produced by the
        underlying Bayesian sampler. Stable across releases for a given seed."""
        return dict(self._raw)


# Mapping from flat pin key -> raw sampler dict key, so `to_prefs_dict()`
# and `invisible_playwright.prefs.translate_profile_to_prefs` observe the pinned value.
_PIN_TO_RAW = {
    "gpu.vendor": "webgl_vendor",
    "gpu.renderer": "webgl_renderer",
    "gpu.class_tier": "gpu_class",
    "screen.width": "screen_w",
    "screen.height": "screen_h",
    "screen.avail_width": "screen_avail_w",
    "screen.avail_height": "screen_avail_h",
    "screen.dpr": "dpr",
    "screen.tier": "screen_tier",
    "hardware.concurrency": "hw_concurrency",
    "hardware.storage_quota_mb": "storage_quota_mb",
    "audio.sample_rate": "audio_sample_rate",
    "audio.output_latency_ms": "audio_output_latency_ms",
    "audio.max_channel_count": "audio_max_channel_count",
    "codec.av1_enabled": "av1_enabled",
    "codec.webm_encoder_enabled": "webm_encoder_enabled",
    "codec.mediasource_webm": "mediasource_webm",
    "codec.mediasource_mp4": "mediasource_mp4",
    "codec.webspeech_synth": "webspeech_synth",
    "webgl.msaa_samples": "msaa_samples",
    # Fonts: pinnable like everything else. The sampler does not produce these
    # (they are invariant, see FontProfile), so _sample_raw seeds the raw dict
    # with the canonical values and a pin overwrites them through the same path
    # as any sampled field - one mechanism, not two.
    "font.ui_family": "font_ui_family",
    "font.ui_size": "font_ui_size",
    "font.monospace_size": "font_monospace_size",
    "font.alpha_ladder": "font_alpha_ladder",
    "font.manifest": "font_manifest",
    # The rasterisation parameters travel the same way for the same reason:
    # invariant, so not sampled, but seeded into the raw dict below so a pin
    # overwrites them through the one mechanism instead of a second one.
    "font.cleartype_gamma": "font_cleartype_gamma",
    "font.cleartype_contrast": "font_cleartype_contrast",
    "font.cleartype_level": "font_cleartype_level",
    "font.cleartype_pixel_structure": "font_cleartype_pixel_structure",
    "font.cleartype_rendering_mode": "font_cleartype_rendering_mode",
    "font.freetype_gamma": "font_freetype_gamma",
    "font.freetype_contrast": "font_freetype_contrast",
    "screen.color_depth": "screen_color_depth",
    "screen.taskbar_px": "taskbar_px",
    "screen.chrome_w": "chrome_w",
    "screen.chrome_h": "chrome_h",
    "screen.window_x": "window_x",
    "screen.window_y": "window_y",
    "hardware.max_touch_points": "max_touch_points",
    "hardware.voices": "voices",
    "hardware.fake_media_devices": "fake_media_devices",
    "hardware.storage_enabled": "storage_enabled",
    "hardware.generics": "generics",
    "hardware.accessibility_overrides": "accessibility_overrides",
    "dark_theme": "dark_theme",
}

#: The rasterisation parameters a Windows machine reports, and the FreeType
#: equivalents that make the Linux build produce the same coverage curve.
#: Invariant, so not sampled: DirectWrite reads them from the machine's own
#: ClearType settings, which differ per monitor and per user, and a value that
#: varies with the host is the thing being closed here, not a knob.
FONT_CLEARTYPE_GAMMA = 2200
FONT_CLEARTYPE_CONTRAST = 100
FONT_CLEARTYPE_LEVEL = 100
FONT_CLEARTYPE_PIXEL_STRUCTURE = 1
FONT_CLEARTYPE_RENDERING_MODE = 5
FONT_FREETYPE_GAMMA = 220
FONT_FREETYPE_CONTRAST = 100

#: 24 is what every ordinary Windows desktop reports. Declared rather than read
#: from the panel: a wide-gamut monitor answers 30, and a persona claiming an
#: office laptop with a 30-bit display is a contradiction a page can read.
SCREEN_COLOR_DEPTH = 24

#: The Windows taskbar, re-exported so a pin can reach it by the same name
#: as every other declared constant. The value lives in constants.py, which
#: the sampler imports too - one number, one home.
from ..constants import TASKBAR_PX, CHROME_W, CHROME_H  # noqa: E402,F401

#: The five Windows English (United States) voices, in the order the binary
#: parses them. See HardwareProfile.voices for why this is not per-locale yet.
VOICES = ",".join([
    "Microsoft David - English (United States)|en-US|1|1",
    "Microsoft Zira - English (United States)|en-US|0|1",
    "Microsoft Mark - English (United States)|en-US|0|1",
    "Microsoft David Desktop - English (United States)|en-US|0|1",
    "Microsoft Zira Desktop - English (United States)|en-US|0|1",
])

#: One fake audio input and one fake video input, on every host.
FAKE_MEDIA_DEVICES = True

#: Cookies, localStorage, sessionStorage and indexedDB all working, which is
#: what a real desktop Firefox reports.
STORAGE_ENABLED = True

#: An ordinary desktop with no accessibility overrides turned on.
ACCESSIBILITY_OVERRIDES = False

#: The generic-family table a Windows Firefox resolves to. Moved out of C++ on
#: 2026-08-09; the engine keeps the same rows compiled in as the floor for a
#: browser launched without this package.
GENERICS = chr(10).join([
    "cursive||Comic Sans MS",
    "serif|x-math|Cambria Math",
    "sans-serif|ja|Yu Gothic UI",
    "serif|ja|Yu Gothic UI",
    "monospace|ja|Yu Gothic UI",
    "sans-serif|ko|Malgun Gothic",
    "serif|ko|Malgun Gothic",
    "monospace|ko|Malgun Gothic",
    "sans-serif|zh-CN|Microsoft YaHei UI",
    "serif|zh-CN|Microsoft YaHei UI",
    "monospace|zh-CN|Microsoft YaHei UI",
    "sans-serif|zh-TW|Microsoft JhengHei UI",
    "serif|zh-TW|Microsoft JhengHei UI",
    "monospace|zh-TW|Microsoft JhengHei UI",
    "sans-serif|zh-HK|Microsoft JhengHei UI",
    "serif|zh-HK|Microsoft JhengHei UI",
    "monospace|zh-HK|Microsoft JhengHei UI",
    "serif||Times New Roman",
    "sans-serif||Arial",
    "monospace||Consolas",
])

# MAX_TOUCH_POINTS was here and is gone. It was a constant compiled into the
# binary, then a constant in this file, and it is now a SAMPLED field: the
# forge draws it per GPU class from `data/cpt_touch_given_class.json`
# (all rows on 0 today, and the file says why). A constant here would be the
# second source of truth engine rule 7 refuses, and unlike the C++ one it would
# look harmless - both were 0 once before, and the duplicate was only found by
# setting one of them to 7 and watching the profile still answer 0.

#: The canonical Windows values. Not sampled - see FontProfile for why.
FONT_UI_FAMILY = "Segoe UI"
FONT_UI_SIZE = "12"          # string: Preferences::GetFloat parses text
FONT_MONOSPACE_SIZE = 13     # Firefox ships 13 on Windows, 12 in its Unix block

#: DirectWrite's grayscale coverage ladder, 17 levels. Lives here rather than in
#: the binary's font manifest because it is not a property of any font file: it
#: is what the Windows rasteriser does to a glyph edge, and it belongs beside
#: the other things the core declares about "what Windows looks like".
FONT_ALPHA_LADDER = (
    0, 18, 35, 53, 70, 87, 104, 121, 138, 153, 169, 185, 200, 215, 230, 243, 255,
)


def _apply_pins_to_raw(raw: Dict[str, Any], pin: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of `raw` with the pinned sampler-level fields updated."""
    out = dict(raw)
    for key, value in pin.items():
        raw_key = _PIN_TO_RAW.get(key)
        if raw_key is None:
            # Shouldn't happen after validation, but guard anyway.
            continue
        out[raw_key] = value
    return out


def generate_profile(
    seed: int,
    pin: Optional[Dict[str, Any]] = None,
    fixed_gpu_class: Optional[str] = None,
) -> Profile:
    """Return a deterministic Profile for the given integer seed.

    pin: optional dict of dotted-path keys (e.g. "screen.width", "gpu.renderer")
        to values that are FORCED in the resulting profile. All other fields
        are still sampled from the Bayesian network based on `seed`, so the
        same seed + same pin map always yields the same profile.

        Example - force a specific GPU and screen while letting everything
        else vary with the seed (via the public invisible_playwright API):

            from invisible_playwright import InvisiblePlaywright

            with InvisiblePlaywright(
                seed=42,
                pin={
                    "gpu.renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D11)",
                    "gpu.vendor":   "Google Inc. (NVIDIA)",
                    "gpu.class_tier": "high_end",
                    "screen.width":  2560,
                    "screen.height": 1440,
                },
            ) as browser:
                ...

        Warning: pinning breaks Bayesian coherence across the pinned fields
        (if you pin a high-end GPU but leave screen unpinned, you may get a
        1080p screen that would be unusual for that GPU class). Pin related
        fields together when coherence matters.

        Supported keys: see the module-level _PIN_GROUPS / _PIN_TOP tables
        or run `help(generate_profile)` after import.
    """
    if pin:
        for key in pin:
            _validate_pin_key(key)

    # fixed_gpu_class re-conditions the whole bundle on a chosen class, so the
    # bundle stays coherent with the WebGL persona actually exposed.
    #
    # IT DEFAULTS TO THE SEED'S OWN PERSONA CLASS, and that default is the fix
    # for a live bug rather than a convenience. `prefs.translate_profile_to_prefs`
    # applies `select_persona(profile.seed)` UNCONDITIONALLY, so the GPU a page
    # sees is always the persona's - which means conditioning the bundle on that
    # persona's class is not a policy a caller may choose, it is an invariant of
    # the pipeline. Passing it was left to the caller, and of the five call sites
    # three passed it and two did not:
    #
    #   config.py, launcher.py, async_api.py     passed it
    #   launch.py (the manager's LAUNCH path)    did not
    #   manager/fingerprint.py (the UI preview)  did not
    #
    # Measured over 500 seeds before this default: 355 (71%) of manager profiles
    # emitted different prefs from the wrapper's for the same seed - storage
    # quota, audio output latency, sample rate, screen size, devicePixelRatio,
    # av1 - every one of them a value the identification service cross-checks
    # against the reported GPU. A profile advertising a mid_range renderer while
    # carrying low_end storage and audio is exactly the internal contradiction
    # the per-GPU pool exists to remove.
    #
    # An explicit `gpu.class_tier` pin still wins, then an explicit
    # `fixed_gpu_class`; only the unspecified case changed.
    eff_class = ((pin or {}).get("gpu.class_tier")
                 or fixed_gpu_class
                 or _forced_gpu_class(int(seed)))
    raw = _sample_raw(int(seed), fixed_gpu_class=eff_class)
    # The GPU NAME the profile reports is the persona's, because the persona is
    # what the browser actually presents.
    #
    # Two pools existed and disagreed. `webgl_renderer_pool.json` has 444 bare
    # names and drives the marginal draw inside `_sample_raw`;
    # `webgl_gpu_pool.json` has the validated Windows personas, each carrying
    # renderer + vendor + extensions + ~100 getParameter values, and
    # `_apply_gpu_persona` writes THAT one into `zoom.stealth.webgl.renderer`.
    # Nothing ever wrote the sampled name into a pref - measured on seed 42, the
    # 224 emitted prefs contain the persona (Intel HD Graphics) and no trace of
    # the sampled GTX 1650 - so `Profile.gpu.renderer` was a label that
    # contradicted the page for every seed with a persona, and the
    # profile-manager showed the label to the user.
    #
    # The DRAW stays. Removing it would renormalise the marginal and remap every
    # identity, which is the weighted-pool rule; and its value still feeds
    # `classify_gpu` when no persona exists. What changes is only which of the
    # two names gets REPORTED, so there is one source instead of two.
    #
    # This runs BEFORE `_apply_pins_to_raw`, so an explicit `gpu.renderer` pin
    # still wins, exactly like `eff_class` above.
    _persona = _select_persona(int(seed))
    if _persona:
        raw["webgl_renderer"] = _persona["renderer"]
        raw["webgl_vendor"] = _persona["vendor"]
    # Seed the invariant font fields BEFORE pins, so a `font.*` pin overwrites
    # them through _apply_pins_to_raw like any sampled field, and so
    # `to_prefs_dict()` reports them alongside everything else.
    raw.setdefault("font_ui_family", FONT_UI_FAMILY)
    raw.setdefault("font_ui_size", FONT_UI_SIZE)
    raw.setdefault("font_monospace_size", FONT_MONOSPACE_SIZE)
    raw.setdefault("font_alpha_ladder", FONT_ALPHA_LADDER)
    raw.setdefault("font_manifest", FONT_MANIFEST)
    raw.setdefault("font_cleartype_gamma", FONT_CLEARTYPE_GAMMA)
    raw.setdefault("font_cleartype_contrast", FONT_CLEARTYPE_CONTRAST)
    raw.setdefault("font_cleartype_level", FONT_CLEARTYPE_LEVEL)
    raw.setdefault("font_cleartype_pixel_structure", FONT_CLEARTYPE_PIXEL_STRUCTURE)
    raw.setdefault("font_cleartype_rendering_mode", FONT_CLEARTYPE_RENDERING_MODE)
    raw.setdefault("font_freetype_gamma", FONT_FREETYPE_GAMMA)
    raw.setdefault("font_freetype_contrast", FONT_FREETYPE_CONTRAST)
    raw.setdefault("screen_color_depth", SCREEN_COLOR_DEPTH)
    raw.setdefault("taskbar_px", TASKBAR_PX)
    raw.setdefault("chrome_w", CHROME_W)
    raw.setdefault("chrome_h", CHROME_H)
    raw.setdefault("window_x", 0)
    raw.setdefault("window_y", 0)
    # No setdefault for max_touch_points: the forge samples it, and a floor
    # here would be a second source of truth that agrees until it does not.
    raw.setdefault("voices", VOICES)
    raw.setdefault("fake_media_devices", FAKE_MEDIA_DEVICES)
    raw.setdefault("storage_enabled", STORAGE_ENABLED)
    raw.setdefault("generics", GENERICS)
    raw.setdefault("accessibility_overrides", ACCESSIBILITY_OVERRIDES)
    if pin:
        raw = _apply_pins_to_raw(raw, pin)
        # The sampler derives screen_avail_h from the DEFAULT taskbar, and the
        # pins land afterwards, so pinning the taskbar alone used to leave
        # availHeight describing a different window from the one everything
        # else was sized against: measured 2026-08-09, screen.taskbar_px=72 on
        # a 1080 screen still reported avail_height 1032, which is 1080-48.
        # Two properties of one window disagreeing is exactly the shape a page
        # reads for free. Re-derive it here - unless avail_height was itself
        # pinned, in which case the caller said what they wanted and an
        # override must not overwrite a more specific override.
        if "screen.taskbar_px" in pin and "screen.avail_height" not in pin:
            raw["screen_avail_h"] = int(raw["screen_h"]) - int(raw["taskbar_px"])

    return Profile(
        seed=int(raw["stealth_seed"]),
        gpu=GPUProfile(
            vendor=raw["webgl_vendor"],
            renderer=raw["webgl_renderer"],
            class_tier=raw["gpu_class"],
        ),
        screen=ScreenProfile(
            width=int(raw["screen_w"]),
            height=int(raw["screen_h"]),
            avail_width=int(raw["screen_avail_w"]),
            avail_height=int(raw["screen_avail_h"]),
            dpr=float(raw["dpr"]),
            tier=str(raw.get("screen_tier", "")),
            color_depth=int(raw["screen_color_depth"]),
            taskbar_px=int(raw["taskbar_px"]),
            chrome_w=int(raw["chrome_w"]),
            chrome_h=int(raw["chrome_h"]),
            window_x=int(raw["window_x"]),
            window_y=int(raw["window_y"]),
        ),
        hardware=HardwareProfile(
            concurrency=int(raw["hw_concurrency"]),
            storage_quota_mb=int(raw["storage_quota_mb"]),
            max_touch_points=int(raw["max_touch_points"]),
            voices=str(raw["voices"]),
            fake_media_devices=bool(raw["fake_media_devices"]),
            storage_enabled=bool(raw["storage_enabled"]),
            generics=str(raw["generics"]),
            accessibility_overrides=bool(raw["accessibility_overrides"]),
        ),
        audio=AudioProfile(
            sample_rate=int(raw["audio_sample_rate"]),
            output_latency_ms=int(raw["audio_output_latency_ms"]),
            max_channel_count=int(raw["audio_max_channel_count"]),
        ),
        codec=CodecProfile(
            av1_enabled=bool(raw["av1_enabled"]),
            webm_encoder_enabled=bool(raw["webm_encoder_enabled"]),
            mediasource_webm=bool(raw["mediasource_webm"]),
            mediasource_mp4=bool(raw["mediasource_mp4"]),
            webspeech_synth=bool(raw["webspeech_synth"]),
        ),
        webgl=WebGLProfile(msaa_samples=int(raw["msaa_samples"])),
        font=FontProfile(
            ui_family=str(raw["font_ui_family"]),
            # str(), not int(): the pref is read as text and an int here would
            # travel all the way to a silently-ignored pref.
            ui_size=str(raw["font_ui_size"]),
            monospace_size=int(raw["font_monospace_size"]),
            alpha_ladder=tuple(int(v) for v in raw["font_alpha_ladder"]),
            manifest=str(raw["font_manifest"]),
            cleartype_gamma=int(raw["font_cleartype_gamma"]),
            cleartype_contrast=int(raw["font_cleartype_contrast"]),
            cleartype_level=int(raw["font_cleartype_level"]),
            cleartype_pixel_structure=int(raw["font_cleartype_pixel_structure"]),
            cleartype_rendering_mode=int(raw["font_cleartype_rendering_mode"]),
            freetype_gamma=int(raw["font_freetype_gamma"]),
            freetype_contrast=int(raw["font_freetype_contrast"]),
        ),
        dark_theme=bool(raw["dark_theme"]),
        browsing_history=list(raw.get("browsing_history") or []),
        _raw=raw,
    )


# ──────────────────────────────────────────────────────────────────────
#  The font manifest, verbatim.
#
#  GENERATED - produced by scripts/gen_bundle_font_manifest.py in the Firefox
#  tree from the 125 bundled font files, and pasted here. Do not hand-edit: the
#  numbers are read out of each face's OS/2, hhea and head tables, so a value
#  typed here would describe a font that does not exist.
#
#  A raw string on purpose: the alias comment carries two backslashes
#  (HKLM\...\FontSubstitutes) and a cooked literal would turn them into escape
#  sequences, which is the same corruption this project has hit four times
#  through shell heredocs.
#
#  It lives beside the other font values rather than in a data file so the
#  profile is the single object that says what Windows looks like.
# ──────────────────────────────────────────────────────────────────────

FONT_MANIFEST = r"""# bundle-fonts.list v3 - generated by scripts/gen_bundle_font_manifest.py; do not edit by hand
# NOTE: the L| coverage ladder was removed 2026-08-08. It is not a property
#   of any font FILE - it is what the rasteriser does to a glyph edge - so it
#   is declared by invisible_core and delivered as a pref. Keeping it here as
#   well would have been a second copy of a value with one owner.
# A|alias|target  - canonical Windows font-substitute table, applied on EVERY OS.
#   Replaces the per-host lookup: Windows read these from the registry
#   (HKLM\...\FontSubstitutes) with no bundle-only gate, so the set varied by
#   machine, and Linux/macOS had none at all. Source: Mozilla's own
#   kFontSubstitutes (StandardFonts-win10.inc) + sDirectWriteSubs, parsed from
#   the tree so there is no second copy to drift.
# f|file|index|w_min|w_max|stretch_min|stretch_max|style|psname|upem|ascent|descent|lineGap|xHeight|capHeight|underlineOffset|underlineSize|strikeoutOffset|strikeoutSize
# v2 adds the vertical metrics, in FONT UNITS (scale by size/upem).
# They exist so no platform backend (DWrite/FreeType/CoreText) computes
# them: each derived its own from the same file and they disagreed on 58
# of 72 families. Descent is positive. See _vertical_metrics().
# P|font|...        - common-fallback prefix used when the character asks for
#   COLOUR presentation; the same list is appended LAST when it asks for
#   monochrome, which is what gfxWindowsPlatform does.
# S|<Script>|font|...- per-script common-fallback list, keyed by the NUMERIC
#   value of mozilla::intl::Script (HAN=17, LATIN=25, ...), read from
#   UnicodeScriptCodes.h so the key cannot drift from the enum.
# T|font|...        - appended after the per-script list, always.
# Y|font|...        - appended only for symbols/punctuation (the character
#   test stays in shared C++: it is Unicode, not a host font engine).
# Z|font|...        - appended after Y.
#   All five come from gfxWindowsPlatform::GetCommonFallbackFonts, PARSED
#   from the tree. They exist because that function is per-platform and the
#   GTK one was a flat list with four Windows names in front - which is why
#   U+4E00 resolves to MS Gothic without it and SimSun with it.
L|0|18|35|53|70|87|104|121|138|153|169|185|200|215|230|243|255
P|Segoe UI Emoji|Twemoji Mozilla
T|Arial
Y|Segoe UI|Segoe UI Symbol|Cambria Math
Z|Arial Unicode MS
S|4|Vrinda|Nirmala UI
S|5|SimSun|SimSun-ExtB
S|6|Gadugi
S|7|Noto Sans Coptic
S|9|Segoe UI Symbol
S|10|Kokila|Nirmala UI
S|11|Nyala|Ebrima
S|12|Segoe UI
S|13|Segoe UI Historic
S|15|Shruti|Nirmala UI
S|16|Raavi|Nirmala UI
S|17|SimSun|SimSun-ExtB
S|18|Malgun Gothic
S|20|Yu Gothic|MS PGothic
S|21|Tunga|Nirmala UI
S|22|Yu Gothic|MS PGothic
S|23|Khmer UI
S|24|Lao UI
S|26|Kartika|Nirmala UI
S|27|Mongolian Baiti
S|28|Myanmar Text
S|29|Segoe UI Historic
S|30|Segoe UI Historic
S|31|Kalinga|Nirmala UI
S|32|Segoe UI Historic
S|33|Iskoola Pota|Nirmala UI
S|34|Estrangelo Edessa
S|35|Latha|Nirmala UI
S|36|Gautami|Nirmala UI
S|37|MV Boli
S|38|Tahoma|Leelawadee UI
S|39|Microsoft Himalaya
S|40|Euphemia
S|41|Microsoft Yi Baiti
S|42|Noto Sans Tagalog
S|43|Noto Sans Hanunoo
S|44|Noto Sans Buhid
S|45|Noto Sans Tagbanwa
S|46|Segoe UI Symbol
S|47|Segoe UI Historic
S|48|Noto Sans Limbu
S|49|Noto Sans Linear B
S|50|Ebrima
S|51|Segoe UI Historic
S|52|Microsoft Tai Le
S|53|Segoe UI Historic
S|54|Yu Gothic|MS PGothic
S|55|Leelawadee UI
S|56|Segoe UI Historic
S|57|Segoe UI Historic
S|58|Noto Sans Syloti Nagri
S|59|Microsoft New Tai Lue
S|60|Ebrima
S|61|Segoe UI Historic
S|62|Noto Sans Balinese
S|63|Noto Sans Batak
S|65|Segoe UI Historic
S|66|Noto Sans Cham
S|71|Segoe UI Historic
S|72|Segoe UI
S|73|SimSun|SimSun-ExtB
S|74|MingLiU|MingLiU-ExtB
S|75|Noto Sans Pahawh Hmong
S|76|Noto Sans Old Hungarian
S|78|Javanese Text
S|79|Noto Sans Kayah Li
S|82|Noto Sans Lepcha
S|83|Noto Sans Linear A
S|84|Noto Sans Mandaic
S|86|Noto Sans Meroitic
S|87|Ebrima
S|88|Segoe UI Historic
S|89|Noto Sans Old Permic
S|90|Microsoft PhagsPa
S|91|Segoe UI Historic
S|92|Noto Sans Miao
S|95|Estrangelo Edessa
S|99|Ebrima
S|101|Segoe UI Historic
S|104|Segoe UI Historic
S|105|Yu Gothic|MS PGothic
S|106|Noto Sans Tai Tham
S|107|Segoe UI Historic
S|108|Segoe UI Historic
S|109|Nirmala UI
S|110|Noto Sans Rejang
S|111|Noto Sans Saurashtra
S|113|Noto Sans Sundanese
S|115|Nirmala UI
S|116|Segoe UI Historic
S|117|Noto Sans Avestan
S|118|Nirmala UI
S|119|Malgun Gothic
S|120|Noto Sans Kaithi
S|121|Noto Sans Manichaean
S|122|Segoe UI Historic
S|123|Noto Sans Psalter Pahlavi
S|125|Segoe UI Historic
S|126|Noto Sans Samaritan
S|127|Noto Sans Tai Viet
S|130|Noto Sans Bamum
S|131|Segoe UI
S|133|Segoe UI Historic
S|134|Noto Sans Bassa Vah
S|135|Noto Sans Duployan
S|136|Noto Sans Elbasan
S|137|Noto Sans Grantha
S|140|Noto Sans Mende Kikakui
S|141|Segoe UI Historic
S|142|Noto Sans Old North Arabian
S|143|Noto Sans Nabataean
S|144|Noto Sans Palmyrene
S|145|Noto Sans Khudawadi
S|146|Noto Sans Warang Citi
S|149|Noto Sans Mro
S|151|Noto Sans Sharada
S|152|Nirmala UI
S|153|Noto Sans Takri
S|157|Noto Sans Khojki
S|158|Noto Sans Tirhuta
S|159|Noto Sans Caucasian Albanian
S|160|Noto Sans Mahajani
S|161|Noto Serif Ahom
S|162|Noto Sans Hatran
S|163|Noto Sans Modi
S|164|Noto Sans Multani
S|165|Noto Sans Pau Cin Hau
S|166|Noto Sans Siddham
S|167|Ebrima
S|168|Noto Sans Bhaiksuki
S|169|Noto Sans Marchen
S|170|Noto Sans Newa
S|171|Gadugi
S|172|SimSun|SimSun-ExtB
S|173|Malgun Gothic
S|182|Noto Sans Hanifi Rohingya
S|188|Noto Sans Wancho
S|200|Urdu Typesetting
S|212|MingLiU|MingLiU-ExtB
A|Arabic Transparent|Arial
A|Arial (Arabic)|Arial
A|Arial (Hebrew)|Arial
A|Arial Baltic|Arial
A|Arial Black|Arial
A|Arial CE|Arial
A|Arial CYR|Arial
A|Arial Greek|Arial
A|Arial TUR|Arial
A|Courier|Courier New
A|Courier New (Arabic)|Courier New
A|Courier New (Hebrew)|Courier New
A|Courier New Baltic|Courier New
A|Courier New CE|Courier New
A|Courier New CYR|Courier New
A|Courier New Greek|Courier New
A|Courier New TUR|Courier New
A|David Transparent|David
A|FangSong_GB2312|FangSong
A|Fixed Miriam Transparent|Miriam Fixed
A|Fixedsys Greek|Fixedsys
A|Franklin Gothic Medium|Franklin Gothic
A|Helv|Microsoft Sans Serif
A|Helvetica|Arial
A|KaiTi_GB2312|KaiTi
A|MS Sans Serif|Microsoft Sans Serif
A|MS Sans Serif Greek|Microsoft Sans Serif
A|MS Serif|Times New Roman
A|MS Serif Greek|Times New Roman
A|MS Shell Dlg|Microsoft Sans Serif
A|MS Shell Dlg 2|Tahoma
A|Miriam Transparent|Miriam
A|Rod Transparent|Rod
A|Roman|Times New Roman
A|Script|Mistral
A|Segoe UI Light|Segoe UI
A|Segoe UI Semilight|Segoe UI
A|Small Fonts|Arial
A|Small Fonts Greek|Arial
A|System Greek|System
A|Tahoma Armenian|Tahoma
A|Times|Times New Roman
A|Times New Roman (Arabic)|Times New Roman
A|Times New Roman (Hebrew)|Times New Roman
A|Times New Roman Baltic|Times New Roman
A|Times New Roman CE|Times New Roman
A|Times New Roman CYR|Times New Roman
A|Times New Roman Greek|Times New Roman
A|Times New Roman TUR|Times New Roman
A|Tms Rmn|Times New Roman
A|Yu Gothic Medium|Yu Gothic
F|Arial
f|arial7_04.ttf|0|400|400|100|100|normal|ArialMT|2048|1854|434|67|1062|1467|-217|150|530|102
f|arialbd7_04.ttf|0|700|700|100|100|normal|Arial-BoldMT|2048|1854|434|67|1062|1466|-217|215|530|102
f|arialbi7_04.ttf|0|700|700|100|100|italic|Arial-BoldItalicMT|2048|1854|434|67|1062|1465|-217|215|530|102
f|ariali7_04.ttf|0|400|400|100|100|italic|Arial-ItalicMT|2048|1854|434|67|1062|1466|-217|150|530|102
f|ariblk.ttf|0|900|900|100|100|normal|Arial-Black|2048|2254|634|0|1062|1466|-256|123|530|102
F|Bahnschrift
f|bahnschrift2_07.ttf|0|300|700|75|100|normal|Bahnschrift|2048|1626|422|410|1038|1454|-100|50|622|102
F|Calibri
f|calibri6_26.ttf|0|400|400|100|100|normal|Calibri|2048|1950|550|0|951|1294|-232|134|512|134
f|calibrib6_26.ttf|0|700|700|100|100|normal|Calibri-Bold|2048|1950|550|0|960|1294|-232|134|512|186
f|calibrii6_26.ttf|0|400|400|100|100|italic|Calibri-Italic|2048|1950|550|0|957|1297|-232|134|512|134
f|calibriz6_26.ttf|0|700|700|100|100|italic|Calibri-BoldItalic|2048|1950|550|0|960|1294|-232|134|512|186
F|Cambria
f|cambria6_99.ttc|0|400|400|100|100|normal|Cambria|2048|1946|455|0|956|1365|-125|116|510|102
f|cambriab6_98.ttf|0|700|700|100|100|normal|Cambria-Bold|2048|1946|455|0|992|1365|-105|156|510|102
f|cambriai6_98.ttf|0|400|400|100|100|italic|Cambria-Italic|2048|1946|455|0|956|1365|-125|116|510|102
f|cambriaz6_98.ttf|0|700|700|100|100|italic|Cambria-BoldItalic|2048|1946|455|0|992|1365|-105|156|510|102
F|Cambria Math
f|cambria6_99.ttc|1|400|400|100|100|normal|CambriaMath|2048|1593|455|353|956|1365|-125|116|510|102
F|Candara
f|candara5_64.ttf|0|400|400|100|100|normal|Candara|2048|1950|550|0|950|1308|-133|20|530|102
f|candarab5_64.ttf|0|700|700|100|100|normal|Candara-Bold|2048|1950|550|0|950|1308|-133|20|530|102
f|candarai5_64.ttf|0|400|400|100|100|italic|Candara-Italic|2048|1950|550|0|961|1308|-133|20|530|102
f|candaraz5_64.ttf|0|700|700|100|100|italic|Candara-BoldItalic|2048|1950|550|0|961|1308|-133|20|530|102
F|Comic Sans MS
f|comic5_15.ttf|0|400|400|100|100|normal|ComicSansMS|2048|2257|597|0|1105|1554|-272|175|630|175
f|comicbd5_15.ttf|0|700|700|100|100|normal|ComicSansMS-Bold|2048|2257|597|0|1105|1554|-272|175|630|175
f|comici5_15.ttf|0|400|400|100|100|italic|ComicSansMS-Italic|2048|2257|597|0|1105|1554|-272|175|630|175
f|comicz5_15.ttf|0|700|700|100|100|italic|ComicSansMS-BoldItalic|2048|2257|597|0|1105|1554|-272|175|630|175
F|Consolas
f|consola7_00.ttf|0|400|400|100|100|normal|Consolas|2048|1884|514|0|1004|1307|-266|144|512|102
f|consolab7_00.ttf|0|700|700|100|100|normal|Consolas-Bold|2048|1884|514|0|1016|1307|-217|194|512|194
f|consolai7_00.ttf|0|400|400|100|100|italic|Consolas-Italic|2048|1884|514|0|1004|1307|-266|144|512|102
f|consolaz7_00.ttf|0|700|700|100|100|italic|Consolas-BoldItalic|2048|1884|514|0|1016|1307|-217|194|512|194
F|Constantia
f|constan5_93.ttf|0|400|400|100|100|normal|Constantia|2048|1950|550|0|928|1406|-154|102|450|100
f|constanb5_93.ttf|0|700|700|100|100|normal|Constantia-Bold|2048|1950|550|0|934|1406|-154|102|450|100
f|constani5_93.ttf|0|400|400|100|100|italic|Constantia-Italic|2048|1950|550|0|938|1406|-154|102|450|100
f|constanz5_93.ttf|0|700|700|100|100|italic|Constantia-BoldItalic|2048|1950|550|0|951|1406|-154|102|450|100
F|Corbel
f|corbel6_01.ttf|0|400|400|100|100|normal|Corbel|2048|1950|550|0|950|1338|-190|120|512|102
f|corbelb6_01.ttf|0|700|700|100|100|normal|Corbel-Bold|2048|1950|550|0|969|1338|-190|120|512|102
f|corbeli6_01.ttf|0|400|400|100|100|italic|Corbel-Italic|2048|1950|550|0|950|1338|-190|120|512|102
f|corbelz6_01.ttf|0|700|700|100|100|italic|Corbel-BoldItalic|2048|1950|550|0|969|1338|-190|120|512|102
F|Courier New
f|cour6_94.ttf|0|400|400|100|100|normal|CourierNewPSMT|2048|1705|615|0|866|1170|-477|84|530|102
f|courbd6_94.ttf|0|700|700|100|100|normal|CourierNewPS-BoldMT|2048|1705|615|0|908|1212|-477|205|530|102
f|courbi6_94.ttf|0|700|700|100|100|italic|CourierNewPS-BoldItalicMT|2048|1705|615|0|908|1212|-477|205|530|102
f|couri6_94.ttf|0|400|400|100|100|italic|CourierNewPS-ItalicMT|2048|1705|615|0|866|1170|-477|84|530|102
F|Ebrima
f|ebrima5_19.ttf|0|400|400|100|100|normal|Ebrima|2048|2210|514|57|1024|1434|-178|119|530|102
f|ebrimabd5_19.ttf|0|700|700|100|100|normal|Ebrima-Bold|2048|2056|498|59|1024|1434|-178|119|512|102
F|Franklin Gothic
f|framd5_02.ttf|0|400|400|100|100|normal|FranklinGothic-Medium|2048|1877|445|0|0|0|-154|102|530|102
f|framdit5_01.ttf|0|400|400|100|100|italic|FranklinGothic-MediumItalic|2048|1877|445|0|0|0|-154|102|530|102
F|Gabriola
f|gabriola5_93.ttf|0|400|400|100|100|normal|Gabriola|4096|2800|1296|2867|1405|2286|-280|140|750|140
F|Gadugi
f|gadugi1_13.ttf|0|400|400|100|100|normal|Gadugi|2048|2210|514|0|1024|1434|-178|119|530|102
f|gadugib1_13.ttf|0|700|700|100|100|normal|Gadugi-Bold|2048|2210|514|0|1024|1434|-178|119|530|102
F|Georgia
f|georgia5_59.ttf|0|400|400|100|100|normal|Georgia|2048|1878|449|0|986|1419|-181|101|594|101
f|georgiab5_59.ttf|0|700|700|100|100|normal|Georgia-Bold|2048|1878|449|0|992|1419|-180|122|618|122
f|georgiai5_59.ttf|0|400|400|100|100|italic|Georgia-Italic|2048|1878|449|0|1001|1419|-183|96|589|96
f|georgiaz5_59.ttf|0|700|700|100|100|italic|Georgia-BoldItalic|2048|1878|449|0|1015|1419|-181|120|618|120
F|Impact
f|impact5_11.ttf|0|400|400|75|75|normal|Impact|2048|2066|432|0|1327|1619|-205|102|690|102
F|Ink Free
f|inkfree2_00.ttf|0|400|400|100|100|normal|InkFree|1000|910|328|0|527|817|-75|50|325|50
F|Javanese Text
f|javatext1_10.ttf|0|400|400|100|100|normal|JavaneseText|2048|2560|2090|0|852|1453|-154|102|512|80
F|Leelawadee
f|LEELAWAD.TTF|0|400|400|100|100|normal|Leelawadee|2048|1960|489|0|1024|1434|-178|119|452|119
F|Leelawadee UI
f|leelauib5_06.ttf|0|700|700|100|100|normal|LeelawadeeUI-Bold|2048|2210|514|0|1024|1434|-178|119|452|119
f|LEELAWUI.TTF|0|400|400|100|100|normal|LeelawadeeUI|2048|2210|514|0|1024|1434|-178|119|452|119
f|leelawui5_06.ttf|0|400|400|100|100|normal|LeelawadeeUI|2048|2210|514|0|1024|1434|-178|119|452|119
F|Lucida Console
f|LUCON.TTF|0|400|400|87.5|87.5|normal|LucidaConsole|2048|1616|432|0|0|0|-205|102|579|102
F|Lucida Sans Unicode
f|L_10646.TTF|0|400|400|100|100|normal|LucidaSansUnicode|2048|2246|901|0|0|0|-205|102|579|102
F|MS Gothic
f|msgothic5_32.ttc|0|400|400|100|100|normal|MS-Gothic|256|220|36|0|115|174|-17|19|66|13
F|MS PGothic
f|msgothic5_32.ttc|2|400|400|100|100|normal|MS-PGothic|256|220|36|0|115|174|-17|19|66|13
F|MS UI Gothic
f|msgothic5_32.ttc|1|400|400|100|100|normal|MS-UIGothic|256|220|36|0|115|174|-17|19|66|13
F|MV Boli
f|mvboli6_85.ttf|0|400|400|100|100|normal|MVBoli|2048|2333|967|0|825|1464|-340|100|430|102
F|Malgun Gothic
f|malgun.ttf|0|400|400|100|100|normal|MalgunGothic|2048|2229|495|0|1050|1471|-400|119|627|102
F|Marlett
f|marlett5_01.ttf|0|500|500|100|100|normal|Marlett|2048|2048|0|0|0|0|0|0|0|0
F|Microsoft Himalaya
f|himalaya5_23.ttf|0|400|400|100|100|normal|MicrosoftHimalaya|2048|1212|836|0|614|909|-51|102|330|110
F|Microsoft JhengHei
f|msjh6_15.ttc|0|400|400|100|100|normal|MicrosoftJhengHeiRegular|2048|2203|521|0|1106|1549|-178|119|512|102
F|Microsoft JhengHei UI
f|msjh6_15.ttc|1|400|400|100|100|normal|MicrosoftJhengHeiUIRegular|2048|2080|521|0|1106|1549|-178|119|512|102
F|Microsoft New Tai Lue
f|ntailu5_99.ttf|0|400|400|100|100|normal|MicrosoftNewTaiLue|2048|1899|780|0|1024|1434|-179|119|452|119
f|ntailub5_99.ttf|0|700|700|100|100|normal|MicrosoftNewTaiLue-Bold|2048|1899|780|0|1024|1434|-178|119|452|119
F|Microsoft PhagsPa
f|phagspa6_00.ttf|0|400|400|100|100|normal|MicrosoftPhagsPa|2048|2138|482|0|1024|1434|-100|200|530|102
f|phagspab6_00.ttf|0|700|700|100|100|normal|MicrosoftPhagsPa-Bold|2048|2138|482|0|1024|1434|-100|200|530|102
F|Microsoft Sans Serif
f|micross7_03.ttf|0|400|400|100|100|normal|MicrosoftSansSerif|2048|1888|430|0|1061|1466|-220|102|530|102
F|Microsoft Tai Le
f|taile6_00.ttf|0|400|400|100|100|normal|MicrosoftTaiLe|2048|1899|705|0|1024|1434|-178|119|452|119
f|taileb6_00.ttf|0|700|700|100|100|normal|MicrosoftTaiLe-Bold|2048|1899|705|0|1024|1434|-178|119|452|119
F|Microsoft Uighur
f|MSUIGHUB.TTF|0|700|700|100|100|normal|MicrosoftUighur-Bold|2048|1400|648|166|571|968|-154|102|512|84
f|MSUIGHUR.TTF|0|400|400|100|100|normal|MicrosoftUighur|2048|1400|648|166|572|966|-154|102|512|84
F|Microsoft YaHei
f|msyh6_31.ttc|0|400|400|100|100|normal|MicrosoftYaHei|2048|2167|536|0|1106|1549|-178|129|655|102
F|Microsoft YaHei UI
f|msyh6_31.ttc|1|400|400|100|100|normal|MicrosoftYaHeiUI|2048|2080|521|0|1106|1549|-178|129|655|102
F|Microsoft Yi Baiti
f|msyi6_00.ttf|0|400|400|100|100|normal|Microsoft-Yi-Baiti|2048|1760|290|53|779|1104|6|102|512|102
F|MingLiU-ExtB
f|mingliub.ttc|0|400|400|100|100|normal|MingLiU-ExtB|1024|820|204|204|440|675|-110|50|260|51
F|Mongolian Baiti
f|monbaiti5_54.ttf|0|400|400|100|100|normal|MongolianBaiti|2048|1729|449|184|875|1480|-304|96|512|88
F|Myanmar Text
f|mmrtext1_21.ttf|0|400|400|100|100|normal|MyanmarText|2048|2122|1689|0|1026|1434|-178|119|530|102
f|mmrtextb1_21.ttf|0|700|700|100|100|normal|MyanmarText-Bold|2048|2210|1689|0|1024|1434|-178|119|530|102
F|NSimSun
f|simsun.ttc|1|400|400|100|100|normal|NSimSun|256|220|36|36|116|175|-22|12|65|12
F|Nirmala UI
f|Nirmala.ttf|0|400|400|100|100|normal|NirmalaUI|2048|2210|514|0|1024|1434|-178|72|530|102
F|PMingLiU-ExtB
f|mingliub.ttc|1|400|400|100|100|normal|PMingLiU-ExtB|1024|820|204|204|440|675|-110|50|260|51
F|Palatino Linotype
f|pala5_03.ttf|0|400|400|100|100|normal|PalatinoLinotype-Roman|2048|2150|613|0|1062|1466|-208|120|530|120
f|palab5_03.ttf|0|700|700|100|100|normal|PalatinoLinotype-Bold|2048|2150|613|0|1062|1466|-208|120|530|122
f|palabi5_03.ttf|0|700|700|100|100|italic|PalatinoLinotype-BoldItalic|2048|2150|613|0|1062|1466|-256|103|530|120
f|palai5_03.ttf|0|400|400|100|100|italic|PalatinoLinotype-Italic|2048|2150|613|0|1062|1466|-208|120|530|120
F|Segoe Print
f|segoepr5_04.ttf|0|400|400|100|100|normal|SegoePrint|2048|2555|1014|46|1020|1392|-133|143|612|102
f|segoeprb5_04.ttf|0|700|700|100|100|normal|SegoePrint-Bold|2048|2555|1014|46|1037|1394|-154|102|620|102
F|Segoe Script
f|segoesc5_03.ttf|0|400|400|100|100|normal|SegoeScript|2048|2230|1014|0|1050|1379|-133|143|627|102
f|segoescb5_03.ttf|0|700|700|100|100|normal|SegoeScript-Bold|2048|2230|1014|45|1065|1394|-133|143|634|102
F|Segoe UI
f|segoeui5_67.ttf|0|400|400|100|100|normal|SegoeUI|2048|2210|514|0|1024|1434|-178|119|530|102
f|segoeuib5_67.ttf|0|700|700|100|100|normal|SegoeUI-Bold|2048|2210|514|0|1024|1434|-178|119|530|102
f|segoeuii5_66.ttf|0|400|400|100|100|italic|SegoeUI-Italic|2048|2210|514|0|1024|1434|-178|119|530|102
f|segoeuil.ttf|0|300|300|100|100|normal|SegoeUI-Light|2048|2210|514|0|1024|1434|-178|119|530|102
f|segoeuisl.ttf|0|350|350|100|100|normal|SegoeUI-Semilight|2048|2210|514|0|1024|1434|-178|119|530|102
f|segoeuiz5_66.ttf|0|700|700|100|100|italic|SegoeUI-BoldItalic|2048|2210|514|0|1024|1434|-178|119|530|102
F|Segoe UI Emoji
f|seguiemj1_60.ttf|0|400|400|100|100|normal|SegoeUIEmoji|2048|2210|514|0|1024|1434|-178|119|530|102
F|Segoe UI Historic
f|seguihis1_09.ttf|0|400|400|100|100|normal|SegoeUIHistoric|2048|2210|514|0|1024|1434|-178|119|530|102
F|Segoe UI Symbol
f|seguisym6_24.ttf|0|400|400|100|100|normal|SegoeUISymbol|2048|2210|514|0|1024|1434|-178|119|530|102
F|SimSun
f|simsun.ttc|0|400|400|100|100|normal|SimSun|256|220|36|36|116|175|-22|12|65|12
F|SimSun-ExtB
f|simsunb5_08.ttf|0|400|400|100|100|normal|SimSun-ExtB|256|220|36|0|0|0|-18|12|65|12
F|Sitka Small
f|SitkaSmall.ttf|0|400|400|100|100|normal|SitkaSmall-Regular|2250|1703|547|563|1133|1484|-158|104|562|112
F|Sylfaen
f|sylfaen5_08.ttf|0|400|400|100|100|normal|Sylfaen|2048|2062|635|0|889|1378|-184|95|530|102
F|Symbol
f|symbol5_01.ttf|0|400|400|100|100|normal|SymbolMT|2048|2059|450|0|0|0|-223|100|530|102
F|Tahoma
f|tahoma7_04.ttf|0|400|400|100|100|normal|Tahoma|2048|2049|423|0|1117|1489|-170|130|689|130
f|tahomabd7_04.ttf|0|700|700|100|100|normal|Tahoma-Bold|2048|2049|423|0|1123|1489|-144|201|689|201
F|Times New Roman
f|times7_11.ttf|0|400|400|100|100|normal|TimesNewRomanPSMT|2048|1825|443|87|916|1356|-223|100|530|102
f|timesbd7_11.ttf|0|700|700|100|100|normal|TimesNewRomanPS-BoldMT|2048|1825|443|87|935|1356|-223|195|530|102
f|timesbi7_11.ttf|0|700|700|100|100|italic|TimesNewRomanPS-BoldItalicMT|2048|1825|443|87|899|1356|-223|195|530|102
f|timesi7_11.ttf|0|400|400|100|100|italic|TimesNewRomanPS-ItalicMT|2048|1825|443|87|881|1356|-223|100|530|102
F|Trebuchet MS
f|trebuc5_15.ttf|0|400|400|100|100|normal|TrebuchetMS|2048|1923|455|0|1071|1465|-261|127|530|127
f|trebucbd5_15.ttf|0|700|700|100|100|normal|TrebuchetMS-Bold|2048|1923|455|0|1071|1465|-261|200|530|200
f|trebucbi5_15.ttf|0|700|700|100|100|italic|Trebuchet-BoldItalic|2048|1923|455|0|1071|1465|-261|200|435|200
f|trebucit5_15.ttf|0|400|400|100|100|italic|TrebuchetMS-Italic|2048|1923|455|0|1071|1465|-261|127|530|127
F|Twemoji Mozilla
f|TwemojiMozilla.ttf|0|400|400|100|100|normal|TwemojiMozilla|512|448|64|46|0|0|-37|25|132|25
F|Verdana
f|verdana5_33.ttf|0|400|400|100|100|normal|Verdana|2048|2059|430|0|1117|1489|-180|120|679|120
f|verdanab5_33.ttf|0|700|700|100|100|normal|Verdana-Bold|2048|2059|430|0|1123|1489|-139|211|773|211
f|verdanai5_33.ttf|0|400|400|100|100|italic|Verdana-Italic|2048|2059|430|0|1117|1489|-180|120|679|120
f|verdanaz5_33.ttf|0|700|700|100|100|italic|Verdana-BoldItalic|2048|2059|430|0|1123|1489|-139|211|773|211
F|Webdings
f|webdings5_01.ttf|0|400|400|100|100|normal|Webdings|2048|1638|410|0|0|0|-273|100|530|100
F|Wingdings
f|wingding5_01.ttf|0|400|400|100|100|normal|Wingdings-Regular|2048|1841|432|0|0|0|-200|100|800|100
F|Wingdings 2
f|wingdng2.ttf|0|400|400|100|100|normal|Wingdings2|2048|1727|432|0|0|0|-200|100|800|100
F|Wingdings 3
f|wingdng3.ttf|0|400|400|100|100|normal|Wingdings3|2048|1900|432|0|0|0|-200|100|800|100
F|Yu Gothic
f|yugothm1_95.ttc|0|500|500|100|100|normal|YuGothic-Medium|2048|2028|608|645|1117|1581|-205|104|858|94
F|Yu Gothic UI
f|yugothm1_95.ttc|1|400|400|100|100|normal|YuGothicUI-Regular|2048|2210|514|0|1117|1581|-205|104|858|94
"""
