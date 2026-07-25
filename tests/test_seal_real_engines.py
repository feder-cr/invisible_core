"""The guard, pointed at real engines that exist on this machine. Read-only.

Spec: docs/firefox-stealth-architecture/17-release-seal-spec.md section 7
("test_engine_guard_real"). CLAUDE.md: "a gate that has only ever printed PASS
is not a gate" - so the synthetic cases in test_seal_engine_guard.py are backed
here by known-bad inputs that were not built by this test file.

Cells closed here:
  D1  a stock, unpatched Firefox of the CORRECT base version, refused on the
      provenance marker alone. A Version-only guard (the shape proposed in the
      audit's G1) waves this exact tree through, which is why the marker is in
      the design.
  D2/D3  whatever is warm in the developer's real cache, checked against the
      packaged seal: an old tree must be refused, the sealed one must pass.

Nothing here writes, moves or deletes anything: verify_engine reads two ini
files and one zip central directory. Every test skips cleanly when the tree it
needs is absent, so CI stays deterministic.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from invisible_core.seal import (
    DEFAULT_ENTRY_REL,
    JUGGLER_ENTRIES,
    EngineMismatch,
    load_seal,
    packaged_seal_path,
    read_engine_identity,
    resource_root,
    verify_engine,
)

ENTRY_REL = DEFAULT_ENTRY_REL.get(sys.platform, "firefox")


def stock_firefox_trees() -> list[Path]:
    """Playwright's own bundled Firefoxes. They carry Juggler by construction,
    which is exactly what makes them the sharp edge of D1: handed to
    binary_path= they launch, honour the spoofed User-Agent, and produce a
    completely unstealthed engine advertising our claim."""
    roots = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "ms-playwright")
    roots.append(Path.home() / "AppData" / "Local" / "ms-playwright")
    roots.append(Path.home() / ".cache" / "ms-playwright")
    roots.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for d in sorted(root.glob("firefox-*")):
            entry = d / "firefox" / ENTRY_REL
            if entry.exists():
                found.append(entry)
        if found:
            break
    return found


def seal_matching(entry: Path, tmp_path: Path, tag: str = "firefox-18") -> object:
    """A seal that agrees with `entry` on Version and BuildID, so the only thing
    left that can refuse the tree is the provenance marker."""
    ident = read_engine_identity(entry)
    p = tmp_path / "matching.seal.json"
    p.write_text(json.dumps({
        "schema": 2, "tag": tag, "upstream_version": ident.version,
        "build_id": ident.build_id, "source_commit": "",
        "playwright": {"min": "1.55.0", "max": "1.61.0"}, "assets": {},
    }, sort_keys=True), encoding="utf-8")
    return load_seal(p)


def real_cache_dirs() -> list[Path]:
    import platformdirs
    root = Path(platformdirs.user_cache_dir("invisible-playwright"))
    if not root.is_dir():
        return []
    return [d for d in sorted(root.iterdir())
            if d.is_dir() and d.name != "geoip" and not d.name.startswith(".tmp-")]


# ------------------------------------------------- the provenance-only refusal

def test_stock_firefox_of_the_sealed_base_version_is_refused_on_provenance(tmp_path):
    """This is the test that makes the marker earn its place.

    The seal is built to agree with the stock tree on Version AND BuildID, so
    both ini checks pass. The tree is still refused, because a stock build
    carries neither `stealthfox` nor `zoom.stealth` in any of the four juggler
    entries while ours carry one in each.
    """
    trees = stock_firefox_trees()
    if not trees:
        pytest.skip("no bundled stock Firefox on this machine (ms-playwright absent)")

    sealed_version = load_seal(packaged_seal_path()).upstream_version
    same_base = [t for t in trees
                 if (read_engine_identity(t).version or "") == sealed_version]
    if not same_base:
        pytest.skip(f"no stock Firefox at the sealed base version {sealed_version}")
    entry = same_base[0]

    seal = seal_matching(entry, tmp_path)
    ident = read_engine_identity(entry)
    # A naive Version-only check, and even a Version+BuildID check, passes here:
    assert ident.version == seal.upstream_version
    assert ident.build_id == seal.build_id
    assert ident.juggler_present is True, "a stock Playwright build does ship juggler"
    assert ident.marked_entries == 0, (
        f"a stock build must score 0/{len(JUGGLER_ENTRIES)} on the provenance marker; "
        f"got {ident.marked_entries} at {entry}")

    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, seal, source=f"binary_path={entry}")
    msg = str(exc.value).lower()
    assert "marker" in msg or "patched" in msg, str(exc.value)


def test_every_stock_firefox_on_this_machine_is_refused_by_the_packaged_seal(tmp_path):
    """The realistic route, with the seal the package actually ships."""
    trees = stock_firefox_trees()
    if not trees:
        pytest.skip("no bundled stock Firefox on this machine")
    if not packaged_seal_path().exists():
        pytest.skip("no packaged seal to check against")
    seal = load_seal(packaged_seal_path())
    for entry in trees:
        with pytest.raises(EngineMismatch):
            verify_engine(entry, seal, source=f"binary_path={entry}")


def test_stock_tree_provenance_is_measured_not_assumed():
    """Guard the guard: if a future Playwright bundle ever shipped one of our
    marker strings, the provenance check would silently weaken. Assert the
    measurement the design rests on, per release."""
    trees = stock_firefox_trees()
    if not trees:
        pytest.skip("no bundled stock Firefox on this machine")
    for entry in trees:
        ident = read_engine_identity(entry)
        assert ident.marked_entries == 0, (
            f"{entry} scores {ident.marked_entries}/{len(JUGGLER_ENTRIES)}: the provenance "
            f"marker no longer separates stock from ours")


# ------------------------------------------- the developer's real warm caches

def test_real_cached_engines_agree_with_the_packaged_seal_or_are_refused(tmp_path):
    """Every tree the machine has ever fetched is still launchable (old tags are
    never pruned). Each one must either BE the sealed build or be refused."""
    dirs = real_cache_dirs()
    if not dirs:
        pytest.skip("no real engine cache on this machine")
    if not packaged_seal_path().exists():
        pytest.skip("no packaged seal to check against")
    seal = load_seal(packaged_seal_path())

    checked = 0
    for d in dirs:
        entry = d / ENTRY_REL
        if not entry.exists():
            continue
        checked += 1
        ident = read_engine_identity(entry)
        coherent = (ident.version == seal.upstream_version
                    and ident.build_id == seal.build_id
                    and ident.marked_entries >= 2)
        if coherent:
            assert verify_engine(entry, seal, source="real cache") == entry
        else:
            with pytest.raises(EngineMismatch):
                verify_engine(entry, seal, source="real cache")
    if not checked:
        pytest.skip("no readable engine in the real cache")


def test_our_own_patched_builds_carry_the_marker():
    """The other half of the measurement: a build of ours must score 4/4, or
    validate_release.py would have published something the runtime refuses."""
    dirs = real_cache_dirs()
    ours = []
    for d in dirs:
        entry = d / ENTRY_REL
        if entry.exists() and (resource_root(entry) / "omni.ja").exists():
            ours.append(entry)
    if not ours:
        pytest.skip("no cached engine of ours on this machine")
    scores = {str(e): read_engine_identity(e).marked_entries for e in ours}
    assert max(scores.values()) == len(JUGGLER_ENTRIES), scores
