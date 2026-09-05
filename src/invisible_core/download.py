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
import psutil
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
    GAMBE_SUPPORTATE,
    PIATTAFORME_SUPPORTATE,
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


#: The first release whose binaries are still published. Everything below it was
#: REMOVED deliberately, so that every user is on one engine rather than a long
#: tail of old ones - the owner's call, and the reason a 404 here is a fact about
#: the pin rather than a fault to report.
OLDEST_PUBLISHED_TAG_NUMBER = 14


def _missing_release_message(tag: str, asset_name: str, url: str) -> str:
    """What to say when the engine archive is not there.

    WHY THIS EXISTS. A 404 surfaced as `requests.exceptions.HTTPError: 404 Client
    Error` and nothing else - no tag, no cause, no remedy. Reported as issue #51
    on 2026-08-01 by somebody whose install came from a git pin predating
    firefox-14, when the binaries moved to the source repo. They did the work
    themselves: found that the tags existed, that no release did, and that the URL
    template named a repo hosting nothing. That investigation is the message's
    job, not the reporter's.

    AND IT STOPS AT A MESSAGE, DELIBERATELY. 18.10.0 shipped a version of this
    that ran `pip install --upgrade` on the caller's environment and then told
    them to re-run. It worked, and it is the wrong shape: a library that installs
    things while it is running mutates an environment nobody asked it to touch,
    ignores whatever lockfile put that version there, and inside a container
    rewrites an image layer at runtime. Removed in 18.11.0. Detecting the state
    and saying what to run is the whole job; running it is the caller's.

    The two cases are genuinely different and the message says which one it is.
    An OLD tag cannot be fixed by waiting: those releases were removed on purpose,
    so the pin has to move. A CURRENT tag missing its asset is something else -
    a re-cut release, a pruned asset - and worth reporting.
    """
    number = None
    if tag.startswith("firefox-") and tag.split("-", 1)[1].isdigit():
        number = int(tag.split("-", 1)[1])

    lines = [
        f"the engine archive for {tag} is not published: {asset_name} -> HTTP 404",
        f"  tried {url}",
        f"  the tag comes from the release seal inside invisible-core, not from "
        f"anything you configured.",
    ]
    if number is not None and number < OLDEST_PUBLISHED_TAG_NUMBER:
        lines += [
            "",
            f"{tag} is one of the engine releases that were REMOVED on purpose, so "
            f"that everybody runs one engine rather than a long tail of old ones. "
            f"It is not coming back and no amount of retrying will find it.",
            "",
            "You are running a version of this package that predates the move. That "
            "usually means an install pinned to a git ref rather than to a release - "
            "including a pin inside some other project that depends on this one.",
            "",
            "  pip install --upgrade invisible-playwright     # or invisible-firefox",
            "",
            "If the pin is not yours to move, the project that owns it has to move "
            "it: a git ref from before the move can never download an engine again.",
        ]
    else:
        lines += [
            "",
            f"{tag} is a current release, so this is not the retired-engine case. "
            f"Either the asset was pruned from the release or the release was "
            f"re-cut without it. Worth reporting, with this message.",
        ]
    return chr(10).join(lines)


#: Wall-clock bound on ONE download, in seconds. `requests`' own ``timeout=`` is
#: per socket operation, not per transfer: a connection that delivers a byte
#: every 59 seconds never trips a ``timeout=60`` and the download runs for as
#: long as whatever is above it allows. On 2026-08-04 a CI job produced zero
#: output for 39.4 minutes and was killed at its 40-minute limit; the same job
#: re-run took 4.6 minutes. This matters more off CI than on it, because
#: ``ensure_binary`` runs on the user's machine and a hang there has no log at
#: all. The default leaves room for the 110 MB engine at about 60 KB/s.
DOWNLOAD_DEADLINE_ENV = "INVISIBLE_DOWNLOAD_DEADLINE"
DOWNLOAD_DEADLINE_DEFAULT = 1800.0


def _download_deadline() -> float:
    """Seconds allowed for one download. Zero or negative removes the bound."""
    raw = os.environ.get(DOWNLOAD_DEADLINE_ENV)
    if raw is None or not raw.strip():
        return DOWNLOAD_DEADLINE_DEFAULT
    try:
        return float(raw)
    except ValueError:
        # Not a silent fall back to the default: an unreadable value here means
        # the caller believes a bound is in force that is not the one they set.
        raise RuntimeError(
            f"{DOWNLOAD_DEADLINE_ENV}={raw!r} is not a number of seconds. "
            f"Unset it for the default of {DOWNLOAD_DEADLINE_DEFAULT:.0f}s, "
            f"or set it to 0 to remove the bound entirely."
        ) from None


