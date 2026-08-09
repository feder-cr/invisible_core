"""Translate an internal Profile into the Firefox prefs dict that the
patched Firefox binary expects.

The output dict keys map 1:1 to ``user.js`` preferences. Playwright passes
them via ``firefox_user_prefs=``. The patched binary propagates them to all
content processes over IPC; C++ patches read the ``zoom.stealth.*``
namespace.

The translation is split into:

  * ``_BASELINE`` - global stealth policy (RFP off, WebRTC leaks blocked,
    safebrowsing disabled, debugger detach, …) plus Windows-canonical
    constants that don't depend on the Profile (system colors palette,
    WebGL extensions whitelist, speech voices, navigator identity).
  * ``translate_profile_to_prefs`` - overlays the Profile fields plus the
    user-supplied ``locale`` and ``timezone``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional

from .constants import OSCPU_OVERRIDE, PLATFORM_OVERRIDE, USER_AGENT
from ._fpforge import Profile
from ._webgl_personas import render_noise_seed, select_persona
from ._headless import cloak_prefs
from ._proxy import configure_proxy


# ──────────────────────────────────────────────────────────────────────
#  Navigator identity - locked to Firefox 150 Windows so the binary
#  reports the same UA / platform / oscpu regardless of the host OS.
# ──────────────────────────────────────────────────────────────────────

_NAVIGATOR_OVERRIDES: Dict[str, str] = {
    # Derived from FIREFOX_UPSTREAM_VERSION (see constants.USER_AGENT): the
    # previous literal said "Firefox/150.0.1", a patch-versioned form that no
    # real Firefox emits.
    "general.useragent.override": USER_AGENT,
    "general.platform.override":   PLATFORM_OVERRIDE,
    "general.oscpu.override":      OSCPU_OVERRIDE,
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
#  System colors - FP Pro probes getComputedStyle(div) with CSS system
#  keywords (ButtonFace, Menu, Highlight, …) and hashes the result into
#  signal s142. On Linux, Firefox resolves these via GTK theme → GTK
#  RGB values diverge from Windows Win32 palette → server-side anomaly
#  even with Windows UA. Pinning the palette to Win10 default closes
#  the gap (see project_css_system_colors.md memory).
# ──────────────────────────────────────────────────────────────────────

_WIN_LIGHT_COLORS: Dict[str, str] = {
    # Measured against stock Firefox 151 on Windows, 2026-08-09, not chosen.
    # A page resolves every one of these through getComputedStyle, and our Linux
    # build answered GTK's values for all seven: menuhover was rgb(53,132,228)
    # against rgba(0,0,0,0.118), oddtreerow was transparent against white,
    # dialogtext was rgb(46,52,54) against black. Eight colours in one call is a
    # desktop-environment readout, which is a stronger tell than any single one.
    #
    # The names are NOT uniform and that is upstream's doing, not a typo here:
    # nsXPLookAndFeel.cpp spells cellhighlight with a hyphen and menuhover,
    # menuhovertext, menubarhovertext and oddtreerow with an UNDERSCORE. Naming
    # all seven with hyphens left exactly those four unset, and the measurement
    # said so - Linux went from 8 divergences to 4, and the 4 were the 4.
    "ui.-moz-cellhighlight":           "#CECECE",
    # F0F0F0 and not FFFFFF, and the difference is a lesson rather than a
    # typo. It was declared FFFFFF from a measurement taken against the retail
    # in C:/tmp/ff151, a directory that holds 153.0.3 - the NAME was the only
    # thing saying 151. Firefox 153 answers white here and 151 answers this,
    # and 151 is the version our User-Agent claims. It is the only one of the
    # 35 colours that moved between the two majors, which is exactly why a
    # wrong judge is hard to catch: it agrees with the right one almost
    # everywhere.
    "ui.-moz-dialog":                  "#F0F0F0",
    "ui.-moz-dialogtext":              "#000000",
    "ui.-moz_menubarhovertext":        "#15141A",
    "ui.-moz_menuhover":               "#0000001E",
    "ui.-moz_menuhovertext":           "#15141A",
    "ui.-moz_oddtreerow":              "#FFFFFF",
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
    "ui.accentcolor":                  "#0060DF",
    "ui.accentcolortext":           "#FFFFFF",
    "ui.selecteditem":              "#0078D7",
    "ui.selecteditemtext":          "#FFFFFF",
    "ui.-moz-hyperlinktext":        "#0066CC",
    "ui.-moz-activehyperlinktext":  "#EE0000",
    "ui.-moz-visitedhyperlinktext": "#551A8B",
}


# ──────────────────────────────────────────────────────────────────────
#  WebGL extensions - Windows ANGLE canonical lists. Empty string =
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
#  Speech voices - Windows canonical "Microsoft *" set. Format:
#  "NAME|LANG|DEFAULT|LOCAL,...". Non-empty value drives the
#  speechSynthesis.getVoices() patch; empty disables it.
# ──────────────────────────────────────────────────────────────────────

# _WIN_VOICES moved to HardwareProfile.voices on 2026-08-09, after verifying
# the two copies were byte-identical. It was a constant in _BASELINE, so the
# voice list - a fingerprint surface, and one that is wrong for every non-en
# locale - could not be pinned, inspected or overridden.


# ──────────────────────────────────────────────────────────────────────
#  Declared media answers
#
#  WHERE THE HOST-DEPENDENCE ACTUALLY IS, measured 2026-08-08 over four arms
#  (retail Windows headed, retail Windows headless, our Windows, our Linux),
#  45 media types and 12 decodingInfo configurations:
#
#      our Windows vs retail Windows    45/45 and 8/8 identical
#      our Linux   vs retail Windows    12 canPlayType rows differ - every
#                                       avc1 with a supported profile, plus
#                                       hev1 and hvc1
#
#  So exactly one question is host-dependent: does this build have an H.264 or
#  HEVC decoder. The Linux build ships ffvpx with H.264 compiled out and reaches
#  for the user's libavcodec, so two Linux users differ from EACH OTHER, which
#  is worse than differing from Windows.
#
#  WHY THIS IS NOT A TABLE OF TYPE STRINGS ANY MORE. It was until this date, and
#  it held 14 whole strings matched against `canPlayType`'s argument. A list of
#  strings can only hold the strings somebody has seen: measured that day it
#  covered 3 of the 12 rows that actually differed, and the space it was trying
#  to enumerate has no end (profile x constraint x level x container x whichever
#  audio codec travels along).
#
#  One layer down the key space is finite and tiny. The binary now consults this
#  in PDMFactory::Supports, which is asked about a TRACK mime type - video/avc,
#  video/hevc, a dozen names Gecko defines itself. Declaring those covers every
#  string that can be built out of them, and everything above (container rules,
#  the codec-list AND, the profile and level windows) stays Gecko's own
#  host-independent logic.
#
#  AND THE CORRECTION THAT MADE THAT POSSIBLE: the rule that baseline, main,
#  extended and high answer while high10, high422 and high444 do not, and that
#  the level must sit in [1, 6.2], is IsAllowedH264Codec in VideoUtils.cpp - a
#  COMPILED GECKO CONSTANT, applied identically on every platform. Measuring it
#  on retail Windows and reading it as a Windows fact produced a 112-vs-84 split
#  that looked like a rule worth declaring and was Gecko's own arithmetic.
#  Verified at the edges: avc1.644000 (level 0) and avc1.64403F (level 6.3)
#  answer empty on retail, exactly as that function says they must.
#
#  ONLY THE TWO DIVERGENT NAMES ARE DECLARED. Every audio codec, VP8, VP9 and
#  AV1 are bundled with the build, agree cross-OS in the measurement, and so
#  carry nothing to declare away. A pref entry that cannot move a measurement is
#  not a safety margin, it is an untested code path.
#
#  THE COST, recorded so nobody re-derives it as a surprise: declaring support
#  for a codec the Linux build cannot decode means a site picks H.264 and the
#  video does not play, where today it falls back to WebM and plays. Fidelity to
#  what a Windows Firefox REPORTS is bought with a broken playback. Shipping the
#  decoder is the way out; changing this answer back is not.
#
#  NEWLINE-separated: a comma is what a multi-codec type carries inside its own
#  value, so a comma separator silently swallowed every combined entry.
# ──────────────────────────────────────────────────────────────────────
_WIN_DECODE_SUPPORT = chr(10).join([
    "video/avc|swhw",
    "video/hevc|swhw",
])

# ──────────────────────────────────────────────────────────────────────
#  Declared mediaCapabilities.decodingInfo
#
#  A separate table because powerEfficient is NOT a property of the track mime
#  type: on retail Windows, vp09.00.10.08 in video/mp4 reports powerEfficient
#  true and a bare vp9 in video/webm reports false, and both build a video/vp9
#  track. Keyed by container plus the codec family (the part before the first
#  dot), which keeps the key space finite - eight families, not an unbounded
#  number of strings built from them.
#
#  MEASURED AGAINST RETAIL RUN HEADED, and that is load-bearing. powerEfficient
#  answers differently under -headless on the same machine: avc1, av01 and vp09
#  all report false headless and true headed, because the hardware check has no
#  compositor to ask. The judge is the Firefox a user runs, not the one our
#  harness runs, so pinning the headless numbers would have pinned three wrong
#  values.
#
#  Three digits: supported, smooth, powerEfficient.
# ──────────────────────────────────────────────────────────────────────
_WIN_DECODING_INFO = chr(10).join([
    "video/mp4|avc1|111",
    "video/mp4|avc3|111",
    "video/mp4|hev1|111",
    "video/mp4|hvc1|111",
    "video/mp4|av01|111",
    "video/mp4|vp09|111",
    "video/webm|av01|111",
    "video/webm|vp09|111",
    "video/webm|vp8|110",
    "video/webm|vp9|110",
])

# ──────────────────────────────────────────────────────────────────────
#  Windows system-font surface
#
#  These 26 also live in the binary's all.js, and the duplication is
#  deliberate: the binary must stay correct when launched WITHOUT this
#  package (invisible_firefox direct-launch, a manual run), because the
#  fallback is not a subtle drift - Gecko's own defaults name "Sans" at
#  13.3333px on Linux, a family that does not exist on Windows, and that
#  is what drove FpJS Pro tampering=True on 2026-08-07. all.js is the
#  compiled floor; this is the source of truth that can move without a
#  Firefox rebuild.
#
#  THE SIZES ARE STRINGS ON PURPOSE. nsXPLookAndFeel reads them through
#  Preferences::GetFloat, which in Gecko parses float prefs from their
#  STRING form; declared as a bare int the pref does not fail, it is
#  silently ignored and the UI falls back to StyleFONT_MEDIUM_PX (16px).
#  The monospace sizes below are genuine ints - different pref type, and
#  `_pref_literal` serialises the two differently.
# ──────────────────────────────────────────────────────────────────────

#: The CSS system-font keywords Gecko resolves through ui.font.*, plus the
#: four -moz- widget fonts. getComputedStyle on `font: menu` reads these.
_UI_FONT_ELEMENTS = (
    "caption", "icon", "menu", "message-box", "small-caption", "status-bar",
    "-moz-pull-down-menu", "-moz-button", "-moz-list", "-moz-field",
)

#: The language groups whose monospace default Firefox sets differently on
#: Windows (13) and in its Unix block (12). The gap is directly readable: it is
#: the width FingerprintJS's fontPreferences probe measures for the monospace
#: generic at the default size, with no font-size set.
_MONOSPACE_LANG_GROUPS = (
    "ar", "el", "he", "x-cyrillic", "x-unicode", "x-western",
)


# ──────────────────────────────────────────────────────────────────────
#  Baseline - applied to every session regardless of Profile.
# ──────────────────────────────────────────────────────────────────────

_BASELINE: Dict[str, Any] = {
    # Turn off Firefox's own resistFingerprinting; we do our own via patches.
    "privacy.resistFingerprinting": False,
    "privacy.resistFingerprinting.letterboxing": False,

    # FF150 fingerprintingProtection - enabled by default (or remotely via
    # Mozilla webcompat overrides). FP Pro detects the side-effects and
    # flips `privacy_settings: true`. On FF146 these were all off → False.
    # Force off so FP Pro reports privacy_settings:false (matches FF146).
    "privacy.fingerprintingProtection":                              False,
    "privacy.fingerprintingProtection.pbmode":                       False,
    "privacy.fingerprintingProtection.remoteOverrides.enabled":      False,

    # Master toggle for Firefox's baseline fingerprinting protection. FF151
    # graduated it from nightly-only to all channels; its 3 desktop targets are
    # EfficientCanvasRandomization + ScreenAvailToResolution + MaxTouchPointsCollapse.
    # EfficientCanvasRandomization re-noises the 2D canvas at the image-encoder
    # stage, DOWNSTREAM of our seeded substitution, with a per-session key →
    # canvas hash drifts every session (fppro_consistency FAIL on FF151).
    # We do our own seeded canvas/screen/touch via patches, so turn Firefox's
    # baseline off (same rationale as the two prefs above). FF150 release had
    # these off already (nightly-only), so this RESTORES shipped-FF150 behavior.
    # Verified A/B: canvas becomes seed-deterministic (canvas_geo host-independent;
    # canvas_text stays host-dependent via glyph rasterization on FF150 AND FF151,
    # a pre-existing property, not a rebase regression). e2e/screen/flags unchanged.
    "privacy.baselineFingerprintingProtection":                      False,

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

    # Proxy - route DNS through SOCKS proxies to avoid local DNS leaks.
    "network.proxy.socks_remote_dns":                     True,
    "network.proxy.failover_direct":                      False,

    # TLS ClientHello fingerprint - match stock Firefox byte-for-byte.
    # The Playwright/Juggler Firefox build this binary derives from re-enables
    # cipher 0xC009 (TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA), which retail Firefox
    # 150 does NOT offer. That extra (17th) cipher shifts our JA3/JA4 away from
    # any real Firefox (ja4 t13d1717h2 vs stock t13d1617h2). A ClientHello that
    # matches no real browser is itself a consistency tell. Disabling it makes
    # JA3/JA4/peetprint byte-identical to retail FF150 (verified on tls.peet.ws).
    # Stock Firefox ships without 0xC009 and works on the whole web, so this only
    # improves fingerprint consistency - it cannot break connectivity.
    "security.ssl3.ecdhe_ecdsa_aes_128_sha":              False,

    # Safebrowsing - chatty and fingerprintable.
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

    # Disable about:newtab auto-load - TopSitesFeed.sys.mjs auto-fetches when
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
    # [CORRETTO 2026-08-09] These three were False, and the reason above is the
    # reason they were: startup network chatter through a residential proxy.
    # But turning them off DELETES Web APIs from the page. Measured against
    # stock 151 with a 119-field sweep: `PushManager` was absent from `window`
    # and `geolocation` was absent from `Navigator.prototype` on both our
    # builds and present on stock. `'geolocation' in navigator` is one line,
    # and a missing API is a suppressed signal, which rule 12 counts as a FAIL
    # rather than a pass.
    #
    # The network goal is kept by the levers that actually carry it: the push
    # SERVICE connection stays off (the API only opens it when a page
    # subscribes), and geolocation only touches the network when a page calls
    # it, which the default-deny below answers locally. A user who blocked
    # location is the most ordinary thing on the web.
    "dom.push.enabled":                                   True,
    "dom.push.connection.enabled":                        False,
    "geo.enabled":                                        True,
    #: `permissions.default.geo` is deliberately NOT SET, and this comment is
    #: here so nobody adds it back for the reason it was added the first time.
    #: It was 2 (deny) for a few hours on 2026-08-09: a denial is local, needs
    #: no network, and is what plenty of real users have chosen. It is also
    #: VISIBLE. Measured across 21 permission names, it was the ONLY divergence
    #: from stock: `navigator.permissions.query({name:"geolocation"})` answered
    #: `denied` where stock answers `prompt`. A profile that has never been
    #: asked says `prompt`, and ours has never been asked - so the fix for one
    #: tell had quietly introduced a smaller one.
    #:
    #: Nothing replaces it. The provider URL below is empty, so a call cannot
    #: reach the network even if a page makes one, and Playwright grants no
    #: permissions by default, so the call is refused exactly as it is for a
    #: user who dismisses the prompt.
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

    # Media devices: a FIXED pair (one audioinput, one videoinput) on every
    # host. navigator.mediaDevices.enumerateDevices is only reachable in a
    # secure context, which is why an earlier probe on about:blank read it as
    # absent on both platforms and saw nothing. Measured properly 2026-08-08:
    # Linux enumerated 0 devices and Windows 2, and a Windows desktop with
    # neither microphone nor camera is the unusual one.
    #
    # But the Windows 2 came from THIS machine's hardware, so it was
    # host-dependent in the first place - a Windows box with no webcam would
    # have reported 1, and two of our own identities would have differed. The
    # fake pair is the invariant: 2 on every host, unchanged on a machine that
    # already had 2. Labels stay empty and device ids stay empty without
    # permission, exactly as a real browser reports them.
    # media.navigator.streams.fake -> HardwareProfile.fake_media_devices

    # Speech synth: enabled (the C++ patch fabricates voices from the
    # comma list above) regardless of the host OS.
    "media.webspeech.synth.enabled":                      True,
    # zoom.stealth.voices.list -> HardwareProfile.voices

    # WebGL extensions whitelist - non-empty pre-empts native enumeration.
    "zoom.stealth.webgl.extensions":                      _WEBGL1_EXTENSIONS,
    "zoom.stealth.webgl2.extensions":                     _WEBGL2_EXTENSIONS,
    # WebGL numeric param overrides - kept empty (A/B test 2026-04-22 showed
    # mismatches between the values we shipped and ANGLE's real envelope
    # raised FP Pro's ML tampering score). Slot kept for future experiments.
    "zoom.stealth.webgl.int_params":                      "",
    "zoom.stealth.webgl.int2_params":                     "",
    "zoom.stealth.webgl.shader_precisions":               "",
    "zoom.stealth.webgl.float_params":                    "",

    # DevTools anti-detection.
    "zoom.stealth.debugger.force_detach":                 True,

    # Canvas substitution (Option B for canvas) - replace pixels with hash(seed,idx),
    # uniform-skip (red-box exact, masking-safe) + full overwrite. Makes the canvas
    # render a pure function of (seed) = HOST-INDEPENDENT (kills the DWrite-vs-FreeType
    # text-raster leak: Canvas Hash + Font hash were the residual Win!=Linux signals).
    # ON by default (paired with webgl.substitute_pixels).
    "zoom.stealth.canvas.substitute_pixels":              True,

    # WebGL substitution (Option B) - replace readback/snapshot RGB with
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
    # - b005 Win dropped 0.4349 -> 0.0564 with audio noise alone disabled (canvas_text/
    # emoji unchanged, so they were a red-herring). The audio value is already host-indep
    # AND identical to a real FF's canonical OfflineAudioContext sum, so a fixed (un-noised)
    # audio is NOT a linking signal (every real FF has the same value) - removing the noise
    # matches real Firefox and clears the tampering flag.
    "zoom.stealth.audio.fp_noise":                        False,

    # Navigator identity (locked to Windows Firefox 150).
    **_NAVIGATOR_OVERRIDES,
}


# ──────────────────────────────────────────────────────────────────────
#  Linux-only Xvfb workarounds - the Linux Firefox build under Xvfb
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
#  Windows virtual-desktop workarounds - when headless=True on Windows,
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
    # Firefox on a CreateDesktop-created alt desktop - parent and GPU process
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
    # which is what we want here (still tight enough - level 4 blocks
    # file/registry write, network calls, hardware access).
    "security.sandbox.content.level": 4,
}


# ──────────────────────────────────────────────────────────────────────
#  Public helpers
# ──────────────────────────────────────────────────────────────────────

def _accept_language(locale: str) -> str:
    # "<locale>, <base>" - the desktop-default shape (e.g. "en-US, en"). Firefox expands it
    # to navigator.languages=["en-US","en"] AND (via the patched binary) the q-valued header
    # "en-US,en;q=0.5". The patched nsHttpHandler (STEALTHFOX, RE 2026-06-23) builds the
    # Accept-Language header from THIS pref even when juggler sets a per-context locale
    # override, so header and navigator.languages stay consistent 2/2 - the most authentic
    # (real desktop) form. Supersedes the 2026-06-22 single-tag workaround.
    lang = locale.replace("_", "-")
    base = lang.split("-")[0]
    return f"{lang}, {base}" if base != lang else lang


def _accept_language_header(locale: str) -> str:
    """The Accept-Language header exactly as Firefox 151 puts it on the wire.

    This is a DECLARATION and not a convenience: the engine used to synthesize
    it, in JavaScript, from a hardcoded ";q=0.5" (juggler/NetworkObserver.js),
    and the value was wrong. Measured 2026-08-09 against stock Firefox 151:
    stock sends `en-US,en;q=0.9` on every request and we sent `q=0.5` on every
    request, on both platforms - a constant, wire-visible difference that any
    server sees without running a line of JavaScript.

    The 0.5 came from writing the patch against a COMMENT. `PrepareAcceptLanguages`
    in nsHttpHandler.cpp still carries a doc block promising `"en, ja"` ->
    `"en,ja;q=0.5"`, which described the old C++ implementation; the function
    below it now forwards to `rust_prepare_accept_languages`, whose own comment
    says "we need to emulate chrome behavior i.e languages should get
    q=1.0,0.9,0.8". The JS patch copied the stale sentence.

    Replicated from that Rust code, not from its prose: for the token at index
    i, `q = max(10 - min(10, i), 1)`, and no q on the first token.
    """
    return _q_ladder([t.strip() for t in _accept_language(locale).split(",")
                      if t.strip()])


def _q_ladder(tags: "list[str]") -> str:
    """Attach q-values to an ordered tag list the way the engine does.

    Split out from the caller so it can be tested on more than the one or two
    tags a locale ever produces: the ladder is the part that was wrong, so it
    is the part worth pinning against a list built by hand.
    """
    out = []
    for i, tag in enumerate(tags):
        q = max(10 - min(10, i), 1)
        out.append(tag if i == 0 else "%s;q=0.%d" % (tag, q))
    return ",".join(out)


# ---------------------------------------------------------------------------
#  One section of the prefs dict per function.
#
#  These were 203 lines inside `translate_profile_to_prefs`, which is the whole
#  product's output in one place: every spoof this project ships leaves through
#  that dict. The comments are the valuable part of this file - each block below
#  keeps the one it came with, word for word.
#
#  They MUTATE `prefs` in a fixed order rather than returning dicts that get
#  merged. Two sections read what an earlier one wrote (the platform workarounds
#  use `setdefault`, and the caller overlay must be able to delete anything),
#  so the order is load-bearing and mutation says so; independent pure functions
#  merged by a caller would look interchangeable and would not be.
#
#  Verified by recording the full output first: 400 profiles across four
#  locales, four timezones, three overlay shapes and both virtual_display
#  values, hashed before and after. Identical.
# ---------------------------------------------------------------------------


#: WebGL enums whose getParameter answer is a Float32Array by SPECIFICATION,
#: even when every value in it happens to be a whole number.
#:
#: They have to be listed because the pool JSON cannot carry the distinction:
#: the generator buckets a two-element answer by Python type, JSON has one
#: number type, and [1.0, 1024.0] round-trips as [1, 1024]. So all three landed
#: in int2_params, and the engine's int2 branch builds an Int32Array.
#:
#: Measured 2026-08-09 against stock Firefox 151 on the same machine:
#:
#:     DEPTH_RANGE                stock Float32Array   ours Int32Array
#:     ALIASED_POINT_SIZE_RANGE   stock Float32Array   ours Int32Array
#:     ALIASED_LINE_WIDTH_RANGE   stock Float32Array   ours Int32Array
#:
#: The VALUES agreed - [0,1], [1,1024], [1,1] - only the type was wrong, and the
#: type is what a page reads with one instanceof, no reference data and no
#: second machine. MAX_VIEWPORT_DIMS (3386) is deliberately NOT here: the
#: specification really does make that one an Int32Array.
_WEBGL_FLOAT_PAIR_ENUMS = ("2928", "33901", "33902")


def _route_float_pairs(prefs: Dict[str, Any]) -> None:
    """Move the spec-float pairs out of int2_params and into float_params.

    Done here rather than in the pool data so it holds for every persona and for
    any pool regenerated later, and so the reason travels with the code instead
    of with fourteen JSON rows.
    """
    src = str(prefs.get("zoom.stealth.webgl.int2_params", "") or "")
    if not src:
        return
    keep, moved = [], []
    for entry in src.split(","):
        enum = entry.split("|", 1)[0]
        (moved if enum in _WEBGL_FLOAT_PAIR_ENUMS else keep).append(entry)
    if not moved:
        return
    prefs["zoom.stealth.webgl.int2_params"] = ",".join(keep)
    existing = str(prefs.get("zoom.stealth.webgl.float_params", "") or "")
    prefs["zoom.stealth.webgl.float_params"] = ",".join(
        ([existing] if existing else []) + moved)



def _apply_gpu_persona(prefs: Dict[str, Any], profile: Profile):
    """The validated WebGL persona, and the reason it is applied on every host.

    On Linux we spoof to a Windows ANGLE renderer string (profile.gpu.renderer)
    so cross-platform sessions report a consistent Windows GPU identity.
    On Windows/mac, spoofing a renderer string ALONE is unsafe - the ~81
    getParameter values stay real, so a name<->params hash mismatch FP Pro flags
    (setting GTX 980 over real Arc A750 params scored ~0.70). Instead we apply a
    VALIDATED PERSONA (see _webgl_personas): a {renderer, vendor} whose params are
    the shared ANGLE D3D11 caps (vendor-independent - identical on any host, per the
    ANGLE source) and whose extension list is FORCED below. That is a coherent fake
    GPU that passes FP Pro host-independently (the host's real GPU never leaks). If no
    validated persona exists for the sampled gpu_class yet, fall back to the host-real
    renderer (empty -> native ANGLE; SanitizeRenderer at ClientWebGLContext.cpp:2592).
    Apply the camoufox-derived real-Firefox GPU persona on EVERY host (Win/Linux/Mac).
    We must ALWAYS look Windows (rule), and the WebGL override is platform-independent:
    SanitizeRenderer (ClientWebGLContext.cpp) is pure string regex, and the param/extension
    overrides are pref-driven, so the C++ presents the SAME Windows ANGLE GPU regardless of
    the host's real GL backend (it never consults it when the pref is set). This is why a
    Windows GPU shows correctly even on a Linux/Mesa host (no more "Generic Renderer").

    Returns the persona, because `_apply_extension_lists` needs to know whether
    one was applied.
    """
    persona = select_persona(profile.seed)
    if persona:
        # Apply the FULL coherent WebGL override (renderer + vendor + webgl1/webgl2 extensions
        # + ~100 getParameter values + shader-precision formats). Setting ALL of them - not just
        # the renderer string - keeps renderer<->params coherent (FP Pro cross-checks them); a
        # string-only spoof over the host's real params is the old ~0.85 mismatch.
        for _k, _v in persona["prefs"].items():
            if _k == "zoom.stealth.webgl2.enabled":
                prefs["webgl.enable-webgl2"] = bool(_v)
            else:
                prefs[_k] = _v
        _route_float_pairs(prefs)
    else:
        prefs["zoom.stealth.webgl.renderer"] = ""
        prefs["zoom.stealth.webgl.vendor"]   = ""
    return persona


#: The canvas-noise mask, pinned to the Intel rate (1/16, ~6.25%).
#:
#: The mask is calibrated to the REAL host GPU's rendering variance - the canvas
#: is drawn by real hardware, NOT the persona's claimed GPU, so it must NOT
#: follow the persona (a non-Intel persona on an Intel host would over-noise).
#: Intel has lower natural rendering variance than NVIDIA/AMD, so the 1/8 rate
#: over-amplifies the FP Pro tampering_ml signal.
#:
#: This was written as `_renderer_lo = "intel"` followed by
#: `if "intel" in _renderer_lo: ... else: 7`, which reads as a per-vendor choice
#: and is not one: the condition is a constant compared against itself, so the
#: 1/8 branch has never executed. Measured across 300 seeds, every profile gets
#: 15. Written as the constant it is, with the reason it is constant - a dead
#: branch that looks live is a decision the next reader thinks was made.
#:
#: To make it host-dependent again, sample the REAL renderer here; do not
#: reintroduce the branch over a literal.
_CANVAS_NOISE_SKIP_MASK = 15


def _apply_canvas_and_msaa(prefs: Dict[str, Any], profile: Profile) -> None:
    # MSAA: on Windows, pin to 4 (Firefox default for ANGLE) so gl.SAMPLES is
    # constant across all sessions. Different MSAA values cause different CN-set
    # parameters hashes even with the same renderer -> detectable variation.
    # Vanilla Intel Arc A750 parameters hash (66544db8) verified at msaa=4.
    _msaa = profile.webgl.msaa_samples if sys.platform.startswith("linux") else 4
    # DEAD: appears in NO file of the engine source; MSAA sample counts come from the real GL driver.
    # prefs["zoom.stealth.webgl.msaa"]        = _msaa
    prefs["webgl.msaa-samples"]             = _msaa
    prefs["webgl.msaa-force"]               = _msaa > 0
    prefs["zoom.stealth.canvas.noise_skip_mask"] = _CANVAS_NOISE_SKIP_MASK


def _apply_screen(prefs: Dict[str, Any], profile: Profile) -> None:
    prefs["zoom.stealth.screen.width"]        = profile.screen.width
    prefs["zoom.stealth.screen.height"]       = profile.screen.height
    prefs["zoom.stealth.screen.color_depth"]  = profile.screen.color_depth
    prefs["zoom.stealth.screen.taskbar_px"]   = profile.screen.taskbar_px
    # The window geometry. Four values because three getters read them in
    # different combinations - screenX, mozInnerScreenX and outerWidth - and
    # declaring only some of them is what produced a window whose right edge
    # was off the screen. Measured against stock 151: 0, 0, 0, 85.
    prefs["zoom.stealth.screen.window_x"]     = profile.screen.window_x
    prefs["zoom.stealth.screen.window_y"]     = profile.screen.window_y
    prefs["zoom.stealth.screen.chrome_w"]     = profile.screen.chrome_w
    prefs["zoom.stealth.screen.chrome_h"]     = profile.screen.chrome_h
    # DEAD, and kept only so the next reader does not re-add them. Neither name
    # is declared in StaticPrefList.yaml, and nsScreen::GetAvailRect ignores
    # them outright: it reads zoom_stealth_screen_width/height and subtracts
    # the DECLARED taskbar_px above. (This comment said "a fixed 48px taskbar"
    # until 2026-08-09, which was true when it was written and stopped being
    # true the day that literal became a declaration - the hazard of describing
    # someone else's file from memory.) Writing them changes nothing; the
    # available rect is already derived from the values above.
    #   prefs["zoom.stealth.screen.avail_width"]  = profile.screen.avail_width
    #   prefs["zoom.stealth.screen.avail_height"] = profile.screen.avail_height
    # DEAD: appears in NO file of the engine source; the DPR that reaches a page comes from layout.css.devPixelsPerPx on the line below.
    # prefs["zoom.stealth.screen.dpr"]          = profile.screen.dpr
    prefs["layout.css.devPixelsPerPx"]        = str(profile.screen.dpr)


def _apply_hardware(prefs: Dict[str, Any], profile: Profile) -> None:
    # Coherent with the sampled gpu_class by construction (the forge draws
    # hw_concurrency conditioned on the GPU class).
    prefs["zoom.stealth.hw_concurrency"]      = profile.hardware.concurrency
    prefs["zoom.stealth.storage.quota_mb"]    = profile.hardware.storage_quota_mb
    # ── Touch: ONE declaration, three surfaces that must agree ──────────────
    #
    # A page can read the touch story three ways, and before 2026-08-09 they
    # came from three places:
    #
    #   navigator.maxTouchPoints   <- declared here (0)
    #   typeof window.Touch        <- dom.w3c_touch_events.enabled, whose
    #                                 default 2 means "ask the HOST"
    #   matchMedia("(any-pointer: coarse)")
    #                              <- a compiled `Fine | Hover` in
    #                                 nsMediaFeatures.cpp
    #
    # Measured that day, our Windows build reported the Touch interfaces
    # PRESENT (this development machine has a digitizer, so the host said yes)
    # with maxTouchPoints 0 and any-pointer coarse false: a machine that
    # supports touch events, has no touch points and has no coarse pointer. No
    # such machine exists. Our Linux build had no Touch interfaces at all, so
    # the same persona answered differently on the two hosts.
    #
    # All three now come from the touch-point count. Pin max_touch_points to 10
    # and the persona becomes a touch laptop across all three surfaces at once;
    # leave it at 0 and it is a desktop with a mouse, coherently.
    touch = profile.hardware.max_touch_points > 0
    prefs["zoom.stealth.max_touch_points"]    = profile.hardware.max_touch_points
    #: 1 = force on, 0 = force off. NEVER 2: at 2 the engine calls
    #: PlatformSupportsTouch() and the answer is the machine we happen to be
    #: running on (dom/events/TouchEvent.cpp).
    prefs["dom.w3c_touch_events.enabled"]     = 1 if touch else 0
    #: PointerCapabilities bitmask (ServoTypes.h): Coarse 1, Fine 2, Hover 4.
    #: The PRIMARY pointer of a laptop with a touchscreen is still the mouse,
    #: so only the union gains Coarse - which is exactly the distinction the
    #: `pointer` / `any-pointer` pair exists to express.
    prefs["zoom.stealth.pointer.primary"]     = 2 | 4
    prefs["zoom.stealth.pointer.all"]         = (2 | 4 | 1) if touch else (2 | 4)
    prefs["zoom.stealth.voices.list"]         = profile.hardware.voices
    prefs["media.navigator.streams.fake"]     = bool(profile.hardware.fake_media_devices)
    # The four storage booleans a page reads (cookieEnabled, localStorage,
    # sessionStorage, indexedDB) come out of these two levers. Gecko has no
    # dom.indexedDB.enabled: indexedDB availability is decided by the cookie
    # behaviour and the storage-access state, which is why declaring the two
    # levers covers all four answers. 0 is BEHAVIOR_ACCEPT.
    _storage = bool(profile.hardware.storage_enabled)
    prefs["network.cookie.cookieBehavior"]    = 0 if _storage else 2
    prefs["dom.storage.enabled"]              = _storage
    prefs["zoom.stealth.fonts.generics"]      = profile.hardware.generics
    # The accessibility media features. Stock Firefox reads these generic prefs
    # BEFORE the per-platform native code (nsXPLookAndFeel), so declaring them
    # needs no patch at all - they were simply never declared, and answered from
    # the host on both platforms.
    _a11y = 1 if profile.hardware.accessibility_overrides else 0
    prefs["ui.prefersReducedMotion"]          = _a11y
    prefs["ui.prefersReducedTransparency"]    = _a11y
    prefs["ui.invertedColors"]                = _a11y


def _apply_audio(prefs: Dict[str, Any], profile: Profile) -> None:
    prefs["zoom.stealth.audio.sample_rate"]       = profile.audio.sample_rate
    prefs["zoom.stealth.audio.output_latency_ms"] = profile.audio.output_latency_ms
    prefs["zoom.stealth.audio.max_channel_count"] = profile.audio.max_channel_count


def _apply_codecs(prefs: Dict[str, Any], profile: Profile) -> None:
    # EME, declared TRUE on every host, and it is not cosmetic.
    #
    # StaticPrefList gives media.eme.enabled the value false on XP_LINUX and
    # true everywhere else - an upstream choice about DRM on free operating
    # systems, not about us. MediaKeySystemAccessManager then rejects every
    # non-clearkey key system outright when it is false, BEFORE it even looks
    # for a CDM. So a Linux build claiming to be Windows answers a question
    # that a real Windows Firefox answers differently.
    #
    # Measured 2026-08-09, one call to requestMediaKeySystemAccess:
    #   our Windows  OK keySystem=com.widevine.alpha
    #   our Linux    NotSupportedError: EME has been preffed off
    # The error text names the cause, which is as detectable as it gets, so
    # rule 7-bis is satisfied by that line alone: a page sees it in one call.
    #
    # This declares the ANSWER TO THE QUESTION, not the presence of a CDM.
    # Whether a Widevine plugin is actually installed stays a real, dynamic
    # fact (GMP download state) that rule 2 forbids tabling - and a fresh
    # Windows profile sits in the same state until its first EME page.
    prefs["media.eme.enabled"]                = True
    prefs["media.av1.enabled"]                = profile.codec.av1_enabled
    prefs["media.encoder.webm.enabled"]       = profile.codec.webm_encoder_enabled
    # NOT media.mediasource.{webm,mp4}.enabled. Those two names do not exist in
    # Firefox - verified against modules/libpref/init/StaticPrefList.yaml, which
    # declares only media.mediasource.enabled / .vp9.enabled / .experimental.
    # Setting a name the binary never reads is a no-op, so the per-seed codec
    # diversity this samples was fictional: every identity we shipped reported
    # the SAME codec surface to canPlayType and MediaSource.isTypeSupported,
    # which is an invariant across the fleet rather than the variation intended.
    # The real switches are media.webm.enabled and media.mp4.enabled.
    prefs["media.webm.enabled"]               = profile.codec.mediasource_webm
    prefs["media.mp4.enabled"]                = profile.codec.mediasource_mp4
    # The DECLARED answers, consulted by the engine before any decoder is
    # asked. The four toggles above are the other pattern: they switch a real
    # capability on or off, which cannot make a decoder the build does not
    # carry exist - and that is exactly why H.264 stayed divergent while every
    # other codec in a 14-type probe agreed.
    prefs["zoom.stealth.media.decode_support"] = _WIN_DECODE_SUPPORT
    prefs["zoom.stealth.media.decoding_info"] = _WIN_DECODING_INFO


def _apply_rasterisation(prefs: Dict[str, Any], profile: Profile) -> None:
    """The glyph rasterisation parameters, declared instead of asked for.

    Six values that the engine reads once at startup and then keeps for the
    whole process. Before this they came from the machine:
    `gfxDWriteFonts::UpdateClearTypeVars` ran defaults -> SYSTEM -> prefs, so
    the user's own ClearType tuning survived wherever a pref was absent, and on
    a development machine that produces the answer you wanted anyway. The
    binary now skips the system query entirely when stealth is on, so these
    prefs are the only source.

    The FreeType pair is not a mirror for its own sake. Skia's glyph mask gamma
    defaults to LINEAR on Linux against DWrite's 2.2, which is two different
    coverage curves over the same outlines, and no amount of matching fonts
    closes that. Measured 2026-08-08: setting it moves the canvas text hash on
    every value, so the lever reaches the rasteriser, but it does not converge
    on its own - the residual is the alpha quantisation, which is a separate
    fix on Skia's A8 mask path.
    """
    f = profile.font
    prefs["gfx.font_rendering.cleartype_params.gamma"]             = f.cleartype_gamma
    prefs["gfx.font_rendering.cleartype_params.enhanced_contrast"] = f.cleartype_contrast
    prefs["gfx.font_rendering.cleartype_params.cleartype_level"]   = f.cleartype_level
    prefs["gfx.font_rendering.cleartype_params.pixel_structure"]   = f.cleartype_pixel_structure
    prefs["gfx.font_rendering.cleartype_params.rendering_mode"]    = f.cleartype_rendering_mode
    prefs["gfx.font_rendering.freetype.gamma"]                     = f.freetype_gamma
    prefs["gfx.font_rendering.freetype.enhanced_contrast"]         = f.freetype_contrast


def _apply_fonts(prefs: Dict[str, Any], profile: Profile) -> None:
    """The Windows system-font surface, from `profile.font`.

    This used to be a comment saying there was nothing to configure (below),
    and that was true only of the FONT LIST. The system-font surface is a
    different thing and it was not covered: with these prefs absent, Gecko
    answers `font: menu` from its own per-OS defaults, which on Linux name
    "Sans" at 13.3333px - a family that does not exist on Windows, on a build
    whose every other signal says Windows. Measured 2026-08-07, that single
    disagreement is what drove FpJS Pro to tampering=True on Linux with
    Windows clean, same seed and same IP.

    The 26 prefs also ship compiled into the binary's all.js. The duplication
    is deliberate: the binary has to stay right when launched WITHOUT this
    package, and the failure mode is not a subtle drift but a family name no
    Windows machine has. all.js is the floor; this layer is the source of
    truth that can move without a Firefox rebuild, and being a layer it can be
    overridden by `extra_prefs` like every other surface.
    """
    for element in _UI_FONT_ELEMENTS:
        prefs[f"ui.font.{element}"] = profile.font.ui_family
        # Kept as the string the profile carries: Preferences::GetFloat reads
        # float prefs from their text form, and an int here is not an error,
        # it is silently ignored in favour of StyleFONT_MEDIUM_PX (16px).
        prefs[f"ui.font.{element}.size"] = profile.font.ui_size
    for lang in _MONOSPACE_LANG_GROUPS:
        prefs[f"font.size.monospace.{lang}"] = profile.font.monospace_size
    # The glyph-edge coverage ladder. It used to live in the binary's font
    # manifest, which was the wrong home twice over: it is not a property of any
    # font FILE (it is what the rasteriser does to an edge), and putting it
    # there meant changing it required a Firefox rebuild. Empty string disables
    # the snap, which is what a caller wants when measuring the tell itself.
    prefs["zoom.stealth.text.coverage_ladder"] = ",".join(
        str(int(v)) for v in profile.font.alpha_ladder)
    # The whole font manifest - families, per-face vertical metrics, the alias
    # table and the per-script fallback lists - carried by this package and
    # handed to the binary, which prefers it over the copy in its own
    # directory. One pref rather than six: the binary already has a parser for
    # this exact text, so moving the CONTENT costs one code path instead of a
    # new format per table.
    #
    # It is a COPY of what the binary ships, and a copy can drift. What stops
    # it drifting silently is the seal: the manifest hash belongs there next to
    # the UA and the BuildID, so a core describing one font generation and a
    # binary carrying another is refused rather than rendered. Until that check
    # exists this is the riskier half of the trade, and it is the reason the
    # binary keeps its own file as the floor.
    if profile.font.manifest:
        prefs["zoom.stealth.fonts.manifest"] = profile.font.manifest


# Font LIST - nothing to configure, and that is a different question from the
# system-font surface above, which _apply_fonts does own. The
# patched binary is self-contained: it is always bundle-only (host system fonts
# never enter the font list), exposes exactly the bundled standard-Windows
# families, and bakes system-ui -> "Segoe UI" and the CSS generics -> Windows
# fonts in C++. There is no external fontlist / allow-list / name-list: the list
# IS the bundle. See gfxPlatformFontList (StealthSkipFamily,
# StealthGenericWindowsFont).


def _apply_theme(prefs: Dict[str, Any], profile: Profile) -> None:
    """Dark mode, plus the Windows colours palette when the theme is light."""
    prefs["ui.systemUsesDarkTheme"] = int(profile.dark_theme)
    if not profile.dark_theme:
        prefs.update(_WIN_LIGHT_COLORS)


def _apply_locale(prefs: Dict[str, Any], locale: str) -> None:
    locale = locale or "en-US"
    lang = locale.replace("_", "-")
    prefs["intl.accept_languages"]     = _accept_language(locale)
    # The wire header, declared rather than synthesized. Juggler has to rewrite
    # Accept-Language because Playwright sets it from the `locale` option as a
    # single tag, which then disagrees with navigator.languages; it used to
    # rebuild the q-values in JavaScript from a hardcoded 0.5. It reads this
    # instead, so the header has one source and it is this file.
    prefs["zoom.stealth.http.accept_language"] = _accept_language_header(locale)
    prefs["general.useragent.locale"]  = lang
    prefs["intl.locale.requested"]     = lang
    prefs["privacy.spoof_english"]     = 0
    # juggler.locale.override seeds the BrowsingContext LanguageOverride FIELD in
    # the parent process (BrowsingContext::Attach), whose DidSet drives BOTH
    # navigator.languages (the full list) AND the realm Intl default locale (the
    # primary tag it extracts) - so Intl.DateTimeFormat / NumberFormat /
    # toLocaleString follow the locale, not just the Accept-Language header. Seed
    # it with the full Accept-Language list so navigator.languages stays the
    # desktop-default 2 elements (["fr-FR","fr"]); the C++ DidSet takes "fr-FR"
    # for Intl. Mirrors juggler.timezone.override; the SOLE source of truth.
    prefs["juggler.locale.override"]   = _accept_language(locale)


def _apply_timezone(prefs: Dict[str, Any], timezone: str) -> None:
    if timezone:
        # juggler.timezone.override is the SOLE source of truth read by the C++
        # timezone chain (BrowsingContext::Attach/DidSet, ContentChild). The old
        # zoom.stealth.timezone pref was declared in the yaml but read by NO
        # code - dropped here on 2026-06-10 (see 20-our-patches.md section 8).
        prefs["juggler.timezone.override"] = timezone


def _apply_render_seed(prefs: Dict[str, Any], profile: Profile) -> None:
    # Cross-process seed (canvas noise + DWrite gamma share this). Only
    # zoom.stealth.fpp.hw_seed is read by the C++; the old zoom.stealth.seed
    # alias was never declared in the yaml and read by nothing - dropped
    # 2026-06-10. The render-noise seed is DECOUPLED from the identity seed and
    # drawn from a calibrated CLEAN pool: the canvas/WebGL render HASH it drives
    # is the dominant FP Pro tampering_ml signal, and some hw_seeds yield a
    # "suspicious" render hash. render_noise_seed() maps to the clean pool while
    # keeping per-seed determinism + diversity. See _webgl_personas.
    prefs["zoom.stealth.fpp.hw_seed"] = render_noise_seed(profile.seed)


def _apply_webrtc_host_ip(prefs: Dict[str, Any], profile: Profile) -> None:
    # Synthetic host ICE candidate - injected by C++ when addr_ct==0 (SOCKS5
    # proxy suppresses all local addresses so Firefox can't gather host cands).
    # LAN IP is seed-derived so it's consistent per session and looks like a
    # real home router assignment (192.168.x.x range).
    _s = profile.seed
    prefs["zoom.stealth.webrtc.host_ip"] = f"192.168.{(_s >> 8) % 254 + 1}.{_s % 254 + 1}"


def _apply_extension_lists(prefs: Dict[str, Any], persona) -> None:
    """Windows/mac extension list.

      - persona active -> the coherent webgl1/webgl2 extension lists (in the GPU's real
        native order) were ALREADY applied above from the GPU pool's `prefs`, alongside the
        matching renderer + params + shader-precisions. Nothing to do here.
      - no persona -> clear so the host-real renderer reports its native extension set
        (matches real vanilla captures for that host's GPU).
    """
    if not sys.platform.startswith("linux") and not persona:
        prefs["zoom.stealth.webgl.extensions"]  = ""
        prefs["zoom.stealth.webgl2.extensions"] = ""


def _apply_platform_workarounds(prefs: Dict[str, Any], *, virtual_display: bool) -> None:
    """`setdefault`, deliberately: anything a section above chose already wins."""
    # Linux Xvfb workarounds (no-op on Windows).
    if sys.platform.startswith("linux"):
        for k, v in _LINUX_XVFB_WORKAROUNDS.items():
            prefs.setdefault(k, v)

    # Windows virtual-desktop workarounds (headless=True on Windows).
    if virtual_display and sys.platform == "win32":
        for k, v in _WIN_VIRT_DESKTOP_WORKAROUNDS.items():
            prefs.setdefault(k, v)


def _apply_caller_overlay(prefs: Dict[str, Any],
                          extra_prefs: Optional[Dict[str, Any]]) -> None:
    """LAST, so users can override anything we set.

    A value of None is a sentinel meaning "delete this pref entirely from the
    final dict" - useful for A/B harnesses that need to test what happens when
    an override is unset (vs set to empty string, which for some prefs like
    general.useragent.override means literally empty UA).
    """
    if extra_prefs:
        for k, v in extra_prefs.items():
            if v is None:
                prefs.pop(k, None)
            else:
                prefs[k] = v


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

    The body is the ORDER, and the order is the contract: the caller overlay
    runs last so it can override or delete anything, and the platform
    workarounds `setdefault` so they never take a choice away from a section
    above them. Each step keeps the reasoning it was written with.
    """
    prefs: Dict[str, Any] = dict(_BASELINE)

    persona = _apply_gpu_persona(prefs, profile)
    _apply_canvas_and_msaa(prefs, profile)
    _apply_screen(prefs, profile)
    _apply_hardware(prefs, profile)
    _apply_audio(prefs, profile)
    _apply_fonts(prefs, profile)
    _apply_rasterisation(prefs, profile)
    _apply_codecs(prefs, profile)
    _apply_theme(prefs, profile)
    _apply_locale(prefs, locale)
    _apply_timezone(prefs, timezone)
    _apply_render_seed(prefs, profile)
    _apply_webrtc_host_ip(prefs, profile)
    _apply_extension_lists(prefs, persona)
    _apply_platform_workarounds(prefs, virtual_display=virtual_display)
    _apply_caller_overlay(prefs, extra_prefs)

    return prefs


# ──────────────────────────────────────────────────────────────────────
#  One composition, not three
# ──────────────────────────────────────────────────────────────────────
#
# `translate_profile_to_prefs` is the fingerprint. It is never the whole prefs
# dict a session runs with: a proxy, a cloak, a humanize toggle and two crash
# prefs sit on top of it, and until 2026-08-01 each of the three entry points
# added its own subset in its own order.
#
#     build_launch_plan          proxy, crash prefs
#     _session.build_prefs       cloak, humanize          (invisible-playwright)
#     get_default_stealth_prefs  humanize
#
# Measured consequence: a caller using `get_default_stealth_prefs` with a SOCKS
# proxy got no `network.proxy.*` pref at all, so the auth prefs the patched
# binary reads were simply absent. Nothing compared the three dicts.
#
# The layers and their order are the union of what the three did, unchanged:
#
#   1. the fingerprint          translate_profile_to_prefs (extra_prefs last)
#   2. the proxy                configure_proxy, mutating
#   3. the cloak                setdefault, so extra_prefs still wins
#   4. humanize                 update, so it wins over extra_prefs
#   5. surviving a hard kill    setdefault, so a caller can override
#
# setdefault vs update is not a detail: each one is the precedence the layer had
# before, and swapping either silently changes what a caller's extra_prefs can
# reach.

#: Cap on a binary-drawn mouse path when `humanize=True`, in seconds.
HUMANIZE_MAX_SECONDS = 1.5


class ComposedPrefs(NamedTuple):
    """The prefs, and the proxy Playwright still has to be told about.

    Two returns because `configure_proxy` has two outputs: it writes the SOCKS
    auth prefs (which the binary reads) and hands back the HTTP/HTTPS dict
    (which only Playwright can act on). A composer that returned prefs alone
    would force every caller to run the proxy step a second time to recover it.
    """
    prefs: Dict[str, Any]
    playwright_proxy: Optional[Dict[str, str]]


def humanize_max_seconds(humanize: Any) -> float:
    """The motion cap implied by a `humanize=` value, for any value.

    Anything unusable falls back to the default rather than raising or being
    written through. `get_default_stealth_prefs` used to do `float(humanize)`
    bare: `humanize="fast"` raised ValueError out of a pref builder, and
    `humanize=-1` wrote `stealthfox.humanize.maxTime = "-1.0"` into the profile.
    The wrapper's own copy of this function had been robust since 0.4.0; this is
    that behaviour, in the one place both now read.
    """
    if humanize is True:
        return HUMANIZE_MAX_SECONDS
    try:
        value = float(humanize)
    except (TypeError, ValueError):
        return HUMANIZE_MAX_SECONDS
    return value if value > 0 else HUMANIZE_MAX_SECONDS


def humanize_prefs(humanize: Any) -> Dict[str, Any]:
    """The `stealthfox.*` prefs implied by a `humanize=` value.

    Falsy turns the binary's own path expansion OFF and writes no cap, which is
    what a caller drawing its own trajectories needs: with both generators live
    the browser expands a path between each pair of the caller's waypoints.
    """
    if not humanize:
        return {"stealthfox.humanize": False}
    return {
        "stealthfox.humanize": True,
        "stealthfox.humanize.maxTime": str(humanize_max_seconds(humanize)),
    }


def compose_session_prefs(
    profile: Profile,
    *,
    locale: Optional[str] = None,
    timezone: Optional[str] = None,
    extra_prefs: Optional[Dict[str, Any]] = None,
    virtual_display: bool = False,
    proxy: Optional[Dict[str, str]] = None,
    cloak: bool = False,
    humanize: Any = None,
    survive_hard_kill: bool = False,
) -> ComposedPrefs:
    """Every pref a session runs with, in one place.

    Each layer above the fingerprint is a flag, and a flag left at its default
    means that layer is not applied - so a caller that wants only the
    fingerprint gets exactly what `translate_profile_to_prefs` returns.

    `humanize=None` is not the same as `humanize=False`: None leaves the two
    `stealthfox.humanize*` prefs untouched, which is what the direct-launch path
    has always done, and False writes the pref off. The distinction matters
    because the pref has a default compiled into the binary, and never writing
    it is not the same as writing it false.

    What each caller still owns is DELIVERY, which is the part that genuinely
    differs: the direct-launch path writes a `user.js`, the Playwright path
    passes a dict to `firefox_user_prefs=`.
    """
    prefs = translate_profile_to_prefs(
        profile,
        locale=locale,
        timezone=timezone,
        extra_prefs=extra_prefs,
        virtual_display=virtual_display,
    )
    playwright_proxy = configure_proxy(proxy, prefs) if proxy else None
    if cloak:
        # setdefault: an explicit caller override wins over the cloak.
        for key, value in cloak_prefs().items():
            prefs.setdefault(key, value)
    if humanize is not None:
        prefs.update(humanize_prefs(humanize))
    if survive_hard_kill:
        # A persistent profile can be hard-killed - a manager Stop, or a kill
        # mid-startup on a rapid relaunch. Keep Firefox from counting that as a
        # startup crash (the "closed unexpectedly / Safe Mode" prompt) or
        # offering to restore a "crashed" session.
        prefs.setdefault("toolkit.startup.max_resumed_crashes", -1)
        prefs.setdefault("browser.sessionstore.resume_from_crash", False)
    return ComposedPrefs(prefs, playwright_proxy)
