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
#: The IP WebRTC announces; a value already set wins (`launch`).
WEBRTC_PUBLIC_IP = "STEALTHFOX_WEBRTC_PUBLIC_IP"
#: "off" leaves the system timer resolution alone (`juggler._pacing`).
CURSOR_TIMER = "INVPW_CURSOR_TIMER"

# --- handoffs: what the library sets for a process it starts ----------------

#: Set before a pin repair, so a repaired process does not start another (`pin`).
CORE_AUTOFIX_ATTEMPTED = "INVISIBLE_CORE_AUTOFIX_ATTEMPTED"
#: The session a process belongs to, for the reaper (`process`).
SESSION_TOKEN = "INVPW_SESSION_TOKEN"
#: The hidden desktop the spawner starts the browser on (`_headless`).
DESKTOP = "INVPW_DESKTOP"
#: Tells the engine to emit no IPv6 candidate, behind a proxy (`launch`).
WEBRTC_DISABLE_IPV6 = "STEALTHFOX_WEBRTC_DISABLE_IPV6"

SETTINGS = (ENGINE_DIR, CACHE_DIR, DOWNLOAD_DEADLINE, GITHUB_TOKEN_OWN,
            GITHUB_TOKEN, SEAL_FILE, CORE_PIN, CORE_AUTOFIX,
            CORE_AUTOFIX_TIMEOUT, UV, GEOIP_MMDB, FONT_MANIFEST,
            WEBRTC_PUBLIC_IP, CURSOR_TIMER)
HANDOFFS = (CORE_AUTOFIX_ATTEMPTED, SESSION_TOKEN, DESKTOP, WEBRTC_DISABLE_IPV6)
#: Everything the library reads from the environment.
READ = SETTINGS + HANDOFFS
