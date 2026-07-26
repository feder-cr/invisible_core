"""The cold path: what ensure_binary fetches, what it refuses, what it leaves.

Ported from invisible_playwright/tests/test_download.py, which exercised THIS
module through the wrapper's alias shim and against the pre-seal contract:
checksums.txt as the payload authority, cache_root()/<tag> as the cache key,
and Path.exists() as the reuse predicate. The code lives here now and the
authority is the seal shipped inside the package, so the coverage lives here too.

test_seal_cache.py is the other half of the same function and deliberately
replaces both network entry points with a raising sentinel: it is about what may
be REUSED. This file is about what happens when nothing may be reused and the
archive actually has to come down the wire, so here the transport is stubbed at
the HTTP layer (responses) and _download_file runs for real.

Cells closed here:
  the URL fetched is the sealed asset under the sealed tag (issue #14 was a
      basename derived twice, once on each side, and 404-ing on every download)
  a payload that is not the sealed bytes is refused, whatever the release says
  an archive that unpacks without the executable installs nothing
  a platform or arch the seal has no leg for is refused before a byte moves
  the five legs are five CI builds: the leg the host runs is the leg that names
      the cache directory, is verified, and is recorded in the stamp

Every seal built here carries all five published legs with five DIFFERENT
BuildIDs, which is what the schema-1 seal could not express. A test that sealed
one leg would pass against a seal-wide BuildID too, and prove nothing.

Hermetic by construction: every test asserts cache_root() is inside tmp_path
before anything is written, so the developer's real 110 MB cache is never read,
written, moved or deleted, and no browser is ever launched.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import responses

from invisible_core.constants import RELEASE_URL_TEMPLATE
from invisible_core.download import (
    cache_dir_for_seal,
    cache_dir_for_version,
    cache_root,
    ensure_binary,
)
from invisible_core.seal import (
    DEFAULT_ENTRY_REL,
    JUGGLER_ENTRIES,
    resource_root,
    STAMP_NAME,
    active_seal,
    load_seal,
    normalize_arch,
    read_stamp,
)

SEALED_VERSION = "151.0"

# Five legs, five CI runs, five BuildIDs. Keyed (platform, arch) exactly as the
# seal is.
BUILD_IDS = {
    ("win32", "x86_64"): "20260724001949",
    ("linux", "x86_64"): "20260724013301",
    ("linux", "arm64"): "20260724021145",
    ("darwin", "x86_64"): "20260724034612",
    ("darwin", "arm64"): "20260724042803",
}
INFIX = {"win32": "win", "linux": "linux", "darwin": "macos"}
EXT = {"win32": "zip", "linux": "tar.gz", "darwin": "tar.gz"}

APP_INI = ("[App]\nVendor=Mozilla\nName=Firefox\nVersion={v}\nBuildID={b}\n\n"
           "[Gecko]\nMinVersion={v}\nMaxVersion={v}\n")
PLAT_INI = "[Build]\nBuildID={b}\nMilestone={v}\n"

PLATFORMS = [
    pytest.param("win32", id="win32-zip"),
    pytest.param("linux", id="linux-targz-loose-juggler"),
    pytest.param("darwin", id="darwin-targz-bundle"),
]


def host_arch() -> str:
    return normalize_arch(platform.machine())


def asset_name(plat: str, arch: str, version: str = SEALED_VERSION) -> str:
    return f"firefox-{version}-stealth-{INFIX[plat]}-{arch}.{EXT[plat]}"


def resources_of(root: Path, plat: str) -> Path:
    """Where application.ini lives, computed independently of the code under test."""
    if plat == "darwin":
        return root / "Firefox.app" / "Contents" / "Resources"
    return root


def build_tree(root: Path, plat: str, *, version: str = SEALED_VERSION,
               build_id: str, marked: int = 4) -> Path:
    """A synthetic engine tree in the layout that platform actually ships.

    Windows and macOS pack the juggler into omni.ja; the Linux archives ship it
    loose under the tree root with no omni.ja at all. The cold path has to land
    a launchable tree for both, so both are built here rather than assuming the
    packaged shape everywhere.
    """
    entry = root / DEFAULT_ENTRY_REL[plat]
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(b"MZ\x90\x00")
    res = resources_of(root, plat)
    res.mkdir(parents=True, exist_ok=True)
    (res / "application.ini").write_text(APP_INI.format(v=version, b=build_id), encoding="utf-8")
    (res / "platform.ini").write_text(PLAT_INI.format(v=version, b=build_id), encoding="utf-8")

    def blob(i: int) -> bytes:
        return (b"Services.prefs.getBoolPref('zoom.stealth.enabled', true)"
                if i < marked else b"// vanilla")

    if plat == "linux":
        for i, name in enumerate(JUGGLER_ENTRIES):
            p = res / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(blob(i))
    else:
        with zipfile.ZipFile(res / "omni.ja", "w") as zf:
            for i, name in enumerate(JUGGLER_ENTRIES):
                zf.writestr(name, blob(i))
    return entry


def pack(tree_root: Path, dst: Path) -> bytes:
    """Archive the CONTENTS of tree_root, the way the release archives are cut:
    the executable sits at the archive root, not under a wrapper directory."""
    files = [p for p in sorted(tree_root.rglob("*")) if p.is_file()]
    if dst.name.endswith(".zip"):
        with zipfile.ZipFile(dst, "w") as zf:
            for p in files:
                zf.write(p, p.relative_to(tree_root).as_posix())
    else:
        with tarfile.open(dst, "w:gz") as tf:
            for p in files:
                tf.add(p, arcname=p.relative_to(tree_root).as_posix())
    return dst.read_bytes()


def write_seal(path: Path, *, tag: str = "firefox-18", version: str = SEALED_VERSION,
               sha_for: dict | None = None, build_ids: dict | None = None,
               omni_sha_for: dict | None = None) -> Path:
    """A five-leg release seal. `sha_for` overrides the sha256 of individual legs
    (by (platform, arch)); every other leg gets a placeholder, exactly like a
    real seal where only the leg you run is ever compared."""
    ids = build_ids or BUILD_IDS
    assets = {}
    for (plat, arch), build_id in ids.items():
        assets[asset_name(plat, arch, version)] = {
            "platform": plat, "arch": arch, "build_id": build_id,
            "sha256": (sha_for or {}).get((plat, arch), "00" * 32),
            "size": 1, "entry_rel": DEFAULT_ENTRY_REL[plat],
            # Empty by default, which is what every leg of a Linux seal carries
            # (that archive tars the unpacked layout and has no omni.ja). It
            # also means the ADOPTION digest check is never entered by default -
            # `if asset.omni_sha256:` is falsy - which is exactly why disabling
            # that check left all 23 tests in this file green. Pass
            # `omni_sha_for` to reach it.
            "omni_sha256": (omni_sha_for or {}).get((plat, arch), ""),
        }
    path.write_text(json.dumps({
        "schema": 2, "tag": tag, "upstream_version": version,
        "source_commit": "0123456789abcdef",
        "playwright": {"min": "1.55.0", "max": "1.61.0"},
        "assets": assets,
    }, sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """An isolated cache root, plus the public-repo download path (no token), so
    _resolve_asset_url returns the direct URL instead of calling the API."""
    root = tmp_path / "Cache"
    root.mkdir()
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_CACHE_DIR", str(root))
    monkeypatch.delenv("STEALTHFOX_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    got = cache_root()
    assert got == root, f"cache_root() ignored the override: {got}"
    assert str(got).startswith(str(tmp_path)), "refusing to run against a real cache"
    return root


def dirs_under(root: Path) -> list:
    return sorted(d.name for d in root.iterdir() if d.is_dir())


def publish(tmp_path, plat: str, *, marked: int = 4, complete: bool = True):
    """Build the leg this host would fetch for `plat`, seal it, and return
    (seal, asset_name, archive_bytes, build_id)."""
    arch = host_arch()
    if (plat, arch) not in BUILD_IDS:
        pytest.skip(f"no {plat} leg for arch {arch}")
    build_id = BUILD_IDS[(plat, arch)]
    name = asset_name(plat, arch)
    payload = tmp_path / f"payload-{plat}"
    if complete:
        build_tree(payload, plat, build_id=build_id, marked=marked)
    else:
        payload.mkdir(parents=True, exist_ok=True)
        (payload / "other.bin").write_bytes(b"X")
    archive_bytes = pack(payload, tmp_path / name)
    seal = load_seal(write_seal(
        tmp_path / f"seal-{plat}.json",
        sha_for={(plat, arch): hashlib.sha256(archive_bytes).hexdigest()}))
    return seal, name, archive_bytes, build_id


# ------------------------------------------------------------- the cold path

@pytest.mark.parametrize("plat", PLATFORMS)
@responses.activate
def test_cold_path_downloads_verifies_extracts_and_stamps(cache, tmp_path, monkeypatch, plat):
    """Cache miss -> GET the sealed asset -> SHA256 against the SEAL -> extract
    -> verify the engine -> stamp -> return the entry.

    The retired version of this test asserted the SHA came from the release's own
    checksums.txt, which is a re-cut tag re-cutting its own proof. The expected
    hash now ships inside this package.
    """
    monkeypatch.setattr(sys, "platform", plat)
    seal, name, archive_bytes, build_id = publish(tmp_path, plat)
    url = RELEASE_URL_TEMPLATE.format(tag=seal.tag, asset=name)
    responses.add(responses.GET, url, body=archive_bytes, status=200)

    got = ensure_binary(seal=seal)

    version_dir = cache_dir_for_seal(seal)
    assert got == version_dir / DEFAULT_ENTRY_REL[plat]
    assert got.exists() and got.read_bytes() == b"MZ\x90\x00"
    # Issue #14 was a basename derived independently on each side: assert the
    # exact URL, not merely that something was fetched.
    assert [c.request.url for c in responses.calls] == [url]
    # The cache key is the identity, and the BuildID in it is THIS leg's: a
    # Windows and a Linux install of one release do not share a directory.
    assert version_dir.name == f"{seal.tag}_{seal.upstream_version}_{build_id}"

    stamp = read_stamp(version_dir)
    assert stamp and stamp["seal_digest"] == seal.digest, stamp
    assert stamp["adopted"] is False
    assert stamp["asset"] == name
    assert stamp["asset_sha256"] == hashlib.sha256(archive_bytes).hexdigest()
    assert stamp["build_id"] == build_id, "the stamp must record the leg, not a seal-wide id"
    assert not [d for d in dirs_under(cache) if d.startswith(".tmp-")], \
        "the staging directory must not survive a successful extraction"


@pytest.mark.parametrize("plat", PLATFORMS)
@responses.activate
def test_a_second_call_serves_the_stamped_tree_without_hitting_the_network(
        cache, tmp_path, monkeypatch, plat):
    """The warm half of the same run: what the cold path wrote is a cache hit.

    The retired test called a bare executable a cache hit, which is what made a
    half-extracted tree reusable forever (test_seal_cache.py, L6). The predicate
    is the stamp the cold path writes LAST, so this asserts the two halves agree:
    what ensure_binary leaves behind is what ensure_binary reuses.
    """
    monkeypatch.setattr(sys, "platform", plat)
    seal, name, archive_bytes, _ = publish(tmp_path, plat)
    responses.add(responses.GET, RELEASE_URL_TEMPLATE.format(tag=seal.tag, asset=name),
                  body=archive_bytes, status=200)

    first = ensure_binary(seal=seal)
    calls_after_cold = len(responses.calls)
    second = ensure_binary(seal=seal)

    assert second == first
    assert len(responses.calls) == calls_after_cold, "a warm cache must not fetch"


# --------------------------------------------------------- the payload refusal

@responses.activate
def test_payload_that_is_not_the_sealed_bytes_is_refused(cache, tmp_path, monkeypatch):
    """The SHA mismatch case, re-pointed at the new authority.

    Under the old contract the expected hash travelled WITH the download
    (checksums.txt from the same release), so a re-cut tag authorised itself.
    The expected hash now travels inside the package, so the archive published
    under the tag can disagree with it, and when it does nothing is installed.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    arch = host_arch()
    build_id = BUILD_IDS[("linux", arch)]
    name = asset_name("linux", arch)
    payload = tmp_path / "payload"
    build_tree(payload, "linux", build_id=build_id)
    archive_bytes = pack(payload, tmp_path / name)
    # The seal expects something else entirely for this leg.
    seal = load_seal(write_seal(tmp_path / "s.json", sha_for={("linux", arch): "ab" * 32}))
    responses.add(responses.GET, RELEASE_URL_TEMPLATE.format(tag=seal.tag, asset=name),
                  body=archive_bytes, status=200)

    with pytest.raises(RuntimeError) as exc:
        ensure_binary(seal=seal)
    msg = str(exc.value)
    assert "payload mismatch" in msg, msg
    assert hashlib.sha256(archive_bytes).hexdigest() in msg, "must show what it got"
    assert "ab" * 32 in msg, "must show what the seal expected"

    assert not cache_dir_for_seal(seal).exists(), "a refused payload must install nothing"
    assert dirs_under(cache) == [], f"left behind: {dirs_under(cache)}"


