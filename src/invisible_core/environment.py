"""Every environment variable the library reads, in one place.

A value in the caller's environment changes what the library does: where the
engine store is, which seal is trusted, whether the pin repair runs, which IP
WebRTC announces. Who could see that list used to depend on who had written it
down: `install_job.LEAKS` named half of these variables by hand, and the test
suite named none, so a developer with `INVISIBLE_PLAYWRIGHT_ENGINE_DIR` in their
shell got 13 tests red that were green everywhere else (measured 2026-10-19,
on the CI of PR #160). Now the modules that read a variable take its name from
here, and `tests/test_environment.py` refuses a name written anywhere else in
the package, so the list cannot be short of one.

Two kinds, one list: `SETTINGS` are set by a person to change the library;
`HANDOFFS` are set by the library itself for a process it starts (or for its
own next attempt) and read back there. Both reach the library from the caller's
environment, so both are in `READ`. The names are part of the contract with
published clients and with the engine: they are never renamed.

Not here: the pre-push gate's own variables (`hooks.py`) and the test harness's
(`testing/`), which the gate and the harness set on purpose.

This module imports nothing from the package, so any module can import it.
"""
from __future__ import annotations

# --- settings: what a person sets ------------------------------------------

#: Where sealed engine trees live (`download.engine_root`).
ENGINE_DIR = "INVISIBLE_PLAYWRIGHT_ENGINE_DIR"
#: The machine's writable state, and the engine store by default (`download.cache_root`).
CACHE_DIR = "INVISIBLE_PLAYWRIGHT_CACHE_DIR"
#: How long a download may take (`download.download_deadline`).
DOWNLOAD_DEADLINE = "INVISIBLE_DOWNLOAD_DEADLINE"
#: A token for the GitHub API; ours first, then the generic one (`download`).
GITHUB_TOKEN_OWN = "STEALTHFOX_GITHUB_TOKEN"
GITHUB_TOKEN = "GITHUB_TOKEN"
#: A seal file instead of the packaged one (`seal`).
SEAL_FILE = "INVISIBLE_SEAL_FILE"
#: "allow" lets a mismatched pair run with a warning (`pin`).
CORE_PIN = "INVISIBLE_CORE_PIN"
#: The pin repair switched off (`pin`).
CORE_AUTOFIX = "INVISIBLE_CORE_AUTOFIX"
#: How long the pin repair's install may take (`pin`).
CORE_AUTOFIX_TIMEOUT = "INVISIBLE_CORE_AUTOFIX_TIMEOUT"
#: Which uv the installer runs (`installer`).
UV = "UV"
#: A local GeoIP database instead of the downloaded one (`_geoip_db`).
GEOIP_MMDB = "STEALTHFOX_GEOIP_MMDB"
#: The font manifest the engine loads; a value already set wins (`launch`).
FONT_MANIFEST = "STEALTHFOX_FONT_MANIFEST"
#: The host network a session declares, `v4=<address>;v6=<0|1>`; a value already set wins (`launch`).
WEBRTC_NETWORK = "STEALTHFOX_WEBRTC_NETWORK"
#: "off" leaves the system timer resolution alone (`juggler._pacing`).
CURSOR_TIMER = "INVPW_CURSOR_TIMER"

# --- handoffs: what the library sets for a process it starts ----------------

#: Set before a pin repair, so a repaired process does not start another (`pin`).
CORE_AUTOFIX_ATTEMPTED = "INVISIBLE_CORE_AUTOFIX_ATTEMPTED"
#: The session a process belongs to, for the reaper (`process`).
SESSION_TOKEN = "INVPW_SESSION_TOKEN"
#: The hidden desktop the spawner starts the browser on (`_headless`).
DESKTOP = "INVPW_DESKTOP"

SETTINGS = (ENGINE_DIR, CACHE_DIR, DOWNLOAD_DEADLINE, GITHUB_TOKEN_OWN,
            GITHUB_TOKEN, SEAL_FILE, CORE_PIN, CORE_AUTOFIX,
            CORE_AUTOFIX_TIMEOUT, UV, GEOIP_MMDB, FONT_MANIFEST,
            WEBRTC_NETWORK, CURSOR_TIMER)
HANDOFFS = (CORE_AUTOFIX_ATTEMPTED, SESSION_TOKEN, DESKTOP)
#: Everything the library reads from the environment.
READ = SETTINGS + HANDOFFS

