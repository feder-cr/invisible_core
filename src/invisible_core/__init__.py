"""invisible_core - pure config for a patched Firefox stealth profile.

Zero Playwright dependency: seed -> fingerprint profile -> Firefox prefs,
binary download, proxy config, geo/timezone resolution. This is the shared
foundation used by both:
  - invisible_playwright (the Playwright automation wrapper), and
  - invisible_firefox (the antidetect profile manager, launches the binary directly).

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
from .prefs import (
    ComposedPrefs,
    compose_session_prefs,
    humanize_prefs,
    translate_profile_to_prefs,
)
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
# The engine identity. Both consumers import these three from `invisible_core.seal`
# already; naming them here makes the package's declared surface match what it
# actually promises - `seal` has no underscore, so it reads as public, and
# nothing in __all__ said so.
from .seal import EngineMismatch, active_seal, verify_engine
from .launch import tz_env, _IANA_TO_POSIX_TZ as IANA_TO_POSIX_TZ, LaunchPlan, build_launch_env, build_launch_plan, write_user_js

# One headline version, and it is the honest one.
#
# `__version__` is DERIVED from the seal this package ships (invisible_core/
# seal.json -> _version.py), so it describes the code that is about to run. That
# is the number to put in a bug report, and the number both consumers pin with
# `invisible-core==`.
#
# `__install_record_version__` is a different fact: what the installer wrote into
# the .dist-info when the distribution was put there. It can disagree - measured
# in this tree: record 0.1.0, files 18.0.0 - and when it does, the record is the
# stale one while the files are what executes. It is kept because `pip`, `pip
# check` and the doctor's "STALE RECORD" line all read it, and diagnosing that
# skew needs both numbers. It is named so it cannot be mistaken for the version.
from ._version import __version__

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __install_record_version__ = _pkg_version("invisible-core")
except PackageNotFoundError:
    # Running from a source checkout with no install record at all.
    __install_record_version__ = ""

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
    "compose_session_prefs",
    "ComposedPrefs",
    "humanize_prefs",
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
    # direct-launch helpers (shared by the wrapper + the profile manager)
    "build_launch_plan",
    "LaunchPlan",
    "build_launch_env",
    "tz_env",
    "EngineMismatch",
    "active_seal",
    "verify_engine",
    "IANA_TO_POSIX_TZ",
    "write_user_js",
    # constants
    "BINARY_VERSION",
    "FIREFOX_UPSTREAM_VERSION",
    "__version__",
    "__install_record_version__",
]
