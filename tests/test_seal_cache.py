"""The cache: what may be reused, and what must never be served. No network.

Spec: docs/firefox-stealth-architecture/17-release-seal-spec.md sections 3.2 (8-11),
4.10 and 7. Audit: 16-version-divergence-matrix.md section 2a/2c.

Cells closed here:
  D2  a per-profile pin (or any caller) asking for a tag this core is not sealed to
  D3  a warm tree from another release sitting under the name the seal would use
  D5  a re-cut payload under the same tag (omni.ja no longer the sealed bytes)
  L6  a half-extracted tree reused forever, because the executable was the predicate
  L7  clear-cache taking the shared root, the other product's engine and geoip

Hermetic by construction: every test asserts cache_root() is inside tmp_path
before it writes anything, so the developer's real 110 MB cache is never touched,
and both network entry points are replaced by a raising sentinel, so "did it go
to the network instead of serving the stale tree" is an assertion, not a hope.
"""
from __future__ import annotations

import json
import platform
import sys
import zipfile
from pathlib import Path

import pytest

from invisible_core import download
from invisible_core.download import (
    cache_dir_for_seal,
    cache_root,
    clear_cache,
    engine_status,
    ensure_binary,
)
from invisible_core.seal import (
    DEFAULT_ENTRY_REL,
    JUGGLER_ENTRIES,
    STAMP_NAME,
    EngineMismatch,
    SealMismatch,
    load_seal,
    normalize_arch,
    read_stamp,
)

SEALED_VERSION = "151.0"
SEALED_BUILD = "20260724001949"
OTHER_BUILD = "20260611193205"
OLD_VERSION = "150.0.1"
OLD_BUILD = "20260624073725"

APP_INI = "[App]\nVendor=Mozilla\nName=Firefox\nVersion={v}\nBuildID={b}\n\n[Gecko]\nMinVersion={v}\nMaxVersion={v}\n"
PLAT_INI = "[Build]\nBuildID={b}\nMilestone={v}\n"


class NetworkTouched(RuntimeError):
    """Raised by the sentinel that replaces every download entry point."""


def entry_rel() -> str:
    return DEFAULT_ENTRY_REL.get(sys.platform, "firefox")


def resources_of(root: Path) -> Path:
    if sys.platform == "darwin":
        return root / "Firefox.app" / "Contents" / "Resources"
    return root


def build_tree(root: Path, *, version: str = SEALED_VERSION, build_id: str = SEALED_BUILD,
               marked: int = 4, complete: bool = True) -> Path:
    """A synthetic engine tree. complete=False leaves only the executable, which
    is exactly what an interrupted extraction leaves behind."""
    entry = root / entry_rel()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(b"MZ\x90\x00")
    if not complete:
        return entry
    res = resources_of(root)
    res.mkdir(parents=True, exist_ok=True)
    (res / "application.ini").write_text(APP_INI.format(v=version, b=build_id), encoding="utf-8")
    (res / "platform.ini").write_text(PLAT_INI.format(v=version, b=build_id), encoding="utf-8")
    with zipfile.ZipFile(res / "omni.ja", "w") as zf:
        for i, name in enumerate(JUGGLER_ENTRIES):
            zf.writestr(name, b"Services.prefs.getBoolPref('zoom.stealth.enabled', true)"
                        if i < marked else b"// vanilla")
    return entry


def asset_name(version: str) -> str:
    infix, ext = {"win32": ("win", "zip"), "linux": ("linux", "tar.gz"),
                  "darwin": ("macos", "tar.gz")}.get(sys.platform, ("linux", "tar.gz"))
    return f"firefox-{version}-stealth-{infix}-{normalize_arch(platform.machine())}.{ext}"


