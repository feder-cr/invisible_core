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
    BINARY_ENTRY_REL,
    GEOIP_ASSET,
    GEOIP_MMDB_NAME,
    GEOIP_REPO,
    GEOIP_RELEASE_URL_TEMPLATE,
    RELEASE_URL_TEMPLATE,
)
from .seal import (
    Asset,
    EngineMismatch,
    Seal,
    SealError,
    SealMismatch,
    active_seal,
    read_engine_identity,
    read_stamp,
    resource_root,
    verify_engine,
    write_stamp,
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
    """Directory where all cached binaries live.

    INVISIBLE_PLAYWRIGHT_CACHE_DIR overrides it. It was set by the test suite
    and read nowhere, so "isolated" cache tests were operating on the
    developer's real cache; honouring it makes them hermetic on every OS.
    """
    override = os.environ.get("INVISIBLE_PLAYWRIGHT_CACHE_DIR")
    if override:
        return Path(override)
    return Path(platformdirs.user_cache_dir("invisible-playwright"))


def cache_dir_for_seal(seal: Seal | None = None) -> Path:
    """The cache directory IS the identity: tag, base version and BuildID.

    A different seal names a different directory, so a warm tree belonging to a
    different expectation is not rejected, it is never looked at.
    """
    s = seal or active_seal()
    return cache_root() / f"{s.tag}_{s.upstream_version}_{s.build_id}"


def cache_dir_for_version(version: str | None = None) -> Path:
    """Back-compat shim. The sealed tag resolves to the content-keyed dir; any
    other tag resolves to its legacy flat name (enumeration / cleanup only)."""
    s = active_seal()
    if version is None or version == s.tag:
        return cache_dir_for_seal(s)
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


def _download_file(url: str, dst: Path, chunk_size: int = 1 << 16, progress=None) -> None:
    """Download ``url`` to ``dst``. If ``progress`` is given it is called with
    ``(bytes_done, total_bytes)`` as the download proceeds (total is 0 when the
    server sends no Content-Length)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    headers: dict[str, str] = {}
    token = _github_token()
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"token {token}"
        headers["Accept"] = "application/octet-stream"
    with requests.get(url, stream=True, timeout=60, headers=headers) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dst, "wb") as f:
            for chunk in r.iter_content(chunk_size):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        try:
                            progress(done, total)
                        except Exception:
                            pass


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


def _adopt_existing_cache(seal: Seal, asset: Asset, version_dir: Path) -> Path | None:
    """Use a tree that is already on disk if it IS the sealed build.

    Covers every warm cache in the field (flat cache_root()/<tag> layout) and a
    content-keyed tree whose stamp was lost. Verified -> moved onto the
    content-keyed name and stamped, no download. Not verified -> left exactly
    where it is (it may belong to the other product) and the cold path runs.
    """
    candidates = []
    if version_dir.exists():
        candidates.append(version_dir)
    legacy = cache_root() / seal.tag
    if legacy != version_dir and legacy.exists():
        candidates.append(legacy)

    for d in candidates:
        entry = d / asset.entry_rel
        if not entry.exists():
            continue
        try:
            verify_engine(entry, seal, source=f"existing cache {d.name}", asset=asset)
        except EngineMismatch as e:
            # e.summary, never a line index into the rendered message: index 3
            # was the "engine says: Firefox X build Y" observation, so every
            # refusal printed a line that reads like a success.
            print(f"invisible-core: not adopting {d}: {e.summary}", file=sys.stderr)
            continue
        if asset.omni_sha256:
            omni = resource_root(entry) / "omni.ja"
            if omni.exists() and _sha256_file(omni).lower() != asset.omni_sha256.lower():
                print(f"invisible-core: not adopting {d}: omni.ja content does not match "
                      f"the sealed payload (superseded or modified tree)", file=sys.stderr)
                continue
        else:
            # This leg ships no omni.ja (the Linux archives tar the unpacked
            # dist/bin layout), so the seal has no payload digest to compare.
            # Say so: an absent check is not a passed check.
            print(f"invisible-core: {d.name}: this platform's asset has no sealed omni.ja "
                  f"digest, so the payload bytes are not content-verified; adoption rests "
                  f"on application.ini, platform.ini and the juggler markers",
                  file=sys.stderr)
        if d != version_dir:
            try:
                os.replace(d, version_dir)
                d, entry = version_dir, version_dir / asset.entry_rel
            except OSError:
                pass  # in use, or another process moved it; it is verified, use it here
        write_stamp(d, seal, asset=asset.name, asset_sha256=None, adopted=True)
        print(f"invisible-core: adopted the engine already cached at {d} "
              f"({seal.describe()}); no download needed", file=sys.stderr)
        return entry
    return None


def ensure_binary(version: str | None = None, progress=None, status=None,
                  *, seal: Seal | None = None) -> Path:
    """Return a verified path to the sealed Firefox executable. Download if needed.

    `version`, when given, must be the sealed tag: this core is paired with
    exactly one build and cannot coherently install another.

    ``progress``, if given, is called with ``(bytes_done, total_bytes)`` while the
    (large) archive downloads, for a UI progress bar. ``status``, if given, is
    called with a phase string ("downloading" | "verifying" | "extracting") so a
    UI can show what the post-100% (silent, no byte-progress) steps are doing -
    the SHA256 check + the archive extraction can take tens of seconds with no
    download progress, and otherwise look frozen at 100%.
    """
    def _phase(p: str) -> None:
        if status is not None:
            try:
                status(p)
            except Exception:
                pass

    seal = seal or active_seal()
    if version is not None and version != seal.tag:
        raise SealMismatch(
            f"this invisible-core is sealed to {seal.describe()}; it cannot install "
            f"{version!r}.\n"
            f"The prefs, the spoofed User-Agent and the protocol expectations in this "
            f"package were generated for the sealed build, so pairing them with another "
            f"engine would ship a browser whose claim contradicts itself.\n"
            f"\n"
            f"To drive {version!r} anyway, generate a seal for that build and point this "
            f"package at it:\n"
            f"  python -m invisible_core seal --binary <path to that firefox> -o my.seal.json\n"
            f"  set INVISIBLE_SEAL_FILE=my.seal.json\n"
            f"The User-Agent and the prefs then move with the seal, so the pair stays "
            f"coherent.\n"
            f"\n"
            f"(A release of invisible-core sealed to {version!r} would also work, if one "
            f"exists. It does not for tags that predate the seal, which is why the route "
            f"above is given first: it works for any build you have on disk.)"
        )
    if seal.is_local:
        raise SealError(
            f"the active seal ({seal.origin}) is a LOCAL seal with no published assets, "
            f"so there is nothing to download. Point binary_path= / INVPW_BINARY_PATH at "
            f"the tree this seal was generated from.")

    plat = sys.platform
    asset = seal.asset_for(plat, platform.machine())
    version_dir = cache_dir_for_seal(seal)
    entry = version_dir / asset.entry_rel

    stamp = read_stamp(version_dir)
    if stamp and stamp.get("seal_digest") == seal.digest and entry.exists():
        return verify_engine(entry, seal, source=f"cache hit {version_dir.name}", asset=asset)

    adopted = _adopt_existing_cache(seal, asset, version_dir)
    if adopted is not None:
        return adopted

    url_archive = _resolve_asset_url(seal.tag, asset.name)
    tmp_dir = cache_root() / f".tmp-{seal.tag}-{os.getpid()}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    with tempfile.TemporaryDirectory() as td:
        archive_path = Path(td) / asset.name
        _phase("downloading")
        _download_file(url_archive, archive_path, progress=progress)
        _phase("verifying")
        actual = _sha256_file(archive_path)
        if actual.lower() != asset.sha256.lower():
            raise RuntimeError(
                f"payload mismatch for {asset.name} under tag {seal.tag}:\n"
                f"  got      {actual}\n"
                f"  expected {asset.sha256}  (from the seal shipped inside invisible-core)\n"
                f"The asset published under this tag is not the payload this core was "
                f"sealed against: the tag was re-cut, or the download was corrupted. "
                f"Refusing to use it."
            )
        _phase("extracting")
        _extract(archive_path, tmp_dir)

    tmp_entry = tmp_dir / asset.entry_rel
    if plat == "darwin":
        _post_extract_darwin(tmp_dir, tmp_entry)
    if not tmp_entry.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"binary not found after extraction: {tmp_entry}")

    shutil.rmtree(version_dir, ignore_errors=True)
    try:
        os.replace(tmp_dir, version_dir)
    except OSError as e:
        # The tree in tmp_dir is ~250 MB that was just downloaded AND sha256
        # verified. Decide before deleting anything: the previous order deleted
        # it first and then tested `entry.exists()` against the directory it had
        # just removed, so the softening branch could never be taken and every
        # loss of the race cost a full re-download.
        if entry.exists():
            # Another process landed the same sealed tree first. Ours is
            # redundant, and the one on disk is verified below like any other.
            shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            raise RuntimeError(
                f"could not move the verified download into place: {e}\n"
                f"  from {tmp_dir}\n"
                f"  to   {version_dir}\n"
                f"The download is COMPLETE and its sha256 matches the seal, so it is "
                f"kept where it is: move that directory onto the destination by hand, "
                f"or re-run once whatever holds the destination has exited. Deleting it "
                f"would cost another {asset.size / (1 << 20):.0f} MB for nothing."
            ) from e
    write_stamp(version_dir, seal, asset=asset.name, asset_sha256=asset.sha256, adopted=False)
    return verify_engine(entry, seal, source=f"fresh download {version_dir.name}", asset=asset)


def engine_status(seal: Seal | None = None) -> tuple:
    """(ok, detail) for the sealed engine's cache. Never raises. For UIs."""
    s = seal or active_seal()
    try:
        asset = s.asset_for(sys.platform, platform.machine())
        entry = cache_dir_for_seal(s) / asset.entry_rel
        if not entry.exists():
            return (False, "not downloaded")
        verify_engine(entry, s, source="status check", asset=asset)
        return (True, s.describe())
    except EngineMismatch as e:
        # The manager renders this string next to a red dot. It must be the
        # problem, carried as data, not whatever line the message layout happens
        # to put at a fixed index.
        return (False, e.summary)
    except Exception as e:
        return (False, str(e))


def iter_cached_engines():
    """Yield (dir, identity_or_None) for every engine tree in the cache root."""
    root = cache_root()
    if not root.exists():
        return
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in {"geoip"} or d.name.startswith(".tmp-"):
            continue
        ident = None
        for rel in set(BINARY_ENTRY_REL.values()):
            if (d / rel).exists():
                try:
                    ident = read_engine_identity(d / rel)
                except Exception:
                    ident = None
                break
        yield d, ident


def _tree_belongs_to_tag(name: str, tag: str) -> bool:
    """Does cache directory `name` hold the engine of release `tag`?

    Two layouts, one predicate: the legacy flat `<tag>` and the content-keyed
    `<tag>_<version>_<buildid>`. The separator is REQUIRED, otherwise a bare
    startswith makes `firefox-180_...` a match for a seal on `firefox-18` and
    clear_cache deletes another release's engine.
    """
    return name == tag or name.startswith(tag + "_")


def clear_cache(tag: str | None = None, *, everything: bool = False) -> list:
    """Remove engine trees only. Never the cache root, never geoip/."""
    want = None if everything else (tag if tag is not None else active_seal().tag)
    removed = []
    for d, _ident in iter_cached_engines():
        if everything or _tree_belongs_to_tag(d.name, want):
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d)
    return removed


# ─────────────────────────────────────────────────────────────────────────
#  GeoIP mmdb (timezone="auto" → map egress IP → IANA zone)
#
#  daijro/geoip-all-in-one is rebuilt weekly and KEEPS ONLY the latest ~2
#  releases - older tags are pruned and 404. So we NEVER pin a tag: on every
#  launch we resolve the CURRENT latest tag from the `releases/latest/download`
#  permalink (its 302 Location carries the tag - a plain CDN request, NOT the
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
    ``releases/latest/download`` permalink - GitHub answers 302 with
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
