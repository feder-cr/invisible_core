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
    # F0F0F0. This one value was declared three times in two days, twice from a
    # binary that was not the right judge, and the sequence is worth more than
    # the number.
    #
    #   declared      FFFFFF   no measurement behind it
    #   2026-08-09    F0F0F0   judged by C:/tmp/ff151-win2
    #   2026-08-09    FFFFFF   judged by C:/tmp/retail151, "the signed retail"
    #   2026-08-10    F0F0F0   judged by a binary verified to be both
    #
    # ff151-win2 reports 151.0 and is not retail: Get-AuthenticodeSignature
    # says NotSigned, ProductName is "Nightly", IsPrivateBuild is true. Right
    # version, wrong build. So the second row was rejected - correctly.
    #
    # C:/tmp/retail151 was then downloaded and extracted to be the answer to
    # that, and its application.ini says Version=153.0.3: the extraction had
    # failed and the directory kept what was already in it. Right provenance,
    # wrong version, and the name said 151 both times. So the third row was
    # measured against 153, which genuinely answers white here - the difference
    # is upstream drift between the two majors, which is exactly the thing
    # rule 4's same-major clause exists to keep out of our numbers.
    #
    # The fourth row is C:/tmp/ffjudge-151.0: application.ini Version=151.0
    # BuildID=20260516144017, Authenticode Valid, CN=Mozilla Corporation,
    # ProductName Firefox, IsPrivateBuild false. Both checks, on the same
    # binary, before it was allowed to decide anything. It answers
    # rgb(240,240,240), which means the FIRST correction had the right value
    # from the wrong evidence - and a right answer from a bad judge is still
    # not knowledge, because the next value it decides will be wrong.
    #
    # Measured the same day across all 35 keywords: our Windows build differs
    # from that judge on this one field and no other, and 6 of the 35 do not
    # resolve from content at all. That 1-of-35 is why a wrong judge survives
    # so long - it agrees almost everywhere.
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
#  package (a manual run, or any other code that launches the binary
#  directly - invisible_firefox did this before its 2026-08-18 deletion),
#  because the fallback is not a subtle drift - Gecko's own defaults name "Sans" at
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
    # The CSS property set a page enumerates has to match retail, and ours was
    # one short. Measured 2026-08-10 against a Firefox 151.0 downloaded from
    # archive.mozilla.org and Authenticode-verified as signed by Mozilla:
    # getComputedStyle(document.documentElement).length is 383 there and was 382
    # here, the missing name being `field-sizing`, and
    # CSS.supports("field-sizing: content") answered false where retail says
    # true. One line of JavaScript.
    #
    # The cause is NOT one of our patches: `layout.css.field-sizing.enabled`
    # defaults to false in this source tree and nothing overrides it, while the
    # shipped 151.0 has the property live. That is a difference between our
    # source base and the released revision - see the entry in 70-known-bugs.md,
    # because three sibling differences (IDBRecord, PictureInPictureEvent,
    # PictureInPictureWindow) are NOT prefs: those interfaces do not exist in
    # our webidl at all and cannot be switched on.
    #
    # This one can, and it takes the count from 382 to exactly 383 with the
    # name in the right place.
    "layout.css.field-sizing.enabled":                    True,

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
    #   ICE-gathering code). Il filtro vero e' in nICEr (addrs.cpp), e dal
    #   2026-08-25 legge UNA sola fonte: la variabile d'ambiente
    #   STEALTHFOX_WEBRTC_DISABLE_IPV6, scritta incondizionatamente da
    #   `launch.build_launch_env` e dal `_session.build_env` del wrapper.
    #   La pref `zoom.stealth.webrtc.disable_ipv6` NON si scrive piu': il ponte
    #   nativo non la legge, e una pref che nessun C++ legge e' proprio cio' che
    #   `test_no_orphan_prefs_in_baseline` vieta di emettere.
    "media.peerconnection.enabled":                       True,
    "media.peerconnection.ice.no_host":                   False,
    "media.peerconnection.ice.default_address_only":      False,
    "media.peerconnection.ice.obfuscate_host_addresses":  True,
    "media.peerconnection.ice.proxy_only":                False,
    "media.peerconnection.ice.relay_only":                False,
    "media.peerconnection.use_document_iceservers":       True,

    # Proxy - route DNS through SOCKS proxies to avoid local DNS leaks.
    #
    # There are TWO prefs and they are not aliases. `nsProtocolProxyService`
    # reads `socks_remote_dns` into mSOCKS4ProxyRemoteDNS and
    # `socks5_remote_dns` into mSOCKS5ProxyRemoteDNS, and `SOCKSRemoteDNS()`
    # picks between them by the negotiated SOCKS version. This project uses
    # SOCKS5, so the one declared here drove the branch that never runs, and
    # the branch that DOES run took Firefox's own compiled default.
    #
    # That default is `true`, so nothing leaked - which is exactly why it
    # survived: the safe behaviour was somebody else's default rather than our
    # declaration. An upstream flip on a rebase, or a proxy that negotiates
    # SOCKS4, and local DNS resolution comes back with nothing here asserting
    # otherwise. Both are declared now. Added 2026-08-09.
    "network.proxy.socks_remote_dns":                     True,
    "network.proxy.socks5_remote_dns":                    True,
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

    # First-run / welcome UI noise.
    #
    # ⛔ QUI STAVA `browser.startup.page: 0`, TOLTA IL 2026-08-20 col revert del
    # newtab. Era l'ULTIMO punto vivo che sopprimeva about:home: con 0 la finestra
    # iniziale parte vuota anche con i cinque file del sorgente riportati a
    # upstream e le cinque prefs newtab tolte, quindi il revert sarebbe stato
    # completo a meta' e la pagina non si sarebbe vista lo stesso. Il default di
    # Gecko e' 1, cioe' la home page, che e' cio' che fa un retail.
    # Le tre righe qui sotto restano: sono la finestra di benvenuto e il
    # controllo del browser predefinito, che sono un'altra cosa dal newtab.
    "browser.shell.checkDefaultBrowser":                  False,
    "browser.aboutwelcome.enabled":                       False,
    "browser.startup.upgradeDialog.enabled":              False,
    # ⛔ QUI STAVA `termsofuse.acceptedVersion: 999`, ED ERA UNA DICHIARAZIONE
    # MORTA. Tolta il 2026-08-19 dopo averla misurata, non dedotta.
    #
    # Non faceva niente per due ragioni indipendenti, e ognuna basta:
    #  1. duplicava il default upstream: browser/app/profile/firefox.js
    #     dichiara gia' termsofuse.acceptedVersion a 999 di suo.
    #  2. da sola non sarebbe bastata comunque. hasUserAcceptedCurrentTOU
    #     (TelemetryReportingPolicy.sys.mjs:536) vuole ANCHE
    #     termsofuse.acceptedDate non nulla, e upstream la mette a "0".
    #     Misurato: con la sola versione il modale esce lo stesso.
    #
    # E non e' riparabile da qui in nessun caso: le prefs di questo dizionario
    # viaggiano sul protocollo e Browser.enable le applica DOPO l'avvio, mentre
    # il modale nasce con la finestra. Misurato: le stesse prefs passate come
    # firefox_user_prefs non riparano, scritte in un user.js prima del lancio
    # riparano. Il rimedio vive nel default compilato del nostro Firefox:
    # browser/app/profile/firefox.js, termsofuse.acceptedDate.
    #
    # Cosa costava tenerla: su una build MOZILLA_OFFICIAL il modale dei termini
    # d'uso copre il viewport e mangia OGNI evento di mouse - fuoco su BODY,
    # zero mousedown, zero mousemove - mentre la tastiera continua a funzionare.
    # Questa riga sembrava proteggerci e non proteggeva niente.

    # ⛔ LE CINQUE PREFS DEL NEWTAB SONO STATE TOLTE (2026-08-20), ED E' UNA
    # DECISIONE DEL PROPRIETARIO: "voglio riavere il codice originale, dei 34mb
    # rimettere questo processo in piedi". Erano queste:
    #
    #   browser.newtabpage.enabled                                   False
    #   browser.newtab.preload                                       False
    #   browser.newtabpage.activity-stream.feeds.topsites            False
    #   browser.newtabpage.activity-stream.feeds.section.topstories  False
    #   browser.newtabpage.activity-stream.enabled                   False
    #
    # La seconda e' quella che teneva GIU' il processo preallocato della nuova
    # scheda, quindi senza toglierla il revert del sorgente non bastava: i
    # cinque file di Firefox sono tornati a upstream, ma il processo sarebbe
    # rimasto spento da qui. Ora il retail e noi partiamo dagli stessi default.
    #
    # ⛔ LA REGRESSIONE E' TORNATA IL 2026-08-23, ED E' STATA CHIUSA NEL MOTORE:
    # queste righe restano tolte e non vanno rimesse.
    #
    # Il segnale era quello previsto - la PRIMA `page.goto()` che muore, non le
    # successive - ma con un altro messaggio: "Navigation ... interrupted by
    # another navigation to about:newtab", e subito dopo, se si aspettava,
    # "can't access property loadURI, browsingContext is undefined". Il gate
    # `fppro_full` falliva 2 volte su 2.
    #
    # ⛔ E LA DIAGNOSI SCRITTA QUI SOPRA ERA SBAGLIATA IN DUE PUNTI, misurati
    # entrambi quel giorno con Playwright puro sullo stesso binario:
    #
    #  1. Non e' la fetch di `TopSitesFeed`. E' il browser PREALLOCATO della
    #     nuova scheda: `JugglerFrameParent` riconosceva il target confrontando
    #     `browserId` con l'`id` di un BrowsingContext - due contatori diversi -
    #     e con browserId 12 contro bcId 12 quel browser estraneo si prendeva il
    #     canale della pagina. Da fuori si vedeva un secondo
    #     `Page.frameAttached mainframe-12` sulla stessa sessione.
    #  2. **Aspettare NON e' il rimedio**, ed era la riga che stava qui. Con
    #     l'attesa vera - non un ritardo fisso: si aspettava che `about:newtab`
    #     fosse arrivato E caricato - la `goto` successiva riusciva **0 volte su
    #     9**, perche' a quel punto il frame che il client crede principale non
    #     esiste sotto il browser della scheda. Il ritardo di 0,4 s che il
    #     wrapper aveva riusciva solo quando VINCEVA LA CORSA, cioe' a caso.
    #
    # Il rimedio sta dove sta la causa: `juggler/JugglerFrameParent.sys.mjs`
    # smentisce il riconoscimento numerico con l'elemento `<browser>` a cui il
    # contesto appartiene. Con quello, e con la preallocazione ACCESA come vuole
    # il proprietario, 10 giri su 10 riusciti e nessun mainframe di troppo.
    # I numeri per esteso in `70-known-bugs.md` [B166].

    # ══════════════════════════════════════════════════════════════════════
    #  LE TRE CHIAVI API, DICHIARATE QUI E IN NESSUN ALTRO POSTO
    # ══════════════════════════════════════════════════════════════════════
    #
    # Decisione del proprietario, 2026-08-20: "voglio portare questi valori su
    # invisible_core, trova il modo che ogni volta che firefox si avvia li legga
    # e li setti prendendoli da invisible_core, come stiamo fondamentalmente
    # facendo per tutte le altre cose che invisible_core genera".
    #
    # PRIMA: erano incise nel binario a tempo di compilazione. `configure`
    # leggeva tre keyfile da $APIKEYDIR e sostituiva `@MOZ_..._API_KEY@` dentro
    # `AppConstants.sys.mjs`, che a runtime e' una costante congelata.
    #
    # ADESSO: quel percorso non esiste piu' - tolte le tre voci di
    # AppConstants.sys.mjs, i tre DEFINES di toolkit/modules/moz.build e le tre
    # --with-*-api-keyfile del .mozconfig - e il solo lettore del motore,
    # `URLFormatter.sys.mjs`, legge queste pref (`stealthDeclaredApiKey`).
    #
    # ⛔ E L'ASSENZA SI RIFIUTA. Senza dichiarazione il motore torna la stringa
    # vuota e registra un errore in console: `checkGoogleSafeBrowsingKey` la
    # legge come falsy e SPEGNE il provider, quindi non parte nessuna richiesta
    # con una chiave finta o con un segnaposto dentro. E' la regola 7: se la
    # dichiarazione manca si rifiuta, non si inventa un default.
    #
    # ⛔ I VALORI SONO QUELLI DI MOZILLA, byte per byte, ed e' deliberato.
    # Il proprietario, 2026-08-20: "devono essere byte per byte identiche a
    # quelle dentro il Firefox scaricato dal loro sito". Sono le stesse chiavi
    # che ogni Firefox retail porta gia' in chiaro dentro `omni.ja`, quindi
    # dichiararle qui non pubblica niente che non sia gia' pubblico - ma resta
    # una scelta, non un dettaglio, ed e' scritta qui perche' si veda.
    #
    # Il dominio e' FINITO E NOTO - tre chiavi - quindi la regola 2 e'
    # soddisfatta e la regola 1 si applica: il core dichiara, il motore obbedisce.
    "zoom.stealth.apikey.google_location_service": "AIzaSyB0mAay6Zu8JTU8XTQtXJLri9eY9wISq6o",
    "zoom.stealth.apikey.google_safebrowsing":     "AIzaSyC7jsptDS3am4tPx4r3nxis7IMjBc5Dovo",
    "zoom.stealth.apikey.mozilla":                 "7e40f68c-7938-4c5d-9f95-e61647c213eb",

    # Disable Firefox internal services that hit the network on startup.
    # Through a residential SOCKS5 proxy these compete with the test
    # navigation and trigger NS_BINDING_FAILED (server-side rate-limit /
    # connection drops). Domains observed in MOZ_LOG: push.services,
    # firefox.settings.services, detectportal, ohttp-gateway, location.
    "browser.aboutConfig.showWarning":                    False,
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
    #: ⛔ `browser.region.network.url` e `browser.region.update.enabled` NON
    #: si emettono piu' - decisione del proprietario, 2026-08-17. Non e' una
    #: dimenticanza: e' il caso in cui sopprimere ALLONTANA dal retail.
    #:
    #: Sono il servizio di REGIONE di Firefox, che non c'entra con la
    #: geolocalizzazione di una pagina - quella la ferma la riga qui sopra,
    #: che resta ed e' portante. Un Firefox standard chiede la regione UNA
    #: volta per sessione, e noi lo facevamo comunque: misurato il
    #: 2026-08-17, il prodotto manda quella richiesta anche con la pref vuota
    #: emessa, mentre un lancio nudo con la stessa pref la azzera. Spedivamo
    #: quindi una dichiarazione che prometteva cio' che non faceva.
    #:
    #: E c'e' una ragione di realness oltre alla somiglianza: la regione
    #: derivata dall'uscita CONCORDA con il fuso e la lingua che dichiariamo
    #: dalla stessa uscita, mentre una regione congelata al default puo'
    #: contraddirli.
    #: ⛔ QUESTA RIGA NON FA NIENTE SULLA NOSTRA BUILD, e resta qui annotata
    #: invece che cancellata perche' la conoscenza costa piu' della riga.
    #:
    #: `services/settings/Utils.sys.mjs` rifiuta l'override del server quando
    #: `AppConstants.RELEASE_OR_BETA` e' vero, salvo test in corso,
    #: `MOZ_REMOTE_SETTINGS_DEVTOOLS=1` nell'ambiente, o un URL gia' nella lista
    #: ammessa. La nostra build impacchettata dichiara `RELEASE_OR_BETA: true` -
    #: viene da `--enable-release` nel mozconfig - e la stringa vuota non e' fra
    #: gli URL ammessi. Quindi `Utils.SERVER_URL` ricade sul ramo `else` e
    #: restituisce il server VERO di Mozilla, e Gecko logga "Ignoring preference
    #: override of remote settings server".
    #:
    #: Conseguenza misurabile: ogni sessione interroga Remote Settings e scarica
    #: gli attachment attraverso il proxy. Vedi `70-known-bugs.md` [B156].
    #:
    #: NON si spegne il poll in blocco: `webcompat-interventions` deve
    #: continuare ad aggiornarsi ed e' FEDELTA'. E non si spegne la revoca dei
    #: certificati per guadagnare memoria - deciso il 2026-08-16: e' un
    #: declassamento di sicurezza su un browser che guidano utenti veri, e non e'
    #: nemmeno invisibile, perche' un rilevatore puo' servire da un host con
    #: certificato revocato e guardare se carichiamo.
    "services.settings.server":                           "",
    "browser.search.geoSpecificDefaults":                 False,
    "browser.contentblocking.report.lockwise.enabled":    False,
    "browser.contentblocking.report.monitor.enabled":     False,
    "dom.private-attribution.submission.enabled":         False,

    "browser.translations.enable":                        False,

    # ⛔ QUI STAVANO SEI PREF CHE SPEGNEVANO HTTP/3, Alt-Svc, ECH e i record
    # DNS HTTPS. TOLTE IL 2026-08-25: erano un segnale SOPPRESSO, e la ragione
    # scritta accanto non reggeva alla misura.
    #
    # Il commento diceva: "SOCKS5 proxy doesn't support UDP ASSOCIATE so HTTP/3
    # fails". Vero dietro un proxy - misurato: il gateway di un provider
    # residenziale rifiuta `UDP ASSOCIATE` con `rep=7` su 8 peer su 8. Ma le
    # pref erano INCONDIZIONATE, e senza proxy il proxy non c'entra niente.
    #
    # Misurato leggendo `performance.getEntriesByType('navigation')[0]
    # .nextHopProtocol`, che e' il protocollo VERO della connessione, detto dal
    # browser stesso:
    #
    #   senza proxy, come spedivamo   cloudflare-quic.com:  h2 -> h2 -> h2
    #   senza proxy, pref tolte       cloudflare-quic.com:  h2 -> **h3** -> h3
    #
    # Cioe' eravamo l'unico browser sulla connessione a non parlare MAI HTTP/3,
    # e Scrapfly lo stampa in chiaro (`http3_supported: false`). Un Firefox
    # retail su una connessione di casa lo usa.
    #
    # **E dietro proxy non serve nessuna condizione**, che e' la parte che
    # rende il rimedio semplice: con le pref al default del motore e un proxy
    # configurato, Firefox **si astiene da solo**. Misurato su entrambi gli
    # schemi, con e senza le pref, quattro corse: sempre `h2 -> h2 -> h2`, con
    # gli stessi tempi (22-24 s). Non ci prova e fallisce: semplicemente non usa
    # QUIC quando c'e' un proxy, esattamente come farebbe il retail.
    #
    # Il criterio, dettato dal proprietario lo stesso giorno: **si spegne una
    # cosa solo se e' davvero indisponibile E non la si puo' falsificare.** Qui
    # non e' indisponibile (senza proxy funziona) e dietro proxy se ne occupa il
    # motore, quindi non c'e' niente da spegnere.
    #
    # `echconfig` e `use_https_rr_as_altsvc` valgono `true` nel motore e sono
    # uscite con le altre: ECH cambia il ClientHello, cioe' proprio cio' che
    # JA3/JA4 misurano, e un HTTPS RR in meno e' una via di scoperta che il
    # retail ha e noi no.

    # Le connessioni speculative RESTANO spente, e la ragione e' diversa: sotto
    # carico producono un annullamento anticipato del canale
    # (NS_BINDING_FAILED). E' un rimedio a un guasto osservato, non una difesa
    # di fingerprint - e non e' stato rimisurato, quindi non si tocca.
    "network.predictor.enabled":                          False,
    "network.dns.disablePrefetch":                        True,
    "network.dns.disablePrefetchFromHTTPS":               True,

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

    # ------------------------------------------------------------------
    # ⛔ QUI NON C'E' NIENTE, ED E' UNA DECISIONE - non una dimenticanza.
    # Proprietario, 2026-08-19: il prodotto deve fare esattamente cio' che fa
    # un Firefox retail, quindi le prefs che sopprimevano traffico che il
    # retail fa sono state TOLTE, non impostate al valore del retail. Togliere
    # eredita il default di Gecko, che e' per definizione quello del retail;
    # impostare lascia un valore nostro che puo' divergere quando upstream
    # cambia idea.
    #
    # Tolte qui: browser.safebrowsing.{malware,phishing,downloads,
    # downloads.remote}.enabled, toolkit.telemetry.{enabled,unified},
    # datareporting.{healthreport.uploadEnabled,policy.dataSubmissionEnabled},
    # app.{update.enabled,normandy.enabled,shield.optoutstudies.enabled},
    # extensions.{update,systemAddon.update,getAddons.cache}.enabled,
    # browser.{discovery.enabled,ping-centre.telemetry,search.update},
    # network.{captive-portal-service,connectivity-service}.enabled,
    # dom.push.connection.enabled, network.http.speculative-parallel-limit.
    #
    # ⛔ E NON VANNO RIDICHIARATE "per sicurezza" al valore del retail: sul
    # percorso Juggler - quello che usa il prodotto - PLAYWRIGHT NON SCRIVE
    # NESSUNA PREF. Il blocco delle ~86 che si trova cercando in giro vive in
    # `bidi/bidiFirefox.ts` e si raggiunge solo con `channel` che inizia per
    # `moz-`, che noi non passiamo mai; la classe base ha `prepareUserDataDir`
    # col corpo VUOTO. Misurato: un profilo Playwright nudo ha 48 prefs, tutte
    # e 48 presenti anche nel profilo del retail firmato, e nessun `user.js`.
    #
    # `app.update.enabled` non e' fra queste per scelta: e' una pref MORTA,
    # rimossa da Firefox (UpdateTelemetry.sys.mjs:54). La impostavamo a vuoto.
    # Cio' che impedisce davvero l'installazione e' `app.update.auto`, che
    # resta a False qui sotto: il retail CONTROLLA e noi pure, ma non
    # scarichiamo e non installiamo, perche' installare distruggerebbe il
    # binario pinnato dal sigillo.
    #
    # Contesto completo: docs/firefox-stealth-architecture/27-retail-network-parity.md
    # ------------------------------------------------------------------

    # ⛔ L'UNICA ECCEZIONE ALLA PARITA', E RESTA FINCHE' LA BUILD NON HA LA CHIAVE.
    # Misurato nel sorgente 2026-08-19, catena completa:
    #   browser.safebrowsing.downloads.remote.url =
    #     https://sb-ssl.google.com/safebrowsing/clientreport/download
    #       ?key=%GOOGLE_SAFEBROWSING_API_KEY%          (all.js:3448)
    #   letta via FormatURLPref                          (ApplicationReputation.cpp:1603-1606)
    #     -> la sentinella `no-google-safebrowsing-api-key` finisce NELLA URL
    #   SendRemoteQueryInternal rifiuta solo URL vuoto o about:blank: la chiave
    #     non la controlla mai.
    # Quindi con questa pref ereditata a `true` e senza chiave vera, OGNI
    # download binario manda a Google una POST con la sentinella dentro la
    # query. Nessun retail emette quella stringa: non e' un'assenza, e' un
    # segnale positivo che ci dichiara build senza chiave, verso il destinatario
    # che stiamo cercando di non insospettire.
    #
    # Il percorso degli AGGIORNAMENTI LISTA e' protetto e non ha questo problema:
    # checkGoogleSafeBrowsingKey azzera updateURL e gethashURL quando la chiave
    # manca (SafeBrowsing.sys.mjs:537-568, :637-643). Solo la reputazione dei
    # download scavalca il controllo.
    #
    # ⛔ SI TOGLIE QUANDO `--with-google-safebrowsing-api-keyfile` e' in vigore
    # nella build: da quel momento questa riga diventa essa stessa la divergenza.
    # Il gancio condizionale e' gia' nel .mozconfig di firefox-21.
    "browser.safebrowsing.downloads.remote.enabled":      False,

    # Update channels.
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

    # The language `getTranslatedShaderSource` answers the page in.
    #
    # We declare Windows on every host, and a Windows Firefox reaches ANGLE,
    # which presents itself as GLES, so its translator emits ESSL. On a host
    # whose GL context is NOT GLES - Linux through GLX - the same translator
    # emits desktop GLSL, and the page reads `#version 450` from a browser whose
    # RENDERER says ANGLE. ANGLE never emits desktop GLSL, so the contradiction
    # is INTERNAL to one page: it does not need to know which OS we run on.
    #
    # What is declared is the LANGUAGE, never the string. The string's domain is
    # infinite (the shader is arbitrary) so a table is forbidden; the language's
    # domain has one element. The engine keeps translating, with the same
    # declared resources, which is why the answer matches a Windows build by
    # construction instead of by measured coincidence.
    #
    # A Windows build reads this pref, finds the language it already uses, and
    # does nothing - the second translation is paid only where the defect is.
    "zoom.stealth.webgl.shader_output_language":          "essl",

    # ⛔ `zoom.stealth.text.*` e non `zoom.stealth.font.*`, e la distinzione
    # non e' estetica: `test_fonts_are_not_configured_via_prefs` nel wrapper
    # pretende che NESSUNA pref `zoom.stealth.font.` sia emessa, perche' il
    # binario e' autosufficiente per i font e la loro configurazione vive
    # nel manifest. Questi due non configurano un font: dicono come si
    # RASTERIZZA, e quello spazio esiste gia' - `zoom.stealth.text.
    # coverage_ladder` sta li'. Il test lo ha trovato lo stesso giorno in
    # cui erano stati messi nello spazio sbagliato, prima che spedissero.
    # How FreeType loads a glyph: the hinting style, and whether antialiasing
    # is on. DECLARED, never asked of the host.
    #
    # On Linux the engine built an FcPattern and read these back from it, and
    # that pattern has had "user and system fontconfig configurations" applied
    # to it - upstream's own words, in the comment right above the code that
    # read them. So `/etc/fonts` decided how OUR bundled faces were
    # rasterised. Measured 2026-08-16 THROUGH THE PRODUCT, same binary and
    # same page: the pixel hash was 3842037683 with this machine's fontconfig,
    # 1512591551 with an empty one, 2489286717 with `hintnone`. A page reads
    # those bytes with getImageData on a canvas with text.
    #
    # Of the six parameters that function took from the pattern these are the
    # only two that move a pixel: rgba, lcdfilter and embeddedbitmap measured
    # identical in every configuration tried, so they stay where they are -
    # declaring them would be code that moves no measurement.
    #
    # 1 = FC_HINT_SLIGHT, the fontconfig constant rather than a numbering of
    # our own, so nothing translates in between. It is what DirectWrite's
    # CLEARTYPE_NATURAL_SYMMETRIC - the rendering mode declared for Windows a
    # few lines above - does on the other side: little grid fitting, the
    # outline kept. Windows never reads these two: the declaration is ONE, and
    # each engine reads the half that concerns it.
    "zoom.stealth.text.freetype_hintstyle":               1,
    "zoom.stealth.text.freetype_antialias":               1,

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