@responses.activate
def test_missing_entry_after_extraction_raises_and_leaves_no_cache(
        cache, tmp_path, monkeypatch):
    """The archive verifies and unpacks, but the executable is not in it.

    Better to raise than to hand back a path that does not exist. The addition
    versus the retired test: the staging tree must be gone too, because a
    half-extracted directory under the cache root is exactly the input the old
    Path.exists() predicate would have reused forever.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    seal, name, archive_bytes, _ = publish(tmp_path, "linux", complete=False)
    responses.add(responses.GET, RELEASE_URL_TEMPLATE.format(tag=seal.tag, asset=name),
                  body=archive_bytes, status=200)

    with pytest.raises(RuntimeError, match="binary not found after extraction"):
        ensure_binary(seal=seal)

    assert not cache_dir_for_seal(seal).exists()
    assert dirs_under(cache) == [], f"staging tree survived: {dirs_under(cache)}"


@responses.activate
def test_an_engine_that_is_not_the_sealed_build_is_refused_after_download(
        cache, tmp_path, monkeypatch):
    """The published archive matched the sealed bytes but is not our build.

    The cold path ends in verify_engine, not in "extraction succeeded". Without
    it a re-cut archive whose sha the seal happened to carry would be handed
    over on the strength of the hash alone.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    seal, name, archive_bytes, _ = publish(tmp_path, "linux", marked=0)
    responses.add(responses.GET, RELEASE_URL_TEMPLATE.format(tag=seal.tag, asset=name),
                  body=archive_bytes, status=200)

    from invisible_core.seal import EngineMismatch
    with pytest.raises(EngineMismatch) as exc:
        ensure_binary(seal=seal)
    assert "fresh download" in str(exc.value), str(exc.value)


