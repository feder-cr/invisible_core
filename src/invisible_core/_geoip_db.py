"""The MaxMind GeoLite2 country database: fetch it, cache it, prune it.

SPLIT OUT OF download.py ON 2026-07-27. That file was 568 lines covering two
subjects that share nothing but the words "download something from GitHub": the
patched Firefox engine, and this. Measured before the split - the geoip half
calls four helpers from the engine half (`cache_root`, `_github_token`,
`_download_file`, `_extract`) and the engine half calls NOTHING here. A
one-directional dependency across a seam is a seam.

It is a separate subject in every other way already: a different release cadence
(MaxMind publishes on its own schedule, this repo does not), a different cache
root, its own tag format (date strings, newest wins), and its own test file -
`tests/test_geoip_update.py` was separate long before the module was.

The four helpers are imported from `download`, not copied. They are the same
"fetch a GitHub release asset" mechanics, and the point of the split is that
this file stops being read by anyone looking for engine code, not that the
mechanics get written twice.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

import requests

from .constants import (
    GEOIP_ASSET,
    GEOIP_MMDB_NAME,
    GEOIP_RELEASE_URL_TEMPLATE,
    GEOIP_REPO,
)
from .download import _download_file, _extract, _github_token, cache_root

__all__ = ["ensure_geoip_mmdb", "geoip_mmdb_path"]


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
    ``releases/latest/download`` permalink - GitHub answers 302 with
    ``Location: …/releases/download/<tag>/<asset>``. Falls back to the API,
    then to ``None`` (offline / unparseable)."""
    # NOTE the bare `except Exception` below. It is correct - any failure here
    # must fall through to the API and then to None - and it also swallowed a
    # missing `import re` when this code was split out of download.py on
    # 2026-07-27: the NameError was caught, the fallback ran, and the unit test
    # that patches `requests.head` silently made a REAL network call and
    # asserted against the real latest tag. Loud only because the fixture
    # pinned a date the world had moved past. If you add a name to this block,
    # check that it is imported; nothing here will tell you.
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
    call - a single cheap permalink HEAD (no GitHub API, so no rate limit).

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