#: La tabella con cui Firefox costruisce `intl.accept_languages` di default,
#: portata da `intl/locale/rust/locale_service_glue/src/lib.rs:89-222`
#: (`locale_service_default_accept_languages`). Chiave = codice di LINGUA, non
#: il locale intero.
_ACCEPT_LANG_TABLE = {
    "ace": "ace, id", "ach": "ach, en-GB", "af": "af, en-ZA, en-GB",
    "ak": "ak, ak-GH", "an": "an, es-ES, es, ca", "ast": "ast, es-ES, es",
    "az": "az-AZ, az", "bo": "bo-CN, bo-IN, bo", "br": "br, fr-FR, fr",
    "brx": "brx, as", "bs": "bs-BA, bs", "cak": "cak, kaq, es",
    "crh": "tr-TR, tr", "cs": "cs, sk", "csb": "csb, csb-PL, pl",
    "cy": "cy-GB, cy", "dsb": "dsb, hsb, de", "el": "el-GR, el",
    "et": "et, et-EE", "fa": "fa-IR, fa", "ff": "ff, fr-FR, fr, en-GB",
    "fi": "fi-FI, fi", "fr": "fr, fr-FR", "frp": "frp, fr-FR, fr",
    "fur": "fur-IT, fur, it-IT, it", "fy": "fy-NL, fy, nl",
    "ga": "ga-IE, ga, en-IE, en-GB", "gd": "gd-GB, gd, en-GB",
    "gl": "gl-ES, gl", "gn": "gn, es", "gv": "gv, en-GB", "he": "he, he-IL",
    "hr": "hr, hr-HR", "hsb": "hsb, dsb, de",
    "hto": "es-MX, es-ES, es, es-AR, es-CL", "hu": "hu-HU, hu",
    "hye": "hye, hy", "ilo": "ilo-PH, ilo", "it": "it-IT, it",
    "ixl": "ixl, es-MX, es", "ja": "ja", "ka": "ka-GE, ka",
    "kab": "kab-DZ, kab, fr-FR, fr", "kk": "kk, ru, ru-RU", "kn": "kn-IN, kn",
    "ko": "ko-KR, ko", "lb": "lb, de-DE, de", "lg": "lg, en-GB",
    "lij": "lij, it", "lt": "lt, ru, pl", "ltg": "ltg, lv",
    "mai": "mai, hi-IN, en", "meh": "meh, es-MX, es", "mix": "mix, es-MX, es",
    "mk": "mk-MK, mk", "ml": "ml-IN, ml", "mr": "mr-IN, mr",
    "nb": "nb-NO, nb, no-NO, no, nn-NO, nn",
    "nn": "nn-NO, nn, no-NO, no, nb-NO, nb", "nr": "nr-ZA, nr, en-ZA, en-GB",
    "nso": "nso-ZA, nso, en-ZA, en-GB", "oc": "oc, ca, fr, es, it",
    "pa": "pa, pa-IN", "ppl": "ppl, es-MX, es", "rm": "rm, rm-CH, de-CH, de",
    "ru": "ru-RU, ru", "sah": "sah, ru-RU, ru", "sc": "sc, it-IT, it",
    "scn": "scn, it-IT, it", "si": "si-LK, si", "sk": "sk, cs",
    "son": "son, son-ML, fr", "sq": "sq, sq-AL", "sr": "sr-RS, sr",
    "st": "st-ZA, st, en-ZA, en-GB", "ta": "ta-IN, ta", "te": "te-IN, te",
    "tl": "tl-PH, tl", "tr": "tr-TR, tr", "trs": "trs, es-MX, es",
    "ts": "ts-ZA, ts, en-ZA, en-GB", "uk": "uk-UA, uk", "ur": "ur-PK, ur",
    "uz": "uz, ru", "ve": "ve-ZA, ve, en-ZA, en-GB", "vi": "vi-VN, vi",
    "xcl": "xcl, hy", "xh": "xh-ZA, xh", "zam": "zam, es-MX, es",
}