# ------------------------------------------------- refusals before any traffic

@responses.activate
def test_unsupported_platform_raises_before_any_network(cache, tmp_path, monkeypatch):
    """A platform the seal has no leg for must be refused before a byte moves.

    The wording changed with the seal (it used to be "unsupported platform");
    what has to survive is the exception type, the naming of the platform that
    was asked for, and the fail-fast - the point of the original test was to
    refuse before, not after, a 110 MB download.
    """
    seal = load_seal(write_seal(tmp_path / "s.json"))
    monkeypatch.setattr(sys, "platform", "freebsd")
    with pytest.raises(NotImplementedError) as exc:
        ensure_binary(seal=seal)
    assert "freebsd" in str(exc.value), str(exc.value)
    assert len(responses.calls) == 0, "it must refuse before the first request"
    assert dirs_under(cache) == []


@responses.activate
def test_unsupported_arch_raises_before_any_network(cache, tmp_path, monkeypatch):
    """The same fail-fast on the other axis: normalize_arch is what the leg
    lookup goes through, so an arch we never shipped stops here rather than
    downloading a leg that cannot run."""
    seal = load_seal(write_seal(tmp_path / "s.json"))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "ppc64le")
    with pytest.raises(NotImplementedError) as exc:
        ensure_binary(seal=seal)
    assert "ppc64le" in str(exc.value), str(exc.value)
    assert len(responses.calls) == 0, "it must refuse before the first request"