def _download_file(url: str, dst: Path, chunk_size: int = 1 << 16, progress=None) -> None:
    """Download ``url`` to ``dst``. If ``progress`` is given it is called with
    ``(bytes_done, total_bytes)`` as the download proceeds (total is 0 when the
    server sends no Content-Length).

    Bounded by ``DOWNLOAD_DEADLINE_ENV`` across the whole transfer; see that
    constant for why the per-read timeout below cannot do it."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    headers: dict[str, str] = {}
    token = _github_token()
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"token {token}"
        headers["Accept"] = "application/octet-stream"
    limit = _download_deadline()
    started = time.monotonic()
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
                # OUTSIDE the `if chunk` on purpose: iter_content yields b"" for
                # a keep-alive, so a stream that holds the socket open while
                # sending no payload would otherwise never reach this check.
                elapsed = time.monotonic() - started
                if limit > 0 and elapsed > limit:
                    f.close()
                    dst.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"the download of {url.rsplit('/', 1)[-1]} passed its "
                        f"{limit:.0f}s deadline: {done} of "
                        f"{total or 'an unknown number of'} bytes after "
                        f"{elapsed:.0f}s. The per-read timeout cannot catch "
                        f"this - a connection that trickles never trips one. "
                        f"Raise {DOWNLOAD_DEADLINE_ENV} if the link really is "
                        f"this slow, or set it to 0 to remove the bound.")


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


def sweep_orphaned_tmp(root: Path | None = None, alive=None) -> list:
    """Remove the `.tmp-<tag>-<pid>` trees left by downloads whose process is gone.

    A download extracts into a directory named after its pid and, when it
    starts again, removes only that one. A process killed during `verifying`
    or `extracting` therefore leaves up to the whole tree behind, and until
    this existed nothing ever looked at it again. The MCP server's prefetch
    (0.13.0) made the case ordinary: a client that closes the server in the
    first minute of its first session kills a download in flight.

    A tree whose pid is alive belongs to a download in flight, in this process
    or another, and is left alone. A reused pid keeps an orphan for one more
    round, which is the conservative side. Returns what was removed.
    """
    root = root or cache_root()
    if not root.is_dir():
        return []
    alive = alive or psutil.pid_exists
    removed = []
    for candidate in root.iterdir():
        if not candidate.is_dir() or not candidate.name.startswith(".tmp-"):
            continue
        pid_text = candidate.name.rsplit("-", 1)[-1]
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if pid == os.getpid() or alive(pid):
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        if not candidate.exists():
            removed.append(candidate)
    return removed


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
            f"so there is nothing to download. Pass binary_path= pointing at the "
            f"build this seal was generated from. "
            f"(INVPW_BINARY_PATH is NOT read by this library - the test scripts and "
            f"run_e2e.py translate it into binary_path=, so it only works under them. "
            f"Corrected 2026-08-19; the same false promise was removed from "
            f"invisible_playwright/_engine.py on 2026-08-14, and naming an env var "
            f"the code never reads sends the reader hunting in the wrong function.)")

    plat = sys.platform
    if plat not in PIATTAFORME_SUPPORTATE:
        # La CONDIZIONE viene dalla dichiarazione in seal.py, non da un nome
        # scritto qui: era `plat == "darwin"`, cioe' lo stesso fatto in un
        # secondo posto. Il messaggio resta specifico dove sappiamo dire
        # qualcosa di utile.
        if plat == "darwin":
            raise NotImplementedError(
                "macOS non e' piu' una piattaforma supportata: da firefox-21 in poi non "
                "vengono piu' pubblicati binari per Mac, e questo pacchetto non ne scarica.\n"
                "I seal delle release precedenti contengono ancora gli asset macOS - restano "
                "leggibili come storia - ma un nuovo avvio su Mac si ferma qui invece di "
                "tentare un download che non esiste.\n"
                "Su Windows e Linux non cambia niente."
            )
        raise NotImplementedError(
            "questa piattaforma non e' fra quelle per cui pubblichiamo un motore: "
            "%s. Le gambe dichiarate sono %s."
            % (plat, ", ".join("%s/%s" % g for g in GAMBE_SUPPORTATE))
        )
    asset = seal.asset_for(plat, platform.machine())
    version_dir = cache_dir_for_seal(seal)
    entry = version_dir / asset.entry_rel

    # On every call, warm path included: a cache that never misses again would
    # otherwise keep a killed download's tree for good.
    sweep_orphaned_tmp(cache_root())

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
        try:
            _download_file(url_archive, archive_path, progress=progress)
        except requests.HTTPError as exc:
            if getattr(exc.response, "status_code", None) != 404:
                raise
            raise RuntimeError(_missing_release_message(seal.tag, asset.name,
                                                       url_archive)) from exc
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
    # (nessun post-extract per darwin: macOS rifiuta al confine sopra, quindi
    #  questo percorso non e' piu' raggiungibile per un Mac. La funzione e' stata
    #  rimossa con la fine del supporto macOS il 2026-08-26.)
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
        # The profile manager used to render this string next to a red dot,
        # until its 2026-08-18 deletion; `doctor` reads it as plain text now.
        # Either way it must be the problem, carried as data, not whatever line
        # the message layout happens to put at a fixed index.
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


# ---------------------------------------------------------------------------
# The GeoIP database moved to `_geoip_db` on 2026-07-27 - a different subject,
# a different release cadence, a different cache root, and nothing in this file
# calls it. These two names stay importable FROM HERE because they are public:
# `from invisible_playwright.download import ensure_geoip_mmdb` is a path a user
# can already have written, through the wrapper's aliasing shim, and a split for
# our own readability is not a reason to break it.
#
# Imported at the BOTTOM on purpose. `_geoip_db` imports four helpers from this
# module, so at the top this would be a cycle; here, everything it needs is
# already defined and the partially-initialised module in sys.modules is enough.
# ---------------------------------------------------------------------------
from ._geoip_db import ensure_geoip_mmdb, geoip_mmdb_path  # noqa: E402,F401