def write_seal(path: Path, *, tag: str = "firefox-18", version: str = SEALED_VERSION,
               build_id: str = SEALED_BUILD, omni_sha256: str = "",
               with_assets: bool = True) -> Path:
    assets = {}
    if with_assets:
        assets[asset_name(version)] = {
            "platform": sys.platform, "arch": normalize_arch(platform.machine()),
            "sha256": "00" * 32, "size": 1, "entry_rel": entry_rel(),
            "omni_sha256": omni_sha256,
            # Per-asset since schema 2: the published legs are five CI builds.
            "build_id": build_id,
        }
    path.write_text(json.dumps({
        "schema": 2, "tag": tag, "upstream_version": version, "build_id": build_id,
        "source_commit": "0123456789abcdef", "playwright": {"min": "1.55.0", "max": "1.61.0"},
        "assets": assets,
    }, sort_keys=True), encoding="utf-8")
    return path


def stamp_for(version_dir: Path, seal, *, adopted: bool = False) -> None:
    """Write a stamp directly, without the code under test, so a test that says
    "a matching stamp is present" cannot be satisfied by a broken writer."""
    (version_dir / STAMP_NAME).write_text(json.dumps({
        "schema": 1, "seal_digest": seal.digest, "tag": seal.tag,
        "upstream_version": seal.upstream_version, "build_id": seal.build_id,
        "asset": asset_name(seal.upstream_version), "asset_sha256": None,
        "adopted": adopted, "written_by": "test", "written_at": "2026-07-25T00:00:00Z",
    }, sort_keys=True), encoding="utf-8")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """An isolated cache root. Nothing below ever looks at the real one."""
    root = tmp_path / "Cache"
    root.mkdir()
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_CACHE_DIR", str(root))
    got = cache_root()
    assert got == root, f"cache_root() ignored the override: {got}"
    assert str(got).startswith(str(tmp_path)), "refusing to run against a real cache"
    return root


@pytest.fixture
def no_network(monkeypatch):
    """Both entry points to the network raise. Reaching them is a PASS signal in
    the tests that must not serve a warm tree, and a failure everywhere else."""
    def boom(*a, **k):
        raise NetworkTouched(f"cold path taken: {a[:2]}")
    monkeypatch.setattr(download, "_resolve_asset_url", boom)
    monkeypatch.setattr(download, "_download_file", boom)
    return boom


@pytest.fixture
def sealed(tmp_path):
    return load_seal(write_seal(tmp_path / "firefox-18.seal.json"))


# ------------------------------------------------------------------- D2

def test_pin_to_another_tag_is_refused_without_touching_the_network(cache, no_network, sealed):
    """D2: a profile row (or any caller) pinned to firefox-14. Today that returns
    a 150.0.1 engine from the warm cache under a 151.0 claim, with zero HTTP and
    zero warnings."""
    build_tree(cache / "firefox-14", version=OLD_VERSION, build_id=OLD_BUILD)
    with pytest.raises(SealMismatch) as exc:
        ensure_binary("firefox-14", seal=sealed)
    msg = str(exc.value)
    assert "firefox-14" in msg and "firefox-18" in msg, msg
    assert (cache / "firefox-14" / entry_rel()).exists(), "must not delete anything"


# ------------------------------------------------------------------- D3

def test_warm_legacy_tree_from_another_release_is_not_served(cache, no_network, sealed):
    """D3: the flat cache_root()/<tag> layout every field install has, holding a
    150.0.1 engine, under a core sealed to 151.0. The half-edit case. It must
    fall through to the cold path rather than hand the stale engine back."""
    legacy = cache / "firefox-18"
    build_tree(legacy, version=OLD_VERSION, build_id=OLD_BUILD)
    with pytest.raises(NetworkTouched):
        ensure_binary(seal=sealed)
    assert (legacy / entry_rel()).exists(), "an unadoptable tree is left in place"


def test_warm_tree_under_the_content_key_but_from_another_build_is_not_served(
        cache, no_network, sealed):
    """The same skew after a re-cut: the directory name is right, the engine
    inside it is not."""
    version_dir = cache_dir_for_seal(sealed)
    build_tree(version_dir, version=SEALED_VERSION, build_id=OTHER_BUILD)
    with pytest.raises(NetworkTouched):
        ensure_binary(seal=sealed)