# ------------------------------------------------- the back-compat path helper

def test_cache_dir_for_version_no_arg_is_the_sealed_content_key():
    """The no-arg call used to name cache_root()/<tag>. It now names the
    content-keyed directory for the ACTIVE seal, which is the path the test
    harnesses and the profile manager look the fetched engine up under."""
    s = active_seal()
    p = cache_dir_for_version()
    assert p == cache_dir_for_seal(s)
    assert p.name == f"{s.tag}_{s.upstream_version}_{s.build_id}"
    assert p.parent == cache_root()


def test_cache_dir_for_the_sealed_tag_is_the_same_directory():
    """Spelling out the tag you already have must not resolve somewhere else, or
    a caller that passes it would fetch the same engine twice."""
    s = active_seal()
    assert cache_dir_for_version(s.tag) == cache_dir_for_version()


def test_cache_dir_for_another_tag_keeps_the_legacy_flat_name():
    """Any other tag resolves to the old flat layout: that is what a warm cache
    from before the seal is called on disk, and clear-cache and enumeration have
    to be able to name it. It is never a directory ensure_binary installs into."""
    s = active_seal()
    other = cache_dir_for_version("firefox-3")
    assert other.name == "firefox-3"
    assert other.parent == cache_root()
    assert other != cache_dir_for_version(s.tag)