#: Le SEI lingue in cui Firefox NON aggiunge ", en-US, en" in coda
#: (`add_en_us = false` nel sorgente citato sopra). Tutte le altre lo aggiungono.
_ACCEPT_LANG_SENZA_EN = {
    "en": None,          # la tabella per `en` dipende dalla regione, vedi sotto
    "my": "my, en-GB, en",
    "ro": "ro-RO, ro-GB, en",
    "sco": "sco, en-GB, en",
    "sl": "sl, en-GB, en",
    "szl": "szl, pl-PL, pl, en, de",
}


def _accept_language(locale: str) -> str:
    """`intl.accept_languages` esattamente come lo costruisce Firefox 151.

    ⛔ QUESTA FUNZIONE RESTITUIVA DUE VOCI PER OGNI LOCALE, E PER 89 LINGUE SU 95
    ERA SBAGLIATO. Restituiva `"<locale>, <base>"` con il commento "la forma di
    default del desktop (es. `en-US, en`)". Quella forma e' giusta **solo per
    l'inglese**, che e' una delle sei lingue in cui Firefox non aggiunge la coda.

    Il sorgente e' `locale_service_default_accept_languages`
    (`intl/locale/rust/locale_service_glue/src/lib.rs:82-234`) e fa due cose:
    una TABELLA di casi speciali per lingua, e poi

        if add_en_us { format!("{langs}, en-US, en") } else { langs }

    con `add_en_us` VERO di default e falso solo per en, my, ro, sco, sl, szl.
    Il ramo di default della tabella e' `"{lang}-{region}, {lang}"`, cioe'
    esattamente la nostra vecchia formula: mancava solo la coda, che e' il pezzo
    che si vede.

    Cosa costava, misurato: un profilo italiano emetteva
        it-IT,it;q=0.9
    dove un Firefox italiano vero emette
        it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7
    su OGNI richiesta HTTP e in `navigator.languages`.

    ⛔ E PERCHE' NON ERA STATO VISTO: il confronto col retail era stato fatto
    contro una build **en-US**, cioe' l'unico caso in cui la vecchia formula e
    quella vera coincidono per costruzione. Il braccio di controllo era scelto
    male, non la misura sbagliata. Quando si confronta una funzione che dipende
    da un parametro, il braccio non puo' stare sul valore in cui il difetto si
    annulla.
    """
    lang = locale.replace("_", "-")
    pezzi = lang.split("-")
    base = pezzi[0]
    regione = pezzi[1] if len(pezzi) > 1 else None

    if base == "en":
        # unico ramo con sotto-casi per regione, e senza coda
        return {"CA": "en-CA, en-US, en", "GB": "en-GB, en",
                "ZA": "en-ZA, en-GB, en-US, en"}.get(regione, "en-US, en")
    if base in _ACCEPT_LANG_SENZA_EN:
        return _ACCEPT_LANG_SENZA_EN[base]

    if base == "ca" and "valencia" in lang.lower():
        langs = "ca-valencia, ca"
    elif base == "zh" and regione == "CN":
        langs = "zh-CN, zh, zh-TW, zh-HK"
    elif base in _ACCEPT_LANG_TABLE:
        langs = _ACCEPT_LANG_TABLE[base]
    elif regione:
        langs = f"{base}-{regione}, {base}"
    else:
        langs = base
    return f"{langs}, en-US, en"


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
    # levers covers all four answers.
    #
    # [CORRECTED 2026-08-09] This declared 0 (BEHAVIOR_ACCEPT, accept
    # everything). A shipped Firefox runs at **5** - dFPI, total cookie
    # protection - which firefox.js sets over the 0 that StaticPrefList.yaml
    # carries as the raw default. So reading the yaml and declaring 0 looked
    # like "the Firefox default" and was the opposite of it.
    #
    # It was found from the wire, not from the source. Our subresource requests
    # put `Referer` before `Connection` where stock puts `Connection` first -
    # stable over three runs a side, and header order is one of the most read
    # HTTP fingerprints there is. Bisected: not our netwerk patches (the bare
    # binary matches stock), not our Juggler (our binary under plain Playwright
    # matches stock), not the locale header override - the wrapper's PREF SET.
    # Then a binary search over all 219 prefs landed on this one.
    #
    # The header order is the smaller half of the damage. BEHAVIOR_ACCEPT turns
    # OFF the third-party cookie partitioning that every current Firefox has,
    # and a page can test for partitioning directly. We were declaring a
    # browser with its most distinctive protection disabled.
    #
    # 5 keeps the four booleans true - measured on stock 151 at its own default,
    # which reports cookieEnabled true and a working localStorage without
    # anybody setting anything.
    _storage = bool(profile.hardware.storage_enabled)
    prefs["network.cookie.cookieBehavior"]    = 5 if _storage else 2
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
    # La quarta della famiglia, aggiunta il 2026-08-24. Governa `forced-colors`
    # e, di rimbalzo, `prefers-contrast`: quest'ultimo Gecko lo DERIVA dal
    # rapporto di contrasto dei colori effettivi e non ha nessun override, ma il
    # primo termine del suo if e' proprio questo flag.
    #
    # Era rimasta indietro perche' Playwright mandava `Browser.setForcedColors`
    # a ogni lancio e l'override cortocircuitava la lettura: la pref non serviva
    # a niente e nessuno si accorgeva che mancasse. Tolto quel comando, senza
    # questa riga la decisione non sarebbe tornata qui - sarebbe andata
    # all'HOST, via LookAndFeel::GetInt(IntID::UseAccessibilityTheme).
    prefs["ui.useAccessibilityTheme"]         = _a11y


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
    # The Thai minimum size, and it is the ONLY one of the 28 language groups
    # that Firefox gives an OS-conditional default: all.js sets 10 inside the
    # XP_WIN block and 13 inside the Unix desktop block. Gecko clamps the
    # effective size before layout, so identical CSS lays out differently.
    #
    # Measured 2026-08-09 with `<span lang="th" style="font-size:5px">`, reading
    # getBoundingClientRect().width: stock 151 and our Windows both answer
    # 42.067 for every size at or below 10, and our Linux answers 54.683 - the
    # width it gives at 13. One element, one property, and the answer names the
    # operating system.
    #
    # 10 is the Windows value, which is what the persona claims. Declared for
    # both platforms, so the correction lands on Linux (rule 4).
    prefs["font.minimum-size.th"] = 10
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
    # La stessa cosa detta all'altro lettore. `ui.systemUsesDarkTheme` la
    # legge LookAndFeel; `prefers-color-scheme` no, perche' nsPresContext
    # guarda prima l'override del BrowsingContext e poi questa pref.
    # 0 = Dark, 1 = Light (StaticPrefList.yaml:10646-10648); il default e' 2,
    # che vuol dire "il sistema", cioe' l'host.
    #
    # Dichiarata il 2026-08-24, quando `Browser.setColorScheme` e' stato tolto
    # dal client. Va in quest'ordine: prima la dichiarazione, poi la rimozione
    # del comando, altrimenti la decisione non torna qui - va alla macchina.
    # Misurato prima: col comando attivo questa pref era codice morto, messa a
    # 0 il browser continuava a rispondere light.
    prefs["layout.css.prefers-color-scheme.content-override"] = (
        0 if profile.dark_theme else 1)
    # ── Three LookAndFeel values that still read the HOST ────────────────────
    #
    # `ui.textScaleFactor` is the one that matters, and it is not a theme
    # setting at all - it MULTIPLIES the device pixel ratio we pin. This file
    # sets `layout.css.devPixelsPerPx` and a comment beside it says that is
    # where the DPR a page sees comes from; it is not the whole story.
    # `widget/Screen.cpp` GetCSSToLayoutDeviceScale and nsIBaseWindow's
    # UnscaledDevicePixelsPerCSSPixel both take that pinned scale and multiply
    # it by LookAndFeel::SystemZoomSettings().mFullZoom, which equals the text
    # scale factor whenever `browser.display.os-zoom-behavior` is 1 - and 1 is
    # its default. On Windows that factor is the live "Make text bigger"
    # accessibility slider read through IUISettings2; on Linux it is the GTK
    # Xft DPI divided by 96. So a user with the slider at 125% shipped a
    # devicePixelRatio nobody declared, readable with `window.devicePixelRatio`
    # or one `matchMedia('(resolution: ...)')`.
    #
    # 1.0 is what a machine at the default setting reports, which is what the
    # personas claim. Both development machines are at the default, so this
    # agreed cross-OS by coincidence and no comparison could have caught it -
    # the same shape as the colour depth, which read the real panel for months
    # while both machines happened to be 24-bit.
    #
    # A STRING, and the quotes are the whole bug fixed on 2026-08-10. Gecko has
    # no float pref type: `Preferences::GetFloat` reads a CHAR pref and parses
    # the text, which is why `layout.css.devPixelsPerPx` five hundred lines up
    # is `str(...)` and why FONT_UI_SIZE carries the note "string:
    # Preferences::GetFloat parses text". Written as a Python float it reached
    # the profile as a number pref, so the name existed with the wrong TYPE and
    # the read failed - and a failed read here does not fall back quietly.
    #
    # What it cost, measured: opening a page in a SECOND browser context killed
    # the browser on Windows. The first context was fine, so nothing in the
    # normal path showed it; the e2e suite wedged at 83% for twenty-two minutes
    # instead of failing. Isolated by a binary search over the 230 prefs the
    # wrapper composes - 14 launches - after the engine, the window cloak, the
    # context kwargs, the environment, the cursor layer and the process job
    # object had each been excluded by their own measurement.
    prefs["ui.textScaleFactor"] = "1.0"
    #: 0 = let the locale decide, which is what Windows does. On Linux this
    #: IntID reads GNOME's `org.gnome.desktop.interface clock-format` live and
    #: OSPreferences_gtk overrides the ICU pattern with it, so a host set to
    #: 24h changed `Intl.DateTimeFormat().resolvedOptions().hourCycle` and every
    #: `toLocaleTimeString()`. Windows has no case for this IntID at all, so
    #: declaring 0 is what makes the two behave the same way.
    prefs["ui.hourCycle"] = 0
    #: Windows hardcodes 1 (select the whole field when tabbing into it); GTK
    #: reads the live `gtk-entry-select-on-focus` setting, which a user can turn
    #: off. A page reads the difference with `input.selectionStart` after
    #: focusing a pre-filled field.
    prefs["ui.selectTextfieldsOnKeyFocus"] = 1
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
    # it with the full Accept-Language list; the C++ DidSet takes the primary tag
    # for Intl. Mirrors juggler.timezone.override; the SOLE source of truth.
    #
    # A sentence here used to promise this keeps navigator.languages at 'the
    # desktop-default 2 elements'. THAT IS WRONG. It was written when
    # _accept_language() still returned 2 tags for every non-English locale,
    # which was itself the defect corrected on 2026-08-19. Firefox DERIVES
    # navigator.languages from this list, so the count is whatever the list
    # holds, and looking like retail means matching the list, not a number.
    #
    # Measured 2026-08-19 against a signed retail 151.0 (judge151-frozen, the
    # updater frozen) handed the same intl.accept_languages, same page served
    # from 127.0.0.1, and read on the product path (a context WITH locale=):
    #   retail it-IT  navigator.languages  it-IT, it, en-US, en   -> FOUR
    #   ours   it-IT  navigator.languages  identical
    #   retail it-IT  Accept-Language      it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7
    #   ours   it-IT  Accept-Language      identical
    #
    # en-US is the single locale where 2 and 4 coincide (the table has no 'en'
    # row to append), which is why an en-US-only control kept the old sentence
    # looking true for as long as it did.
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


