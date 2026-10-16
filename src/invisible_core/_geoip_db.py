"""The MaxMind GeoLite2 country database: fetch it, cache it, prune it.

SPLIT OUT OF download.py ON 2026-07-27. That file was 568 lines covering two
subjects that share nothing but the words "download something from GitHub": the
patched Firefox engine, and this. Measured before the split - the geoip half
calls four helpers from the engine half (`state_dir`, `_github_token`,
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
from .download import (DOWNLOAD_ATTEMPTS, _download_file, _extract,
                       _github_token, state_dir)

__all__ = ["ensure_geoip_mmdb", "geoip_mmdb_path"]


def _geoip_root() -> Path:
    return state_dir("geoip")


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


def _download_geoip_tag(tag: str, *, attempts: int) -> Path:
    """Download + extract a specific tag's mmdb if not already cached.

    ``attempts`` has no default on purpose. Whether a transient failure here is
    worth asking again about depends on something only the caller knows - is
    there a cached mmdb to fall back on - and a default would let the next
    caller skip that question without noticing they had been asked it.

    ⛔ A TAG DIRECTORY APPEARS COMPLETE OR NOT AT ALL [B320]. This extracted
    straight into it, so while one process was still writing the mmdb another
    saw the file exist, opened half of it and failed with "Is this a valid
    MaxMind DB file?" - every test of a four-worker e2e that resolved a
    timezone on a cold cache, 66 of them on the wrapper's main on 2026-10-16,
    a race latent since the first release. Now the archive is extracted
    beside the tag directories, outside the glob `_cached_geoip_mmdb` reads
    and the directories `_prune_old_geoip_tags` removes, the database is
    opened with the real reader, and only then is the directory renamed into
    place in one step. A process that loses the rename uses the winner's.
    """
    dst_dir = _geoip_root() / tag
    found = _mmdb_in(dst_dir)
    if found is not None:
        return found
    url = GEOIP_RELEASE_URL_TEMPLATE.format(tag=tag, asset=GEOIP_ASSET)
    root = _geoip_root()
    root.mkdir(parents=True, exist_ok=True)
    # Beside the tag directories, on the same filesystem so the rename is
    # atomic, and outside `root` so nothing that lists the tags can see it.
    staging = Path(tempfile.mkdtemp(prefix=f".geoip-{tag}-", dir=root.parent))
    try:
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / GEOIP_ASSET
            _download_file(url, archive, attempts=attempts)
            _extract(archive, staging)
        mmdb = _mmdb_in(staging)
        if mmdb is None:
            raise RuntimeError(f"geoip mmdb not found after extraction of {url}")
        _check_readable(mmdb)
        if dst_dir.exists() and _mmdb_in(dst_dir) is None:
            # A directory with no database left by the code before this one.
            shutil.rmtree(dst_dir, ignore_errors=True)
        try:
            os.replace(staging, dst_dir)
        except OSError:
            if _mmdb_in(dst_dir) is None:
                raise
            # Another process landed the same tag first; its copy is complete.
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    found = _mmdb_in(dst_dir)
    if found is None:
        raise RuntimeError(f"geoip mmdb not found in {dst_dir} after it was put in place")
    return found


def _mmdb_in(directory: Path) -> Path | None:
    """The mmdb of a tag directory: GEOIP_MMDB_NAME, or the first *.mmdb when
    the archive names it otherwise."""
    target = directory / GEOIP_MMDB_NAME
    if target.is_file():
        return target
    found = sorted(directory.glob("*.mmdb")) if directory.is_dir() else []
    return found[0] if found else None


def _check_readable(mmdb: Path) -> None:
    """Refuse a database the reader cannot open, before anyone else can see it.
    A truncated download is otherwise found by the first session that resolves
    a timezone, as an error that names the cache and not the download."""
    import maxminddb
    try:
        with maxminddb.open_database(str(mmdb), mode=maxminddb.MODE_MMAP):
            pass
    except (OSError, ValueError, getattr(maxminddb, "InvalidDatabaseError", ValueError)) as exc:
        raise RuntimeError(f"the downloaded geoip database {mmdb.name} cannot be read: {exc}") from exc


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
        # newer build available (or nothing cached) → fetch it.
        #
        # ⛔ ASKING AGAIN IS WORTH IT ONLY WITH NOTHING TO FALL BACK ON, and this
        # is the one function that knows which case it is in. With a cached mmdb
        # the refresh is an improvement, not a requirement: the `except` below
        # is about to return that copy, so retrying would buy ten seconds of
        # wall clock and a couple of lines on stderr, at browser-session start,
        # to reach exactly the same answer. With a COLD cache the failure is
        # terminal for the caller - it raises, and `timezone="auto"` stops
        # working - so there the attempts are the whole point.
        attempts = 1 if cached else DOWNLOAD_ATTEMPTS
        try:
            mmdb = _download_geoip_tag(latest, attempts=attempts)
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
