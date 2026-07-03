"""invisible_core — pure config for a patched Firefox stealth profile.

Zero Playwright dependency: seed -> fingerprint profile -> Firefox prefs,
binary download, proxy config, geo/timezone resolution. This is the shared
foundation used by both:
  - invisible_playwright (the Playwright automation wrapper), and
  - invisible_manager (the antidetect profile manager, launches the binary directly).

Quickstart:

    from invisible_core import generate_profile, translate_profile_to_prefs, ensure_binary

    profile = generate_profile(seed=42)
    prefs   = translate_profile_to_prefs(profile)   # dict for firefox user prefs
    binary  = ensure_binary()                        # path to the patched Firefox
"""
from ._fpforge import (
    AudioProfile,
    CodecProfile,
    GPUProfile,
    HardwareProfile,
    Profile,
    ScreenProfile,
    WebGLProfile,
    generate_profile,
)
from ._webgl_personas import forced_gpu_class, select_persona, render_noise_seed
from .prefs import translate_profile_to_prefs
from .download import ensure_binary, ensure_geoip_mmdb
from ._geo import (
    GeoTimezoneError,
    discover_egress_ip,
    ip_to_timezone,
    prepare_session_geo,
    resolve_session_locale,
    resolve_session_timezone,
)
from ._headless import cloak_prefs, make_virtual_display
from ._proxy import configure_proxy
from .config import get_default_args, get_default_stealth_prefs
from .constants import BINARY_VERSION, FIREFOX_UPSTREAM_VERSION

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("invisible-core")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    # fingerprint generation
    "generate_profile",
    "Profile",
    "GPUProfile",
    "ScreenProfile",
    "HardwareProfile",
    "AudioProfile",
    "CodecProfile",
    "WebGLProfile",
    # webgl personas
    "forced_gpu_class",
    "select_persona",
    "render_noise_seed",
    # prefs
    "translate_profile_to_prefs",
    "get_default_stealth_prefs",
    "get_default_args",
    # binary + geoip
    "ensure_binary",
    "ensure_geoip_mmdb",
    # geo / timezone
    "resolve_session_timezone",
    "resolve_session_locale",
    "prepare_session_geo",
    "discover_egress_ip",
    "ip_to_timezone",
    "GeoTimezoneError",
    # proxy + headless helpers
    "configure_proxy",
    "cloak_prefs",
    "make_virtual_display",
    # constants
    "BINARY_VERSION",
    "FIREFOX_UPSTREAM_VERSION",
    "__version__",
]
