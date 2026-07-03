"""Download and cache the patched Firefox binary from GitHub Releases."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

import platformdirs
import requests

from .constants import (
    ARCHIVE_NAME,
    BINARY_ENTRY_REL,
    BINARY_VERSION,
    BROKEN_VERSIONS,
    GEOIP_ASSET,
    GEOIP_MMDB_NAME,
    GEOIP_REPO,
    GEOIP_RELEASE_URL_TEMPLATE,
    RELEASE_URL_TEMPLATE,
)


def _github_token() -> str | None:
    return os.environ.get("STEALTHFOX_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _parse_owner_repo(template: str) -> tuple[str, str]:
    """Extract (owner, repo) from RELEASE_URL_TEMPLATE."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/releases/", template)
    if not m:
        raise RuntimeError(f"cannot parse owner/repo from {template!r}")
    return m.group(1), m.group(2)


def cache_root() -> Path:
    """Directory where all cached binaries live."""
    return Path(platformdirs.user_cache_dir("invisible-playwright"))


def cache_dir_for_version(version: str = BINARY_VERSION) -> Path:
    return cache_root() / version


def _resolve_asset_url(tag: str, asset_name: str) -> str:
    """Return a downloadable URL for the asset.

    For private repos the direct `releases/download/<tag>/<asset>` URL returns
    404 even with a token, so we resolve via the API: list assets for the
    release tag, find the one matching `asset_name`, and use its API URL with
    `Accept: application/octet-stream` (which 302-redirects to a signed URL).
    For public repos the direct URL still works without a token.
    """
    token = _github_token()
    if not token:
        return RELEASE_URL_TEMPLATE.format(tag=tag, asset=asset_name)
    owner, repo = _parse_owner_repo(RELEASE_URL_TEMPLATE)
    api = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
    r = requests.get(api, headers={"Authorization": f"token {token}"}, timeout=30)
    r.raise_for_status()
    for a in r.json().get("assets", []):
        if a.get("name") == asset_name:
            return a["url"]
    raise RuntimeError(f"asset {asset_name!r} not found in release {tag!r}")


def _download_file(url: str, dst: Path, chunk_size: int = 1 << 16) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    headers: dict[str, str] = {}
    token = _github_token()
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"token {token}"
        headers["Accept"] = "application/octet-stream"
    with requests.get(url, stream=True, timeout=60, headers=headers) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(chunk_size):
                if chunk:
                    f.write(chunk)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_checksums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            # sha256sum uses ' *' or '  ' prefix for binary vs text mode
            key = parts[-1].lstrip("*")
            out[key] = parts[0]
    return out


def _extract(archive: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dst)
    elif archive.name.endswith(".tar.gz") or archive.suffix in {".tgz", ".gz"}:
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dst)
    else:
        raise RuntimeError(f"unknown archive format: {archive}")


def _post_extract_darwin(app_root: Path, entry: Path) -> None:
    """Make an ad-hoc-signed .app launchable on macOS.

    The .app is downloaded via requests (no Finder quarantine attached), but we
    strip com.apple.quarantine defensively and ensure the inner binary is
    executable. We exec the inner binary directly (not via LaunchServices), so
    Gatekeeper's first-launch prompt does not apply; the ad-hoc signature
    (applied in release.yml) is what lets the arm64 Mach-O run at all.
    """
    app = app_root
    # walk up to the .app bundle dir if entry points inside it
    for parent in entry.parents:
        if parent.name.endswith(".app"):
            app = parent
            break
    try:
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(app)], check=False)
    except FileNotFoundError:
        pass
    try:
        entry.chmod(0o755)
    except OSError:
        pass


def ensure_binary(version: str = BINARY_VERSION) -> Path:
    """Return a path to a runnable Firefox executable. Download if needed."""
    if version in BROKEN_VERSIONS:
        raise RuntimeError(
            f"{version} is a known-broken release (the juggler automation layer is "
            f"missing, so Playwright cannot drive it). Upgrade invisible_playwright "
            f"(current BINARY_VERSION={BINARY_VERSION}) or pass a newer version."
        )
    plat = sys.platform
    mach = platform.machine()
    asset = ARCHIVE_NAME(plat, mach)
    entry_rel = BINARY_ENTRY_REL.get(plat)
    if entry_rel is None:
        raise NotImplementedError(f"no binary entry for platform {plat}")

    version_dir = cache_dir_for_version(version)
    entry = version_dir / entry_rel
    if entry.exists():
        return entry

    url_archive = _resolve_asset_url(version, asset)
    url_sums = _resolve_asset_url(version, "checksums.txt")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        archive_path = tmp / asset
        _download_file(url_archive, archive_path)
        sums_path = tmp / "checksums.txt"
        _download_file(url_sums, sums_path)
        sums = _parse_checksums(sums_path.read_text())
        expected = sums.get(asset)
        if expected is None:
            raise RuntimeError(f"no SHA256 for {asset} in checksums.txt")
        actual = _sha256_file(archive_path)
        if actual.lower() != expected.lower():
            raise RuntimeError(
                f"SHA256 mismatch for {asset}: got {actual}, expected {expected}"
            )
        _extract(archive_path, version_dir)

    if plat == "darwin":
        _post_extract_darwin(version_dir, entry)

    if not entry.exists():
        raise RuntimeError(f"binary not found after extraction: {entry}")
    return entry


