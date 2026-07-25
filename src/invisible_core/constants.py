"""Constants. Everything that describes the engine is a projection of the seal.

No engine fact is hand-edited here. BINARY_VERSION and FIREFOX_UPSTREAM_VERSION
used to be two literals that had to move together, which is how a tag and a base
version could disagree inside one published commit. They, BUILD_ID, CONTRACT_N,
SEAL_DIGEST, SOURCE_COMMIT and everything computed from them (BINARY_BASENAME,
UA_VERSION, USER_AGENT, and ARCHIVE_NAME, which returns the sealed asset name
verbatim whenever the seal has assets) come out of seal.json, which CI generated
from the archives it published, so they cannot disagree with each other or with
the engine.

What IS hand-written, because no seal carries it: the "firefox-" / "-stealth"
affixes and the per-platform archive suffixes that surround the sealed version
in BINARY_BASENAME / ARCHIVE_NAME, RELEASE_URL_TEMPLATE (the repo that hosts the
releases), BINARY_ENTRY_REL (seal.DEFAULT_ENTRY_REL, the executable path inside
each archive), and the four GEOIP_* constants (a third-party repo, its asset
name and its download URL). Those move only when the hosting repo, the archive
layout or the upstream GeoIP project moves.

The NAMES are the binary/wrapper contract and never change.
"""
from __future__ import annotations

from ._version import CORE_REVISION, __version__ as PKG_VERSION
from .seal import DEFAULT_ENTRY_REL, active_seal, normalize_arch

_SEAL = active_seal()

BINARY_VERSION: str = _SEAL.tag
FIREFOX_UPSTREAM_VERSION: str = _SEAL.upstream_version
# The BuildID of the leg THIS host runs. The five published legs are five CI
# builds with five BuildIDs, so there is no seal-wide value to export here; the
# launch-time check compares against the per-asset one, not against this.
BUILD_ID: str = _SEAL.build_id
CONTRACT_N: int = _SEAL.contract_n
SEAL_DIGEST: str = _SEAL.digest
SOURCE_COMMIT: str = _SEAL.source_commit

# Retired. A superseded build is now simply a build no seal points at, and
# ensure_binary() refuses any tag but the sealed one, so there is nothing to
# blacklist. Kept as an empty frozenset for import compatibility.
BROKEN_VERSIONS: frozenset[str] = frozenset()

BINARY_BASENAME: str = f"firefox-{FIREFOX_UPSTREAM_VERSION}-stealth"

# Spoofed User-Agent. Firefox puts only MAJOR.MINOR in the UA (a real 150.0.1
# build reports "Firefox/150.0"), so the truncation is part of the form.
UA_VERSION: str = ".".join(FIREFOX_UPSTREAM_VERSION.split(".")[:2])
USER_AGENT: str = (
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{UA_VERSION}) "
    f"Gecko/20100101 Firefox/{UA_VERSION}"
)


def ARCHIVE_NAME(platform_key: str, machine: str) -> str:
    """The platform-specific archive filename, taken from the seal when it has
    assets (the sealed name is the published name, by construction) and
    reconstructed only for a local seal."""
    pk = platform_key.lower()
    arch = normalize_arch(machine)
    if not _SEAL.is_local:
        return _SEAL.asset_for(pk, machine).name
    if pk == "win32":
        return f"{BINARY_BASENAME}-win-{arch}.zip"
    if pk == "linux":
        return f"{BINARY_BASENAME}-linux-{arch}.tar.gz"
    if pk == "darwin":
        return f"{BINARY_BASENAME}-macos-{arch}.tar.gz"
    raise NotImplementedError(f"unsupported platform: {platform_key}")


BINARY_ENTRY_REL = dict(DEFAULT_ENTRY_REL)

# GitHub release URL template. Binaries are hosted on the source repo
# (firefox_antidetect_patch) since firefox-14 - the same repo that builds them,
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