def test_two_seals_differing_only_in_build_id_never_share_a_directory(cache, no_network, tmp_path):
    """D3/D4 structurally: the cache key IS the identity, so a tree belonging to
    a different seal is not rejected, it is never looked at."""
    seal_a = load_seal(write_seal(tmp_path / "a.json", build_id=SEALED_BUILD))
    seal_b = load_seal(write_seal(tmp_path / "b.json", build_id=OTHER_BUILD))
    dir_a, dir_b = cache_dir_for_seal(seal_a), cache_dir_for_seal(seal_b)
    assert dir_a != dir_b
    assert SEALED_BUILD in dir_a.name and OTHER_BUILD in dir_b.name

    build_tree(dir_a, build_id=SEALED_BUILD)
    stamp_for(dir_a, seal_a)
    with pytest.raises(NetworkTouched):
        ensure_binary(seal=seal_b)
    assert (dir_a / entry_rel()).exists(), "the other seal's tree is untouched"


# ------------------------------------------------------------------- L6

def test_half_extracted_tree_is_never_reused(cache, no_network, sealed):
    """L6: extraction interrupted after the executable landed. Today the bare
    Path.exists() short-circuit returns that tree forever."""
    version_dir = cache_dir_for_seal(sealed)
    entry = build_tree(version_dir, complete=False)
    assert entry.exists() and not (version_dir / STAMP_NAME).exists()

    ok, detail = engine_status(sealed)
    assert ok is False, detail
    with pytest.raises(NetworkTouched):
        ensure_binary(seal=sealed)


def test_a_matching_stamp_does_not_bypass_the_engine_check(cache, no_network, sealed):
    """The stamp is the reuse predicate, but it is not the verdict: a stamped
    directory whose engine was swapped underneath is still refused."""
    version_dir = cache_dir_for_seal(sealed)
    build_tree(version_dir, version=OLD_VERSION, build_id=OLD_BUILD)
    stamp_for(version_dir, sealed)
    with pytest.raises(EngineMismatch):
        ensure_binary(seal=sealed)


def test_stamped_matching_tree_is_served_with_no_network(cache, no_network, sealed):
    """The warm path that must stay fast: right directory, right engine, stamp
    present. One ini read and one zip directory read, no HTTP."""
    version_dir = cache_dir_for_seal(sealed)
    entry = build_tree(version_dir)
    stamp_for(version_dir, sealed)
    assert ensure_binary(seal=sealed) == entry


# --------------------------------------------------------- adoption and D5

def test_legacy_flat_tree_matching_the_seal_is_adopted_not_re_downloaded(
        cache, no_network, tmp_path):
    """Migration: the 110 MB tree already on disk is verified, moved onto the
    content-keyed name and stamped adopted, with zero bytes downloaded."""
    legacy = cache / "firefox-18"
    build_tree(legacy)
    omni_sha = _sha256(resources_of(legacy) / "omni.ja")
    seal = load_seal(write_seal(tmp_path / "s.json", omni_sha256=omni_sha))
    version_dir = cache_dir_for_seal(seal)

    got = ensure_binary(seal=seal)

    assert got == version_dir / entry_rel(), got
    assert not legacy.exists(), "the legacy tree is moved, not copied"
    stamp = read_stamp(version_dir)
    assert stamp and stamp.get("adopted") is True, stamp
    assert stamp.get("seal_digest") == seal.digest


def test_recut_payload_under_the_same_tag_is_not_adopted(cache, no_network, tmp_path):
    """D5: the tag was re-cut, so the tree on disk is the superseded payload.
    Its omni.ja is not the sealed bytes, so adoption must refuse it instead of
    silently keeping two populations on one reported version."""
    legacy = cache / "firefox-18"
    build_tree(legacy)
    seal = load_seal(write_seal(tmp_path / "s.json", omni_sha256="ab" * 32))
    with pytest.raises(NetworkTouched):
        ensure_binary(seal=seal)
    assert (legacy / entry_rel()).exists(), "left in place: it may be the other product's"


# ------------------------------------------------------------------- L7