# ─────────────────────────────────────────────────────────────────────────
#  GeoIP mmdb (timezone="auto" → map egress IP → IANA zone)
#
#  daijro/geoip-all-in-one is rebuilt weekly and KEEPS ONLY the latest ~2
#  releases — older tags are pruned and 404. So we NEVER pin a tag: on every
#  launch we resolve the CURRENT latest tag from the `releases/latest/download`
#  permalink (its 302 Location carries the tag — a plain CDN request, NOT the
#  rate-limited GitHub API) and download it if it differs from the cached one.
#  Offline → reuse the cached mmdb; cold cache + offline → raise (the caller can
#  then fall back off timezone="auto"). No stale pinned tag to rot.
# ─────────────────────────────────────────────────────────────────────────


def _geoip_root() -> Path:
    return cache_root() / "geoip"


def _cached_geoip_mmdb() -> Path | None:
    """Newest cached mmdb across tag dirs, or None. Tag dirs are date strings
    (e.g. ``2026.06.17``) so a lexical sort is chronological."""
    root = _geoip_root()
    if not root.exists():
        return None
    cands = sorted(root.glob("*/*.mmdb"))
    return cands[-1] if cands else None


def _geoip_latest_url() -> str:
    return f"https://github.com/{GEOIP_REPO}/releases/latest/download/{GEOIP_ASSET}"


def _latest_geoip_tag_api() -> str:
    """Latest ``daijro/geoip-all-in-one`` release tag via the GitHub API
    (fallback for :func:`_resolve_latest_geoip_tag` when the permalink HEAD
    can't be parsed)."""
    headers = {"Accept": "application/vnd.github+json"}
    token = _github_token()
    if token:
        headers["Authorization"] = f"token {token}"
    r = requests.get(
        f"https://api.github.com/repos/{GEOIP_REPO}/releases/latest",
        headers=headers, timeout=15,
    )
    r.raise_for_status()
    tag = r.json().get("tag_name")
    if not tag:
        raise RuntimeError("no tag_name in geoip-all-in-one latest release")
    return tag


def _resolve_latest_geoip_tag() -> str | None:
    """Current latest release tag WITHOUT the rate-limited API: HEAD the
    ``releases/latest/download`` permalink — GitHub answers 302 with
    ``Location: …/releases/download/<tag>/<asset>``. Falls back to the API,
    then to ``None`` (offline / unparseable)."""
    try:
        r = requests.head(_geoip_latest_url(), allow_redirects=False, timeout=10)
        loc = r.headers.get("Location") or r.headers.get("location") or ""
        m = re.search(r"/releases/download/([^/]+)/", loc)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        return _latest_geoip_tag_api()
    except Exception:
        return None


def _download_geoip_tag(tag: str) -> Path:
    """Download + extract a specific tag's mmdb if not already cached."""
    dst_dir = _geoip_root() / tag
    target = dst_dir / GEOIP_MMDB_NAME
    if not target.exists():
        url = GEOIP_RELEASE_URL_TEMPLATE.format(tag=tag, asset=GEOIP_ASSET)
        dst_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / GEOIP_ASSET
            _download_file(url, archive)
            _extract(archive, dst_dir)
    if target.exists():
        return target
    # asset name inside the zip may differ from GEOIP_MMDB_NAME
    found = sorted(dst_dir.glob("*.mmdb"))
    if found:
        return found[0]
    raise RuntimeError(f"geoip mmdb not found after extraction in {dst_dir}")


def _prune_old_geoip_tags(keep: str) -> None:
    """Drop every cached tag dir except ``keep`` to bound disk usage."""
    root = _geoip_root()
    if not root.exists():
        return
    for d in root.iterdir():
        if d.is_dir() and d.name != keep:
            shutil.rmtree(d, ignore_errors=True)


def geoip_mmdb_path() -> Path | None:
    """Path to the currently-cached mmdb (newest tag), or None if none cached."""
    return _cached_geoip_mmdb()


def ensure_geoip_mmdb() -> Path:
    """Return the geoip mmdb, always the latest daijro build. Checked on EVERY
    call — a single cheap permalink HEAD (no GitHub API, so no rate limit).

    Resolution order:
      1. ``STEALTHFOX_GEOIP_MMDB`` env → use that file (user-supplied / test).
      2. Resolve the CURRENT latest tag. If it differs from the newest cached
         tag (or nothing is cached) → download it, prune older tags, return it.
      3. Latest tag == newest cached tag → use the cache (no download).
      4. Couldn't resolve the tag (offline / unparseable): cached mmdb → use it;
         cold cache → raise (caller can then drop timezone="auto").
    """
    override = os.environ.get("STEALTHFOX_GEOIP_MMDB")
    if override:
        p = Path(override)
        if not p.exists():
            raise RuntimeError(f"STEALTHFOX_GEOIP_MMDB points to a missing file: {p}")
        return p

    cached = _cached_geoip_mmdb()
    cached_tag = cached.parent.name if cached else None

    latest = _resolve_latest_geoip_tag()
    if latest and latest != cached_tag:
        # newer build available (or nothing cached) → fetch it
        try:
            mmdb = _download_geoip_tag(latest)
            _prune_old_geoip_tags(mmdb.parent.name)
            return mmdb
        except Exception:
            if cached:
                return cached  # transient download failure → keep using the cache
            raise

    if cached:
        return cached  # cache is already the latest, or we're offline

    raise RuntimeError(
        "geoip mmdb unavailable: no cached copy and GitHub is unreachable. "
        "Connect once to download it, or set STEALTHFOX_GEOIP_MMDB to a local "
        "geoip-aio-all.mmdb file."
    )