def test_two_tags_never_share_a_directory():
    """firefox-3 and firefox-4 must never collide: extraction would clobber one
    with the other and break every downgrade."""
    a, b = cache_dir_for_version("firefox-3"), cache_dir_for_version("firefox-4")
    assert a != b and a.parent == b.parent


def test_cache_root_is_a_path_that_names_the_product():
    """A Path, not a string (callers use .mkdir()), and identifiable as ours so a
    user can delete it without taking another tool's cache with it."""
    p = cache_root()
    assert isinstance(p, Path)
    assert "invisible-playwright" in str(p).lower()


def test_the_stamp_name_is_the_one_the_cache_tests_assume():
    """One literal, two files: if STAMP_NAME moved, every reuse assertion in
    test_seal_cache.py would keep passing while testing nothing."""
    assert STAMP_NAME == ".invisible-seal.json"


# --------------------------------- the last step: moving 250 MB into place (D6)
#
# `os.replace(tmp_dir, version_dir)` is the last thing that can fail after a
# ~250 MB download has already been fetched AND sha256-verified. Its OSError
# branch used to rmtree that verified tree FIRST and then test
# `if not entry.exists()` - against a directory it had just deleted, under a
# destination that had been rmtree'd two lines earlier. So the softening branch
# was unreachable, and every loss of the race cost a full re-download.

@pytest.mark.parametrize("plat", PLATFORMS)
@responses.activate
def test_a_failed_final_move_keeps_the_verified_download(cache, tmp_path, monkeypatch, plat):
    monkeypatch.setattr(sys, "platform", plat)
    seal, name, archive_bytes, _ = publish(tmp_path, plat)
    responses.add(responses.GET, RELEASE_URL_TEMPLATE.format(tag=seal.tag, asset=name),
                  body=archive_bytes, status=200)
    version_dir = cache_dir_for_seal(seal)

    import os as _os

    from invisible_core import download as dl
    real_replace = _os.replace

    def refuse_the_final_move(src, dst, *a, **kw):
        # Only the one move under test: write_stamp() and everything else that
        # replaces a file must keep working, or the test proves nothing.
        if Path(dst) == version_dir:
            raise OSError(13, "Permission denied")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(dl.os, "replace", refuse_the_final_move)

    with pytest.raises(RuntimeError) as exc:
        ensure_binary(seal=seal)

    staging = [d for d in cache.iterdir() if d.is_dir() and d.name.startswith(".tmp-")]
    assert len(staging) == 1, dirs_under(cache)
    assert (staging[0] / DEFAULT_ENTRY_REL[plat]).exists(), \
        "the verified download was deleted, so the retry pays for it again"
    msg = str(exc.value)
    assert str(staging[0]) in msg, msg
    assert str(version_dir) in msg, msg
    assert len(responses.calls) == 1


@pytest.mark.parametrize("plat", PLATFORMS)
@responses.activate
def test_losing_the_race_uses_the_tree_the_winner_landed(cache, tmp_path, monkeypatch, plat):
    """The branch that was unreachable: another process put the same sealed tree
    in place while ours was extracting. Ours is redundant, so it is dropped and
    the tree on disk is verified like any other - no exception, no download."""
    monkeypatch.setattr(sys, "platform", plat)
    seal, name, archive_bytes, build_id = publish(tmp_path, plat)
    responses.add(responses.GET, RELEASE_URL_TEMPLATE.format(tag=seal.tag, asset=name),
                  body=archive_bytes, status=200)
    version_dir = cache_dir_for_seal(seal)

    import os as _os

    from invisible_core import download as dl
    real_replace = _os.replace

    def winner_lands_first(src, dst, *a, **kw):
        if Path(dst) == version_dir:
            build_tree(version_dir, plat, build_id=build_id)
            raise OSError(13, "Permission denied")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(dl.os, "replace", winner_lands_first)

    got = ensure_binary(seal=seal)

    assert got == version_dir / DEFAULT_ENTRY_REL[plat]
    assert got.exists()
    assert not [d for d in dirs_under(cache) if d.startswith(".tmp-")], dirs_under(cache)
    stamp = read_stamp(version_dir)
    assert stamp and stamp["seal_digest"] == seal.digest, stamp
