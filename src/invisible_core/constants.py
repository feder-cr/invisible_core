"""Compile-time constants that pin the wrapper to a specific Firefox build.

BINARY_VERSION is bumped every time new Firefox patches are released. It is
deliberately decoupled from the Python package version so that pure-Python
bugfixes don't force a multi-hour Firefox rebuild.
"""
from __future__ import annotations

# Bump this when a new patched Firefox build is released on GitHub.
BINARY_VERSION: str = "firefox-18"

# Releases known to be broken — ensure_binary() refuses them with a clear error
# instead of handing the user an unusable binary. firefox-8 was packaged without
# the juggler automation layer, so Playwright cannot drive it (TargetClosedError);
# fixed in firefox-9 (package-manifest.in now ships chrome/juggler). A cached
# firefox-8 from before the bump would otherwise keep being used silently.
BROKEN_VERSIONS: frozenset[str] = frozenset({"firefox-8"})

# Underlying Firefox version. Drives the spoofed UA below and the archive
# basename, so it is NOT display-only: it must match the base the binary was
# actually built from. A binary that behaves like 151 while claiming 150 is a
# cross-check a detector can make (the FF151 PNG encoder signature is already
# observable), so this moves in the same change that ships the binary.
FIREFOX_UPSTREAM_VERSION: str = "151.0"

# The base filename prefix used inside archives.
BINARY_BASENAME: str = f"firefox-{FIREFOX_UPSTREAM_VERSION}-stealth"

# ── Spoofed User-Agent ────────────────────────────────────────────────
# Single source of truth, derived from FIREFOX_UPSTREAM_VERSION so it can
# never drift from the binary we actually ship.
#
# Firefox puts only MAJOR.MINOR in the UA - a real 150.0.1 build reports
# "Firefox/150.0", never "Firefox/150.0.1" (verified against the binary:
# navigator.userAgent of the unpatched build ends in "Firefox/150.0").
# Both call sites used to hardcode "Firefox/150.0.1", a string no real
# Firefox ever emits - i.e. the spoof itself was the fingerprint. Deriving
# it here keeps the form correct and makes the next base bump automatic.
UA_VERSION: str = ".".join(FIREFOX_UPSTREAM_VERSION.split(".")[:2])  # e.g. "151.0"
USER_AGENT: str = (
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{UA_VERSION}) "
    f"Gecko/20100101 Firefox/{UA_VERSION}"
)


def ARCHIVE_NAME(platform_key: str, machine: str) -> str:
    """Return the platform-specific archive filename.

    platform_key: sys.platform ("win32", "linux", "darwin")
    machine:      platform.machine() ("AMD64", "x86_64", "arm64", "aarch64", ...)
    """
    pk = platform_key.lower()
    m = machine.lower()
    if m in {"amd64", "x86_64"}:
        arch = "x86_64"
    elif m in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise NotImplementedError(f"unsupported arch: {machine}")

    if pk == "win32":
        return f"{BINARY_BASENAME}-win-{arch}.zip"
    if pk == "linux":
        return f"{BINARY_BASENAME}-linux-{arch}.tar.gz"
    if pk == "darwin":
        return f"{BINARY_BASENAME}-macos-{arch}.tar.gz"
    raise NotImplementedError(f"unsupported platform: {platform_key}")


# Binary entry point relative path inside the extracted archive root.
# macOS ships the .app bundle (renamed to a stable "Firefox.app" by release.yml);
# the wrapper execs the inner binary directly, which sidesteps Gatekeeper.
BINARY_ENTRY_REL = {
    "win32": "firefox.exe",
    "linux": "firefox",
    "darwin": "Firefox.app/Contents/MacOS/firefox",
}

# GitHub release URL template. Binaries are hosted on the source repo
# (firefox_antidetect_patch) since firefox-14 — the same repo that builds them,
# so both the Playwright wrapper and the direct-launch profile-manager fetch
# from one place. (firefox-13 and earlier lived on invisible_playwright.)
RELEASE_URL_TEMPLATE = (
    "https://github.com/feder-cr/firefox_antidetect_patch/releases/download/{tag}/{asset}"
)

# ─────────────────────────────────────────────────────────────────────────
#  GeoIP database (timezone="auto" → resolve IANA zone from proxy egress IP)
# ─────────────────────────────────────────────────────────────────────────
# daijro/geoip-all-in-one merges IP2Location LITE + GeoLite2 + DB-IP into a
# single mmdb (country ISO + coordinates + IANA timezone via tzfpy), rebuilt
# weekly. GPL-3.0, so we DOWNLOAD it at runtime into the user cache (like the
# Firefox binary) rather than bundling it into this MIT package. The `-all`
# variant covers IPv4+IPv6. download.py NEVER pins a tag (daijro prunes old
# releases, so a pinned tag eventually 404s): on every launch it resolves the
# CURRENT latest tag from the `releases/latest/download` permalink (no GitHub
# API, no rate limit) and pulls it if newer than the cache.
GEOIP_REPO: str = "daijro/geoip-all-in-one"
GEOIP_ASSET: str = "geoip-aio-all.mmdb.zip"
GEOIP_MMDB_NAME: str = "geoip-aio-all.mmdb"
GEOIP_RELEASE_URL_TEMPLATE: str = (
    "https://github.com/daijro/geoip-all-in-one/releases/download/{tag}/{asset}"
)
