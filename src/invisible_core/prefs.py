"""Translate an internal Profile into the Firefox prefs dict that the
patched Firefox binary expects.

The output dict keys map 1:1 to ``user.js`` preferences. Playwright passes
them via ``firefox_user_prefs=``. The patched binary propagates them to all
content processes over IPC; C++ patches read the ``zoom.stealth.*``
namespace.

The translation is split into:

  * ``_BASELINE`` — global stealth policy (RFP off, WebRTC leaks blocked,
    safebrowsing disabled, debugger detach, …) plus Windows-canonical
    constants that don't depend on the Profile (system colors palette,
    WebGL extensions whitelist, speech voices, navigator identity).
  * ``translate_profile_to_prefs`` — overlays the Profile fields plus the
    user-supplied ``locale`` and ``timezone``.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, Optional

from ._fpforge import Profile
from ._webgl_personas import render_noise_seed, select_persona


# ──────────────────────────────────────────────────────────────────────
#  Navigator identity — locked to Firefox 150 Windows so the binary
#  reports the same UA / platform / oscpu regardless of the host OS.
# ──────────────────────────────────────────────────────────────────────

_NAVIGATOR_OVERRIDES: Dict[str, str] = {
    "general.useragent.override":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) "
        "Gecko/20100101 Firefox/150.0.1",
    "general.platform.override":   "Win32",
    "general.oscpu.override":      "Windows NT 10.0; Win64; x64",
    # general.buildID.override removed 2026-04-28: the previous value
    # "20181001000000" was a 2018 buildID stuck on a 2026-built Firefox 150
    # binary (real BuildID=20260426192818 from application.ini). The 7.5-yr
    # discrepancy is the kind of internal-consistency check Google reCAPTCHA
    # can use to flag bot/spoofed browsers. Deleting the override lets
    # Firefox emit its compiled-in buildID, which auto-tracks the binary.
    # A/B knockout 2026-04-28 (n=30): F2 delete +0.083 RC vs BASE; n=100
    # confirm: +0.021; overnight isolated: +0.155 single-variant. Variable
    # signal, but the underlying data error is unambiguous.
    "general.appversion.override": "5.0 (Windows)",
}


# ──────────────────────────────────────────────────────────────────────
#  System colors — FP Pro probes getComputedStyle(div) with CSS system
#  keywords (ButtonFace, Menu, Highlight, …) and hashes the result into
#  signal s142. On Linux, Firefox resolves these via GTK theme → GTK
#  RGB values diverge from Windows Win32 palette → server-side anomaly
#  even with Windows UA. Pinning the palette to Win10 default closes
#  the gap (see project_css_system_colors.md memory).
# ──────────────────────────────────────────────────────────────────────

_WIN_LIGHT_COLORS: Dict[str, str] = {
    "ui.activeborder":              "#B4B4B4",
    "ui.activecaption":             "#99B4D1",
    "ui.appworkspace":              "#ABABAB",
    "ui.background":                "#000000",
    "ui.buttonface":                "#F0F0F0",
    "ui.buttonhighlight":           "#FFFFFF",
    "ui.buttonshadow":              "#A0A0A0",
    "ui.buttontext":                "#000000",
    "ui.buttonborder":              "#000000",
    "ui.captiontext":               "#000000",
    "ui.graytext":                  "#6D6D6D",
    "ui.highlight":                 "#0078D7",
    "ui.highlighttext":             "#FFFFFF",
    "ui.inactiveborder":            "#F4F7FC",
    "ui.inactivecaption":           "#BFCDDB",
    "ui.inactivecaptiontext":       "#434E54",
    "ui.infobackground":            "#FFFFE1",
    "ui.infotext":                  "#000000",
    "ui.menu":                      "#F9F9FB",
    "ui.menutext":                  "#000000",
    "ui.scrollbar":                 "#C8C8C8",
    "ui.threeddarkshadow":          "#696969",
    "ui.threedface":                "#F0F0F0",
    "ui.threedhighlight":           "#FFFFFF",
    "ui.threedlightshadow":         "#E3E3E3",
    "ui.threedshadow":              "#A0A0A0",
    "ui.window":                    "#FFFFFF",
    "ui.windowframe":               "#646464",
    "ui.windowtext":                "#000000",
    "ui.mark":                      "#FFFF00",
    "ui.marktext":                  "#000000",
    "ui.accentcolor":               "#0078D4",
    "ui.accentcolortext":           "#FFFFFF",
    "ui.selecteditem":              "#0078D7",
    "ui.selecteditemtext":          "#FFFFFF",
    "ui.-moz-hyperlinktext":        "#0066CC",
    "ui.-moz-activehyperlinktext":  "#EE0000",
    "ui.-moz-visitedhyperlinktext": "#551A8B",
}


# ──────────────────────────────────────────────────────────────────────
#  WebGL extensions — Windows ANGLE canonical lists. Empty string =
#  fall back to native Mesa/ANGLE; non-empty = `getSupportedExtensions`
#  returns this list verbatim and `IsSupported()` rejects anything else.
# ──────────────────────────────────────────────────────────────────────

_WEBGL1_EXTENSIONS = ",".join([
    "ANGLE_instanced_arrays",
    "EXT_blend_minmax",
    "EXT_color_buffer_half_float",
    "EXT_float_blend",
    "EXT_frag_depth",
    "EXT_sRGB",
    "EXT_shader_texture_lod",
    "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic",
    "OES_element_index_uint",
    "OES_fbo_render_mipmap",
    "OES_standard_derivatives",
    "OES_texture_float",
    "OES_texture_float_linear",
    "OES_texture_half_float",
    "OES_texture_half_float_linear",
    "OES_vertex_array_object",
    "WEBGL_color_buffer_float",
    "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders",
    "WEBGL_depth_texture",
    "WEBGL_draw_buffers",
    "WEBGL_lose_context",
    "WEBGL_provoking_vertex",
])

_WEBGL2_EXTENSIONS = ",".join([
    "EXT_color_buffer_float",
    "EXT_color_buffer_half_float",
    "EXT_float_blend",
    "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic",
    "OES_draw_buffers_indexed",
    "OES_texture_float_linear",
    "OES_texture_half_float_linear",
    "OVR_multiview2",
    "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders",
    "WEBGL_lose_context",
    "WEBGL_provoking_vertex",
])


# ──────────────────────────────────────────────────────────────────────
#  Speech voices — Windows canonical "Microsoft *" set. Format:
#  "NAME|LANG|DEFAULT|LOCAL,...". Non-empty value drives the
#  speechSynthesis.getVoices() patch; empty disables it.
# ──────────────────────────────────────────────────────────────────────

_WIN_VOICES = ",".join([
    "Microsoft David - English (United States)|en-US|1|1",
    "Microsoft Zira - English (United States)|en-US|0|1",
    "Microsoft Mark - English (United States)|en-US|0|1",
    "Microsoft David Desktop - English (United States)|en-US|0|1",
    "Microsoft Zira Desktop - English (United States)|en-US|0|1",
])


# ──────────────────────────────────────────────────────────────────────
#  Baseline — applied to every session regardless of Profile.
# ──────────────────────────────────────────────────────────────────────

_BASELINE: Dict[str, Any] = {
    # Turn off Firefox's own resistFingerprinting; we do our own via patches.
    "privacy.resistFingerprinting": False,
    "privacy.resistFingerprinting.letterboxing": False,

    # FF150 fingerprintingProtection — enabled by default (or remotely via
    # Mozilla webcompat overrides). FP Pro detects the side-effects and
    # flips `privacy_settings: true`. On FF146 these were all off → False.
    # Force off so FP Pro reports privacy_settings:false (matches FF146).
    "privacy.fingerprintingProtection":                              False,
    "privacy.fingerprintingProtection.pbmode":                       False,
    "privacy.fingerprintingProtection.remoteOverrides.enabled":      False,

    # WebRTC: enabled, looks like a real Firefox behind NAT, no real-IP leak.
    # obfuscate_host_addresses=true → host candidate is `<uuid>.local` mDNS,
    #   exactly like vanilla Firefox (BrowserLeaks "No Leak", Local IP "-").
    #   The mDNS-IPC hang feared on older builds does NOT reproduce on FF150.
    # The proxy-egress srflx is injected by our C++ (srflx swap §17 + fallback
    #   §17.B), fed the egress IP via STEALTHFOX_WEBRTC_PUBLIC_IP from
    #   launcher._build_env (auto-discovered from the proxy).
    # IPv6: media.peerconnection.ice.disableIPv6 is DEAD on FF150 (read by no
    #   ICE-gathering code). The real switch is our zoom.stealth.webrtc.disable_ipv6
    #   (nICEr addrs.cpp filter) + the STEALTHFOX_WEBRTC_DISABLE_IPV6 env.
    "media.peerconnection.enabled":                       True,
    "media.peerconnection.ice.no_host":                   False,
    "media.peerconnection.ice.default_address_only":      False,
    "media.peerconnection.ice.obfuscate_host_addresses":  True,
    "zoom.stealth.webrtc.disable_ipv6":                   True,
    "media.peerconnection.ice.proxy_only":                False,
    "media.peerconnection.ice.relay_only":                False,
    "media.peerconnection.use_document_iceservers":       True,

    # Proxy — route DNS through SOCKS proxies to avoid local DNS leaks.
    "network.proxy.socks_remote_dns":                     True,
    "network.proxy.failover_direct":                      False,

    # TLS ClientHello fingerprint — match stock Firefox byte-for-byte.
    # The Playwright/Juggler Firefox build this binary derives from re-enables
    # cipher 0xC009 (TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA), which retail Firefox
    # 150 does NOT offer. That extra (17th) cipher shifts our JA3/JA4 away from
    # any real Firefox (ja4 t13d1717h2 vs stock t13d1617h2). A ClientHello that
    # matches no real browser is itself a consistency tell. Disabling it makes
    # JA3/JA4/peetprint byte-identical to retail FF150 (verified on tls.peet.ws).
    # Stock Firefox ships without 0xC009 and works on the whole web, so this only
    # improves fingerprint consistency — it cannot break connectivity.
    "security.ssl3.ecdhe_ecdsa_aes_128_sha":              False,

    # Safebrowsing — chatty and fingerprintable.
    "browser.safebrowsing.malware.enabled":               False,
    "browser.safebrowsing.phishing.enabled":              False,
    "browser.safebrowsing.downloads.enabled":             False,
    "browser.safebrowsing.downloads.remote.enabled":      False,

    # First-run / welcome UI noise.
    "browser.startup.page":                               0,
    "browser.shell.checkDefaultBrowser":                  False,
    "browser.aboutwelcome.enabled":                       False,
    "browser.startup.upgradeDialog.enabled":              False,
    "termsofuse.acceptedVersion":                         999,

    # Disable about:newtab auto-load — TopSitesFeed.sys.mjs auto-fetches when
    # a tab opens, triggering a cross-process BC swap that hijacks the first
    # page.goto() (NS_BINDING_ABORTED on creepjs/peet/sannysoft/fppro).
    "browser.newtabpage.enabled":                         False,
    "browser.newtab.preload":                             False,
    "browser.newtabpage.activity-stream.feeds.topsites":  False,
    "browser.newtabpage.activity-stream.feeds.section.topstories": False,
    "browser.newtabpage.activity-stream.enabled":         False,

    # Disable Firefox internal services that hit the network on startup.
    # Through a residential SOCKS5 proxy these compete with the test
    # navigation and trigger NS_BINDING_FAILED (server-side rate-limit /
    # connection drops). Domains observed in MOZ_LOG: push.services,
    # firefox.settings.services, detectportal, ohttp-gateway, location.
    "browser.aboutConfig.showWarning":                    False,
    "network.captive-portal-service.enabled":             False,
    "network.connectivity-service.enabled":               False,
    "dom.push.enabled":                                   False,
    "dom.push.connection.enabled":                        False,
    "geo.enabled":                                        False,
    "geo.provider.network.url":                           "",
    "browser.region.network.url":                         "",
    "browser.region.update.enabled":                      False,
    "services.settings.server":                           "",
    "browser.search.geoSpecificDefaults":                 False,
    "browser.contentblocking.report.lockwise.enabled":    False,
    "browser.contentblocking.report.monitor.enabled":     False,
    "extensions.systemAddon.update.enabled":              False,
    "extensions.update.enabled":                          False,
    "extensions.getAddons.cache.enabled":                 False,
    "browser.discovery.enabled":                          False,
    "browser.ping-centre.telemetry":                      False,
    "app.normandy.enabled":                               False,
    "dom.private-attribution.submission.enabled":         False,
    "browser.translations.enable":                        False,
    "browser.search.update":                              False,

    # HTTP/3 + speculative + Alt-Svc disabled. SOCKS5 proxy doesn't
    # support UDP ASSOCIATE so HTTP/3 fails. Speculative connections
    # under load cause early channel cancel (NS_BINDING_FAILED).
    "network.http.http3.enable":                          False,
    "network.http.http3.enabled":                         False,
    "network.http.altsvc.enabled":                        False,
    "network.http.altsvc.oe":                             False,
    "network.http.speculative-parallel-limit":            0,
    "network.predictor.enabled":                          False,
    "network.dns.disablePrefetch":                        True,
    "network.dns.disablePrefetchFromHTTPS":               True,
    "network.dns.echconfig.enabled":                      False,
    "network.dns.use_https_rr_as_altsvc":                 False,

    # === Fission / site-isolation disabled (FF146 Playwright parity) ===
    # Force a single content-process model. Three knobs are required in FF150:
    # upstream Playwright Firefox (FF146-based) only needed fission.autostart=False
    # because FF146's default isolation strategy was looser. FF150 ships with
    # fission.webContentIsolationStrategy=1 (IsolateEverything) which still
    # site-isolates cross-origin iframes into separate `webIsolated` content
    # processes EVEN WHEN fission.autostart is False. From the parent process's
    # point of view, those iframes get a Juggler Frame placeholder with no
    # docShell, no URL, and an execution context that wraps the wrong global,
    # so frame.evaluate() fails with cross-origin SOP errors and
    # element_handle.content_frame() returns None.
    #
    # Pinning the strategy to 0 keeps every cross-origin web iframe in the
    # parent's content process, where the Juggler code paths from the FF146
    # era expect them. processCount.webIsolated=1 is kept as belt-and-suspenders
    # in case some path still classifies an origin as webIsolated despite the
    # strategy change. It costs nothing to leave.
    #
    # See issue #20 + tests/test_cross_origin_iframe.py for the regression
    # sentinel that catches a future A/B flipping these back.
    "fission.autostart":                                  False,
    "fission.autostart.session":                          False,
    "fission.webContentIsolationStrategy":                0,  # IsolateNothing
    "dom.ipc.processCount.webIsolated":                   1,


    # Telemetry & data reporting.
    "datareporting.healthreport.uploadEnabled":           False,
    "datareporting.policy.dataSubmissionEnabled":         False,
    "toolkit.telemetry.enabled":                          False,
    "toolkit.telemetry.unified":                          False,
    "app.shield.optoutstudies.enabled":                   False,

    # Update channels.
    "app.update.enabled":                                 False,
    "app.update.auto":                                    False,

    # Speech synth: enabled (the C++ patch fabricates voices from the
    # comma list above) regardless of the host OS.
    "media.webspeech.synth.enabled":                      True,
    "zoom.stealth.voices.list":                           _WIN_VOICES,

    # WebGL extensions whitelist — non-empty pre-empts native enumeration.
    "zoom.stealth.webgl.extensions":                      _WEBGL1_EXTENSIONS,
    "zoom.stealth.webgl2.extensions":                     _WEBGL2_EXTENSIONS,
    # WebGL numeric param overrides — kept empty (A/B test 2026-04-22 showed
    # mismatches between the values we shipped and ANGLE's real envelope
    # raised FP Pro's ML tampering score). Slot kept for future experiments.
    "zoom.stealth.webgl.int_params":                      "",
    "zoom.stealth.webgl.int2_params":                     "",
    "zoom.stealth.webgl.shader_precisions":               "",
    "zoom.stealth.webgl.float_params":                    "",

    # DevTools anti-detection.
    "zoom.stealth.debugger.force_detach":                 True,

    # Canvas substitution (Option B for canvas) — replace pixels with hash(seed,idx),
    # uniform-skip (red-box exact, masking-safe) + full overwrite. Makes the canvas
    # render a pure function of (seed) = HOST-INDEPENDENT (kills the DWrite-vs-FreeType
    # text-raster leak: Canvas Hash + Font hash were the residual Win!=Linux signals).
    # ON by default (paired with webgl.substitute_pixels).
    "zoom.stealth.canvas.substitute_pixels":              True,

    # WebGL substitution (Option B) — replace readback/snapshot RGB with
    # hash(seed,idx), endpoint-preserving. Makes the WebGL render hash a pure
    # function of (seed, dims) = HOST-INDEPENDENT, so no per-host hw_seed
    # calibration is needed (the gamma path was per-host: NVIDIA/Arc-on-Win clean
    # seeds went dirty on the Linux GL backend). ON by default.
    "zoom.stealth.webgl.substitute_pixels":              True,

    # WebGPU presence consistency. Firefox enables dom.webgpu.enabled by default on
    # Windows/Mac-ARM but NOT on Linux/Mac-x64. We ALWAYS claim Windows, so force it ON
    # on every host: a Windows FF MUST expose navigator.gpu (object); a Linux host leaving
    # it undefined while the UA says Windows is an inconsistency tell (RE 2026-06-22:
    # has_gpu was object on Win, undefined on WSL). adapter.info is empty (FF privacy
    # default) so no GPU-name leak; requestAdapter may be null on a GPU-less host, which
    # is itself plausible for a real Windows machine.
    "dom.webgpu.enabled":                                 True,

    # Audio fingerprint noise OFF. RE 2026-06-22: the per-session OfflineAudioContext
    # noise (gated by hw_seed) was THE dominant driver of FP Pro tampering_ml on Windows
    # — b005 Win dropped 0.4349 -> 0.0564 with audio noise alone disabled (canvas_text/
    # emoji unchanged, so they were a red-herring). The audio value is already host-indep
    # AND identical to a real FF's canonical OfflineAudioContext sum, so a fixed (un-noised)
    # audio is NOT a linking signal (every real FF has the same value) — removing the noise
    # matches real Firefox and clears the tampering flag.
    "zoom.stealth.audio.fp_noise":                        False,

    # Navigator identity (locked to Windows Firefox 150).
    **_NAVIGATOR_OVERRIDES,
}


# ──────────────────────────────────────────────────────────────────────
#  Linux-only Xvfb workarounds — the Linux Firefox build under Xvfb
#  cannot run WebRender (`ConnectToCompositor` retries forever). We
#  disable WebRender + force WebGL through the GL software path so
#  webgl_basics / webgl_extensions still report.
# ──────────────────────────────────────────────────────────────────────

_LINUX_XVFB_WORKAROUNDS: Dict[str, Any] = {
    "gfx.webrender.all":                       False,
    "gfx.webrender.force-disabled":            True,
    "webgl.force-enabled":                     True,
    # webgl.software-rendering-enabled / webgl.force-layers-readback removed in FF150.
}

# ──────────────────────────────────────────────────────────────────────
#  Windows virtual-desktop workarounds — when headless=True on Windows,
#  Firefox runs on a CreateDesktop virtual desktop. The hardware GPU is
#  inaccessible from the virtual desktop, so the GPU process crashes when
#  it tries to initialize the D3D11 compositor with hardware acceleration.
#
#  Approach: force D3D11 WARP (CPU software renderer) for the GPU process.
#  layers.d3d11.force-warp=True → compositor uses WARP → GPU process stable.
#  webgl.angle.force-warp=True  → ANGLE uses WARP → WebGL context creates.
#
#  CRITICAL: do NOT set webgl.out-of-process=False. That moves WebGL from the
#  GPU process to the sandboxed content process. The content process sandbox
#  blocks D3D11 access entirely → ANGLE crashes the content process →
#  canvas.getContext('webgl') throws instead of returning null.
#
#  gfx.canvas.accelerated=False: default is true, disabling avoids any
#  hardware GPU dependency for 2D canvas in the content process.
# ──────────────────────────────────────────────────────────────────────

_WIN_VIRT_DESKTOP_WORKAROUNDS: Dict[str, Any] = {
    # FF150 regression vs FF146 on CreateDesktop alt-desktop:
    # The GPU process sandbox (level=1, default since FF110) tries to parent
    # its compositor window to the parent process's window. Our worker spawns
    # Firefox on a CreateDesktop-created alt desktop — parent and GPU process
    # do not share the same desktop/HWND namespace, so window parenting fails
    # silently. WebRender falls back to "Software D3D11" and OOP-WebGL never
    # publishes a hardware ANGLE renderer → getContext('webgl') returns a
    # context but extensions/parameters/$hash all come back null/empty (FF146
    # had a more permissive sandbox, so the same setup worked there).
    # Bugzilla refs: 1798091, 1524591, 1229829. Lowering the GPU sandbox to 0
    # restores hardware compositor + functional WebGL on alt desktops.
    "security.sandbox.gpu.level": 0,
    # Same root cause as above, content process side. Wrapper repo issue #18
    # (tab crash on cross-process navigation under headless=True). Sandbox
    # content level > 4 puts content processes on the sandbox's own
    # kAlternateWinstation (see security/sandbox/win/src/sandboxbroker/
    # sandboxBroker.cpp line 1113-1114:
    # `if (aSandboxLevel > 4) config->SetDesktop(kAlternateWinstation)`).
    # Combined with our CreateDesktop alt-desktop, that puts browser process
    # and content processes on DIFFERENT desktops. Cross-process navigation
    # then fails window parenting between parent and child, the content
    # process exits cleanly (exitCode=0, signal=null) and Playwright fires
    # page.on('crash') ~10s after page load. Lowering content sandbox to 4
    # keeps content processes on the same desktop as the browser process,
    # which is what we want here (still tight enough — level 4 blocks
    # file/registry write, network calls, hardware access).
    "security.sandbox.content.level": 4,
}


# ──────────────────────────────────────────────────────────────────────
#  Public helpers
# ──────────────────────────────────────────────────────────────────────

def _accept_language(locale: str) -> str:
    # "<locale>, <base>" — the desktop-default shape (e.g. "en-US, en"). Firefox expands it
    # to navigator.languages=["en-US","en"] AND (via the patched binary) the q-valued header
    # "en-US,en;q=0.5". The patched nsHttpHandler (STEALTHFOX, RE 2026-06-23) builds the
    # Accept-Language header from THIS pref even when juggler sets a per-context locale
    # override, so header and navigator.languages stay consistent 2/2 — the most authentic
    # (real desktop) form. Supersedes the 2026-06-22 single-tag workaround.
    lang = locale.replace("_", "-")
    base = lang.split("-")[0]
    return f"{lang}, {base}" if base != lang else lang


def translate_profile_to_prefs(
    profile: Profile,
    *,
    locale: str = "en-US",
    timezone: str = "",
    extra_prefs: Optional[Dict[str, Any]] = None,
    virtual_display: bool = False,
) -> Dict[str, Any]:
    """Return a complete prefs dict ready for Playwright's firefox_user_prefs=.

    Args:
        profile:         Bayesian-sampled fingerprint (from ``generate_profile``).
        locale:          BCP-47 tag, e.g. ``"en-US"``.
        timezone:        IANA timezone name, e.g. ``"America/New_York"``.
        extra_prefs:     Optional overlay applied LAST.
        virtual_display: When True on Windows, apply GPU-disabling workarounds
                         to prevent the GPU process from crashing on virtual
                         desktops that have no D3D11 backend.
    """
    prefs: Dict[str, Any] = dict(_BASELINE)

    # GPU / WebGL renderer/vendor.
    # On Linux we spoof to a Windows ANGLE renderer string (profile.gpu.renderer)
    # so cross-platform sessions report a consistent Windows GPU identity.
    # On Windows/mac, spoofing a renderer string ALONE is unsafe — the ~81
    # getParameter values stay real, so a name↔params hash mismatch FP Pro flags
    # (setting GTX 980 over real Arc A750 params scored ~0.70). Instead we apply a
    # VALIDATED PERSONA (see _webgl_personas): a {renderer, vendor} whose params are
    # the shared ANGLE D3D11 caps (vendor-independent — identical on any host, per the
    # ANGLE source) and whose extension list is FORCED below. That is a coherent fake
    # GPU that passes FP Pro host-independently (the host's real GPU never leaks). If no
    # validated persona exists for the sampled gpu_class yet, fall back to the host-real
    # renderer (empty → native ANGLE; SanitizeRenderer at ClientWebGLContext.cpp:2592).
    # Apply the camoufox-derived real-Firefox GPU persona on EVERY host (Win/Linux/Mac).
    # We must ALWAYS look Windows (rule), and the WebGL override is platform-independent:
    # SanitizeRenderer (ClientWebGLContext.cpp) is pure string regex, and the param/extension
    # overrides are pref-driven, so the C++ presents the SAME Windows ANGLE GPU regardless of
    # the host's real GL backend (it never consults it when the pref is set). This is why a
    # Windows GPU shows correctly even on a Linux/Mesa host (no more "Generic Renderer").
    _persona = select_persona(profile.seed)
    if _persona:
        # Apply the FULL coherent WebGL override (renderer + vendor + webgl1/webgl2 extensions
        # + ~100 getParameter values + shader-precision formats). Setting ALL of them — not just
        # the renderer string — keeps renderer<->params coherent (FP Pro cross-checks them); a
        # string-only spoof over the host's real params is the old ~0.85 mismatch.
        for _k, _v in _persona["prefs"].items():
            if _k == "zoom.stealth.webgl2.enabled":
                prefs["webgl.enable-webgl2"] = bool(_v)
            else:
                prefs[_k] = _v
    else:
        prefs["zoom.stealth.webgl.renderer"] = ""
        prefs["zoom.stealth.webgl.vendor"]   = ""
    # Canvas-noise mask is calibrated to the REAL host GPU's rendering variance — the canvas is
    # drawn by real hardware, NOT the persona's claimed GPU, so it must NOT follow the persona
    # (a non-Intel persona on an Intel host would over-noise). Dev/deployment host is Intel.
    _renderer_lo = "intel"

    # MSAA: on Windows, pin to 4 (Firefox default for ANGLE) so gl.SAMPLES is
    # constant across all sessions. Different MSAA values cause different CN-set
    # parameters hashes even with the same renderer → detectable variation.
    # Vanilla Intel Arc A750 parameters hash (66544db8) verified at msaa=4.
    _msaa = profile.webgl.msaa_samples if sys.platform.startswith("linux") else 4
    prefs["zoom.stealth.webgl.msaa"]        = _msaa
    prefs["webgl.msaa-samples"]             = _msaa
    prefs["webgl.msaa-force"]               = _msaa > 0

    # Canvas pixel-noise density per vendor. Intel has lower natural
    # rendering variance than NVIDIA/AMD, so the default 1/8 noise rate
    # over-amplifies the FP Pro tampering ML signal. Drop to 1/16 for Intel
    # to keep tampering_ml below the detection threshold while still
    # breaking the canvas geometry hash.
    if "intel" in _renderer_lo:
        prefs["zoom.stealth.canvas.noise_skip_mask"] = 15  # 1/16, ~6.25%
    else:
        prefs["zoom.stealth.canvas.noise_skip_mask"] = 7   # 1/8,  ~12.5%

    # Screen
    prefs["zoom.stealth.screen.width"]        = profile.screen.width
    prefs["zoom.stealth.screen.height"]       = profile.screen.height
    prefs["zoom.stealth.screen.avail_width"]  = profile.screen.avail_width
    prefs["zoom.stealth.screen.avail_height"] = profile.screen.avail_height
    prefs["zoom.stealth.screen.dpr"]          = profile.screen.dpr
    prefs["layout.css.devPixelsPerPx"]        = str(profile.screen.dpr)

    # Hardware — coherent with the sampled gpu_class by construction (the forge
    # draws hw_concurrency conditioned on the GPU class).
    prefs["zoom.stealth.hw_concurrency"]      = profile.hardware.concurrency
    prefs["zoom.stealth.storage.quota_mb"]    = profile.hardware.storage_quota_mb

    # Audio
    prefs["zoom.stealth.audio.sample_rate"]       = profile.audio.sample_rate
    prefs["zoom.stealth.audio.output_latency_ms"] = profile.audio.output_latency_ms
    prefs["zoom.stealth.audio.max_channel_count"] = profile.audio.max_channel_count

    # Codec
    prefs["media.av1.enabled"]                = profile.codec.av1_enabled
    prefs["media.encoder.webm.enabled"]       = profile.codec.webm_encoder_enabled
    prefs["media.mediasource.webm.enabled"]   = profile.codec.mediasource_webm
    prefs["media.mediasource.mp4.enabled"]    = profile.codec.mediasource_mp4

    # Fonts — real bundled fonts (no collapse). The binary ships the real
    # Windows font files in <GRE>/fonts and loads them via MOZ_BUNDLED_FONTS, so
    # glyphs are genuine on every host. We expose this profile's family set via
    # the per-profile fontlist, which the binary applies to the native system
    # font allow-list AT CONSTRUCTION (no runtime rebuild → no scan stall), and
    # force the system-ui generic to Segoe UI. profile.fonts is the _fpforge
    # sample (core always + a conditioned optional subset). Per-profile metric
    # uniqueness comes from the shared fpp.hw_seed jitter in the HarfBuzz shaper
    # (set with the other fpp prefs), not from fabricated widths.
    prefs["zoom.stealth.font.fontlist"] = ",".join(profile.fonts)
    prefs["zoom.stealth.font.system_ui"] = "Segoe UI"

    # Activate the bundled real-Windows fonts (MOZ_BUNDLED_FONTS / <GRE>/fonts).
    prefs["gfx.bundled-fonts.activate"] = 1
    # Point the CSS generics at Windows defaults. On a Windows HOST Firefox's
    # built-in name-lists already resolve to these (and they're in the fontlist
    # so they survive the allow-list); but on a non-Windows host (Linux/Mac) the
    # built-in defaults name host fonts (DejaVu/Liberation/…) which the allow-list
    # hides — so the generics would collapse. Setting them explicitly keeps the
    # generics resolving to the bundled Windows families on EVERY host (system-ui
    # is forced to Segoe UI by the C++ hook above, so it is not listed here).
    prefs["font.name-list.serif.x-western"] = "Times New Roman"
    prefs["font.name-list.sans-serif.x-western"] = "Arial"
    prefs["font.name-list.monospace.x-western"] = "Consolas"
    prefs["font.name-list.sans-serif.ja"] = "Yu Gothic UI"
    prefs["font.name-list.serif.ja"] = "Yu Gothic UI"
    prefs["font.name-list.sans-serif.ko"] = "Malgun Gothic"
    prefs["font.name-list.serif.ko"] = "Malgun Gothic"
    prefs["font.name-list.sans-serif.zh-CN"] = "Microsoft YaHei UI"
    prefs["font.name-list.sans-serif.zh-TW"] = "Microsoft JhengHei UI"
    prefs["font.name-list.sans-serif.zh-HK"] = "Microsoft JhengHei UI"

    # UI / dark mode + Windows colors palette (only when light theme).
    prefs["ui.systemUsesDarkTheme"] = int(profile.dark_theme)
    if not profile.dark_theme:
        prefs.update(_WIN_LIGHT_COLORS)

    # Locale prefs.
    locale = locale or "en-US"
    lang = locale.replace("_", "-")
    prefs["intl.accept_languages"]     = _accept_language(locale)
    prefs["general.useragent.locale"]  = lang
    prefs["intl.locale.requested"]     = lang
    prefs["privacy.spoof_english"]     = 0
    # juggler.locale.override seeds the BrowsingContext LanguageOverride FIELD in
    # the parent process (BrowsingContext::Attach), whose DidSet drives BOTH
    # navigator.languages (the full list) AND the realm Intl default locale (the
    # primary tag it extracts) — so Intl.DateTimeFormat / NumberFormat /
    # toLocaleString follow the locale, not just the Accept-Language header. Seed
    # it with the full Accept-Language list so navigator.languages stays the
    # desktop-default 2 elements (["fr-FR","fr"]); the C++ DidSet takes "fr-FR"
    # for Intl. Mirrors juggler.timezone.override; the SOLE source of truth.
    prefs["juggler.locale.override"]   = _accept_language(locale)

    if timezone:
        # juggler.timezone.override is the SOLE source of truth read by the C++
        # timezone chain (BrowsingContext::Attach/DidSet, ContentChild). The old
        # zoom.stealth.timezone pref was declared in the yaml but read by NO
        # code — dropped here on 2026-06-10 (see 20-our-patches.md §8).
        prefs["juggler.timezone.override"] = timezone

    # Cross-process seed (canvas noise + DWrite gamma share this). Only
    # zoom.stealth.fpp.hw_seed is read by the C++; the old zoom.stealth.seed
    # alias was never declared in the yaml and read by nothing — dropped
    # 2026-06-10. The render-noise seed is DECOUPLED from the identity seed and
    # drawn from a calibrated CLEAN pool: the canvas/WebGL render HASH it drives
    # is the dominant FP Pro tampering_ml signal, and some hw_seeds yield a
    # "suspicious" render hash. render_noise_seed() maps to the clean pool while
    # keeping per-seed determinism + diversity. See _webgl_personas.
    prefs["zoom.stealth.fpp.hw_seed"] = render_noise_seed(profile.seed)

    # Synthetic host ICE candidate — injected by C++ when addr_ct==0 (SOCKS5
    # proxy suppresses all local addresses so Firefox can't gather host cands).
    # LAN IP is seed-derived so it's consistent per session and looks like a
    # real home router assignment (192.168.x.x range).
    _s = profile.seed
    _lan_ip = f"192.168.{(_s >> 8) % 254 + 1}.{_s % 254 + 1}"
    prefs["zoom.stealth.webrtc.host_ip"] = _lan_ip

    # Windows/mac extension list:
    #  - persona active → the coherent webgl1/webgl2 extension lists (in the GPU's real
    #    native order) were ALREADY applied above from the GPU pool's `prefs`, alongside the
    #    matching renderer + params + shader-precisions. Nothing to do here.
    #  - no persona → clear so the host-real renderer reports its native extension set
    #    (matches real vanilla captures for that host's GPU).
    if not sys.platform.startswith("linux") and not _persona:
        prefs["zoom.stealth.webgl.extensions"]  = ""
        prefs["zoom.stealth.webgl2.extensions"] = ""

    # Linux Xvfb workarounds (no-op on Windows).
    if sys.platform.startswith("linux"):
        for k, v in _LINUX_XVFB_WORKAROUNDS.items():
            prefs.setdefault(k, v)

    # Windows virtual-desktop workarounds (headless=True on Windows).
    if virtual_display and sys.platform == "win32":
        for k, v in _WIN_VIRT_DESKTOP_WORKAROUNDS.items():
            prefs.setdefault(k, v)

    # Caller overlay LAST so users can override anything we set. A value of
    # None is treated as a sentinel meaning "delete this pref entirely from
    # the final dict" — useful for A/B harnesses that need to test what
    # happens when an override is unset (vs set to empty string, which for
    # some prefs like general.useragent.override means literally empty UA).
    if extra_prefs:
        for k, v in extra_prefs.items():
            if v is None:
                prefs.pop(k, None)
            else:
                prefs[k] = v

    return prefs