#: Millisecondi fra due `mousemove` consecutivi, MEDIA di una gaussiana (il
#: passo fisso era di per se' un tell, e il motore lo sfuma gia').
#:
#: Il valore non e' scelto a gusto: e' quello che il generatore Python del
#: wrapper - il percorso PREDEFINITO, quindi il riferimento - produce davvero.
#: Misurato il 2026-08-24 sulla stessa mossa, leggendo i `dt` dalla pagina:
#: media 31,9 ms, mediana 31,5, minimo 16, e solo il 5% sotto i 16,7 ms.
#:
#: Il motore del binario, che e' il RIPIEGO quando il generatore Python manca,
#: girava invece sul default compilato di 10 ms e dava media 14,2 ms con minimo
#: 2 ms: **79 dt su 115 piu' fitti di quanto un 60 Hz reale possa consegnare**,
#: perche' Firefox unisce i mousemove al ritmo di refresh. Due percorsi per la
#: stessa cosa che si comportavano in modo diverso, e il piu' veloce era quello
#: che nessun hardware puo' produrre.
#:
#: INTERO per forza: Juggler la legge con `getIntPref`, e una pref del tipo
#: sbagliato arriva col nome giusto e non viene letta.
HUMANIZE_STEP_MS = 32


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
        # Dichiarata qui e non lasciata al default compilato nel motore: era
        # l'unica dei tre fratelli `stealthfox.humanize*` che il core non
        # nominava, quindi la decideva il binario da solo.
        "stealthfox.humanize.stepMs": HUMANIZE_STEP_MS,
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
    delegates_auth: bool = True,
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
    # delegates_auth passa oltre senza essere interpretato qui: chi lancia sa se
    # ha un Playwright a cui dare un endpoint HTTP, questo composer no.
    playwright_proxy = (configure_proxy(proxy, prefs, delegates_auth=delegates_auth)
                        if proxy else None)

    # ⛔ UNCONDITIONAL, and the reason is REALNESS - not the launch bug it also
    # happens to fix. Windows' occlusion tracker can decide our chrome window is
    # occluded, and from that verdict the browser is treated as BACKGROUNDED. What
    # a page then reads, with no stealth override anywhere:
    #
    #   * `document.visibilityState === "hidden"` and `document.hidden === true`,
    #     plus a real `visibilitychange` event (`Document::ComputeVisibilityState`)
    #   * `requestAnimationFrame` throttled to 1 Hz (`layout.throttled_frame_rate`)
    #   * `setTimeout`/`setInterval` clamped to 1000 ms
    #     (`dom.min_background_timeout_value`)
    #   * `navigator.mediaDevices.enumerateDevices()` that NEVER RESOLVES
    #     (`MediaDevices.cpp` returns early when the BrowsingContext is inactive)
    #   * video suspended: `totalVideoFrames` frozen while `currentTime` advances
    #
    # Every one of those is a SUPPRESSED signal on a surface a detector reads, and
    # rule 12 says a suppressed signal is a FAIL, not a pass. An automated browser
    # must never be treated as backgrounded: nobody is looking at it, but the page
    # is.
    #
    # This lived in `CLOAK_PREFS` until 2026-08-14, i.e. applied only when
    # `cloak=True`, which requires `headless=True` - so the DEFAULT headful path
    # ran with the tracker on. Measured that day on the persistent-relaunch path:
    # 14 relaunches out of 14 with this pref against 6 hangs out of 9 without.
    # The hang's own mechanism is NOT established (`70-known-bugs.md` [B150]);
    # this pref is justified by the observable list above, which does not depend
    # on it.
    #
    # setdefault, so an explicit caller override still wins.
    prefs.setdefault("widget.windows.window_occlusion_tracking.enabled", False)

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