# --- the engine's MOZ_* variables: what passes, and nothing else -------------
#
# The engine reads hundreds of `MOZ_*` variables, and a good part of them change
# what a page can read: whether ICE runs in the socket process
# (`MOZ_DISABLE_SOCKET_PROCESS`), the process model, the sandbox, the GL path,
# the occlusion tracking. A value of the HOST in one of them would be a value a
# page reads that came from the host, which no declaration of the core covers
# (the prefs are locked, the environment was not). So `launch.build_launch_env`
# drops every `MOZ_*` of the environment it starts from, whatever its source
# (`os.environ` or the caller's `base_env`), except these two lists. They are
# tuples on purpose: they are not variables the library READS (`READ`), they are
# the whole of what the engine's environment may carry of the `MOZ_` family.

#: Written by the core, always, over whatever the caller's environment says
#: (`launch._DISPLAY_BACKEND`, `launch._NO_CRASH_REPORTER`).
MOZ_SET_BY_THE_CORE = ("MOZ_ENABLE_WAYLAND", "MOZ_CRASHREPORTER_DISABLE")
#: Diagnostics no page reads: the engine's log (`xpcom/base/Logging.cpp`;
#: `MOZ_LOG_MODULES` is its deprecated name for `MOZ_LOG`), the folder some of
#: its tests write to, and `MOZ_REMOTE_ALLOW_SYSTEM_ACCESS`, which only opens the
#: chrome context to Marionette/BiDi (`remote/components/RemoteAgent.sys.mjs`),
#: both off by themselves: no page reads it. They change what the engine WRITES
#: or what a debugging client may reach, never what a page can read, and the
#: measurements of the studies and the chrome-scope tests of the wrapper use them.
MOZ_DIAGNOSTICS = ("MOZ_LOG", "MOZ_LOG_FILE", "MOZ_LOG_MODULES", "MOZ_UPLOAD_DIR",
                   "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS")
#: Everything of the `MOZ_` family the engine's environment may carry.
MOZ_PASSED = MOZ_SET_BY_THE_CORE + MOZ_DIAGNOSTICS

# --- the GL and toolkit families of Linux: the same rule -----------------------
#
# On Linux the engine draws on its own Mesa, which reads none of these (its
# options are compiled in), but the toolkit still maps the host's GLX at startup
# for the X11 visuals, and GTK itself reads the scale (`GDK_SCALE`,
# `GDK_DPI_SCALE`), the theme (`GTK_THEME`), the portal, the decorations and
# the input method from the environment. A value of the host in any of them is a
# host value on the road to what a page reads, so the prefixes below go the way
# of the host's `MOZ_*`: out of the engine's environment, whatever its source.

#: Prefixes of the host's variables that never reach the engine (upper case).
#: `MOZ_` is the one with a way in (`MOZ_PASSED`); the others have only what
#: the core writes itself (`TOOLKIT_SET_BY_THE_CORE`).
HOST_FAMILIES = ("MOZ_", "MESA_", "LIBGL_", "GALLIUM_", "GALLIVM_", "LP_", "__GL", "__EGL",
                 "EGL_", "DRI_", "GDK_", "GTK_", "LC_")
#: Variables of the host's zone and language, by exact name (a prefix would take `TZDIR` and `LANGEXT` along). The
#: engine's own paths read them before the session's declaration is asked: ICU's default zone and locale
#: (`intl/icu/source/common/putil.cpp`), fontconfig's default language, the language of text with no `lang`
#: (`gfxFcPlatformFontList.cpp`). The core writes the language itself, from the session's locale
#: (`launch._declared_locale`); the zone is the pref `juggler.timezone.override`, and `TZ` is never written.
HOST_NAMES = ("TZ", "LANG", "LANGUAGE")
#: Written by the core, always, over whatever the caller's environment says
#: (`launch._DISPLAY_BACKEND`).
TOOLKIT_SET_BY_THE_CORE = ("GDK_BACKEND",)
#: Written by the core from the session's locale (`launch._declared_locale`), after the host's are gone.
LOCALE_SET_BY_THE_CORE = ("LANG", "LANGUAGE", "LC_ALL")
#: Everything of `HOST_FAMILIES` the engine's environment may carry.
FAMILIES_PASSED = MOZ_PASSED + TOOLKIT_SET_BY_THE_CORE