def test_clear_cache_removes_engines_only(cache, tmp_path):
    """L7: clear-cache used to rmtree the shared root, taking the manager's
    engine and the geoip database with it."""
    geoip = cache / "geoip" / "2026.07.01"
    geoip.mkdir(parents=True)
    (geoip / "geoip-aio-all.mmdb").write_bytes(b"db")
    a = cache / f"firefox-18_{SEALED_VERSION}_{SEALED_BUILD}"
    b = cache / "firefox-14"
    build_tree(a)
    build_tree(b, version=OLD_VERSION, build_id=OLD_BUILD)

    removed = clear_cache("firefox-18")
    assert [p.name for p in removed] == [a.name]
    assert not a.exists() and b.exists()
    assert (geoip / "geoip-aio-all.mmdb").exists(), "geoip must survive"
    assert cache.exists(), "the cache root itself must survive"

    clear_cache(everything=True)
    assert not b.exists()
    assert (geoip / "geoip-aio-all.mmdb").exists()
    assert cache.exists()


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ------------------------------------------------- L7 again: the separator (D6)

def test_clear_cache_without_a_tag_spares_a_longer_tags_tree(cache, sealed, monkeypatch):
    """D6: the no-tag branch selected on `d.name.startswith(seal.tag)` with no
    separator, so a core sealed to firefox-18 would delete firefox-180's engine
    the day that release exists. The tag branch already required the separator;
    both do now.
    """
    monkeypatch.setattr(download, "active_seal", lambda: sealed)
    mine_flat = cache / "firefox-18"
    mine_keyed = cache / f"firefox-18_{SEALED_VERSION}_{SEALED_BUILD}"
    future = cache / "firefox-180_152.0_20270101000000"
    other = cache / "firefox-1"
    for d in (mine_flat, mine_keyed, future, other):
        build_tree(d)

    removed = sorted(p.name for p in clear_cache())

    assert removed == sorted([mine_flat.name, mine_keyed.name]), removed
    assert future.exists(), "firefox-180 is another release, not this one's cache"
    assert other.exists(), "firefox-1 is another release too"


def test_clear_cache_with_an_explicit_tag_spares_a_longer_tag_too(cache):
    """The same predicate, reached the other way."""
    mine = cache / f"firefox-18_{SEALED_VERSION}_{SEALED_BUILD}"
    future = cache / "firefox-180_152.0_20270101000000"
    build_tree(mine)
    build_tree(future)

    removed = [p.name for p in clear_cache("firefox-18")]

    assert removed == [mine.name], removed
    assert future.exists()


# ------------------------------ the refusal a user actually reads (D4)

def test_the_not_adopting_line_names_the_problem(cache, no_network, sealed, capsys):
    """The adoption path printed `e.args[0].splitlines()[3]`, which is the
    `engine says: Firefox X build Y` line: an observation that reads like a
    success. A user watching a 150.0.1 tree be rejected was told "engine says:
    Firefox 150.0.1 build ..." and nothing about what was wrong with it."""
    legacy = cache / "firefox-18"
    build_tree(legacy, version=OLD_VERSION, build_id=OLD_BUILD)
    with pytest.raises(NetworkTouched):
        ensure_binary(seal=sealed)

    lines = [l for l in capsys.readouterr().err.splitlines() if "not adopting" in l]
    assert len(lines) == 1, lines
    line = lines[0]
    assert "engine says" not in line, line
    assert OLD_VERSION in line and SEALED_VERSION in line, line
    assert "!=" in line, line


def test_engine_status_reports_the_problem_not_the_observation(cache, sealed):
    """The profile manager used to render this string next to a red dot,
    until its 2026-08-18 deletion; `doctor` reads it as plain text now."""
    version_dir = cache_dir_for_seal(sealed)
    build_tree(version_dir, version=OLD_VERSION, build_id=OLD_BUILD)

    ok, detail = engine_status(sealed)

    assert ok is False
    assert "engine says" not in detail, detail
    assert OLD_VERSION in detail and SEALED_VERSION in detail, detail
    assert "\n" not in detail, detail
