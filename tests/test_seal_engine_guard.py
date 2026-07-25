"""verify_engine: every refusal the Release Seal promises. No browser, no network.

Spec: docs/firefox-stealth-architecture/17-release-seal-spec.md sections 4.6 and 7.
Audit: docs/firefox-stealth-architecture/16-version-divergence-matrix.md section 2a.

Cells closed here:
  D3 / D4  a warm engine from another release running under a newer claim
  D1       a foreign engine of the SAME base version but a different build
  D1       a stock (unpatched) engine, refused on provenance alone
  L6/L8    a tree with no juggler, and a doctored application.ini

Everything is synthetic: a directory with application.ini, platform.ini, a real
omni.ja zip holding the four juggler entries, and a stub executable. That is the
whole input surface verify_engine reads, so no real engine is needed and nothing
outside tmp_path is touched.
"""
from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from invisible_core.seal import (
    DEFAULT_ENTRY_REL,
    JUGGLER_ENTRIES,
    EngineMismatch,
    SealError,
    load_seal,
    read_engine_identity,
    verify_engine,
)

# The real numbers this machine carries, so the fixtures describe reality:
#   firefox-18 -> 151.0    build 20260724001949   (the sealed build)
#   PW stock   -> 151.0    build 20260611193205   (same base, foreign build)
#   firefox-14 -> 150.0.1  build 20260624073725   (the warm old engine)
SEALED_VERSION = "151.0"
SEALED_BUILD = "20260724001949"
FOREIGN_BUILD = "20260611193205"
OLD_VERSION = "150.0.1"
OLD_BUILD = "20260624073725"

APP_INI = ("[App]\nVendor=Mozilla\nName=Firefox\nVersion={v}\nBuildID={b}\n"
           "ID={{ec8030f7-c20a-464f-9b0e-13a3a9e97384}}\n\n"
           "[Gecko]\nMinVersion={v}\nMaxVersion={v}\n")
PLAT_INI = "[Build]\nBuildID={b}\nMilestone={v}\nSourceStamp=deadbeef\n"


def entry_rel() -> str:
    return DEFAULT_ENTRY_REL.get(sys.platform, "firefox")


def resources_of(root: Path) -> Path:
    """Where application.ini lives for `root`, computed independently of the
    code under test (the darwin bundle puts it under Contents/Resources)."""
    if sys.platform == "darwin":
        return root / "Firefox.app" / "Contents" / "Resources"
    return root


def build_tree(root: Path, *, version: str = SEALED_VERSION, build_id: str = SEALED_BUILD,
               plat_version: str | None = None, plat_build: str | None = None,
               marked: int = 4, juggler: bool = True, omni: bool = True) -> Path:
    """Create a synthetic engine tree. Returns the entry executable path."""
    res = resources_of(root)
    res.mkdir(parents=True, exist_ok=True)
    (res / "application.ini").write_text(APP_INI.format(v=version, b=build_id), encoding="utf-8")
    (res / "platform.ini").write_text(
        PLAT_INI.format(v=plat_version or version, b=plat_build or build_id), encoding="utf-8")
    if omni:
        with zipfile.ZipFile(res / "omni.ja", "w") as zf:
            if juggler:
                for i, name in enumerate(JUGGLER_ENTRIES):
                    # a marked entry carries one of the two provenance strings
                    zf.writestr(name, b"Services.prefs.getIntPref('zoom.stealth.hw_seed', -1)"
                                if i < marked else b"// vanilla juggler entry")
            zf.writestr("modules/AppConstants.sys.mjs", b"// unrelated")
    entry = root / entry_rel()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(b"MZ\x90\x00")
    return entry


def write_seal(path: Path, *, tag: str = "firefox-18", version: str = SEALED_VERSION,
               build_id: str = SEALED_BUILD, assets: dict | None = None) -> Path:
    path.write_text(json.dumps({
        "schema": 2, "tag": tag, "upstream_version": version, "build_id": build_id,
        "source_commit": "0123456789abcdef", "playwright": {"min": "1.55.0", "max": "1.61.0"},
        "assets": assets or {},
    }, sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture
def sealed(tmp_path):
    """The seal under test: firefox-18, Firefox 151.0, the sealed BuildID."""
    return load_seal(write_seal(tmp_path / "firefox-18.seal.json"))


# --------------------------------------------------------------- the baseline

def test_sealed_tree_passes(tmp_path, sealed):
    """Without this, every refusal below could come from a guard that refuses
    everything, which proves nothing."""
    entry = build_tree(tmp_path / "sealed")
    assert verify_engine(entry, sealed, source="unit") == entry


# ------------------------------------------------- D3 / D4: the warm old tree

def test_old_engine_under_a_newer_seal_is_refused(tmp_path, sealed):
    """D3/D4: the maintainer half-edit (or the rollback), warm cache. The engine
    is 150.0.1 while the config claims 151.0 - silent today, loud here."""
    entry = build_tree(tmp_path / "warm", version=OLD_VERSION, build_id=OLD_BUILD)
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, sealed, source="cache hit firefox-18")
    msg = str(exc.value)
    assert OLD_VERSION in msg, msg
    assert SEALED_VERSION in msg, msg
    assert "origin" in msg and "cache hit firefox-18" in msg, msg


# ---------------------------------------------- D1: same version, other build

def test_same_version_different_build_is_refused(tmp_path, sealed):
    """D1 in the shape this machine actually carries: a bundled stock Firefox
    151.0. The base version agrees, so only the BuildID separates them."""
    entry = build_tree(tmp_path / "foreign", version=SEALED_VERSION, build_id=FOREIGN_BUILD)
    ident = read_engine_identity(entry)
    assert ident.version == sealed.upstream_version, "the version-only check must pass here"

    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, sealed, source="binary_path=<foreign>")
    msg = str(exc.value)
    assert FOREIGN_BUILD in msg and SEALED_BUILD in msg, msg


# -------------------------------------------------------- D1: provenance only

def test_unpatched_tree_is_refused_even_when_version_and_build_match(tmp_path, sealed):
    """The marker earns its place. Version, BuildID and Milestone all agree with
    the seal; the only thing missing is the juggler provenance. A Version-only
    guard (and a Version+BuildID one) waves this through."""
    entry = build_tree(tmp_path / "stock", marked=0)
    ident = read_engine_identity(entry)
    assert (ident.version, ident.build_id) == (sealed.upstream_version, sealed.build_id)
    assert ident.marked_entries == 0
    assert ident.juggler_present is True, "a stock build still ships juggler entries"

    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, sealed, source="binary_path=<stock>")
    msg = str(exc.value).lower()
    assert "marker" in msg or "patched" in msg, msg


def test_tree_without_juggler_is_refused(tmp_path, sealed):
    """The old BROKEN_VERSIONS class (firefox-8 shipped with no juggler), now
    checked on every route instead of only inside ensure_binary."""
    entry = build_tree(tmp_path / "nojuggler", juggler=False)
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, sealed, source="unit")
    assert "juggler" in str(exc.value).lower()


def test_marker_erosion_is_tolerated_down_to_two_of_four(tmp_path, sealed):
    """A partial juggler restructure must not brick every user at once, so the
    runtime floor is 2 of 4 while validate_release.py demands 4 of 4."""
    ok = build_tree(tmp_path / "eroded2", marked=2)
    assert verify_engine(ok, sealed, source="unit") == ok

    bad = build_tree(tmp_path / "eroded1", marked=1)
    with pytest.raises(EngineMismatch):
        verify_engine(bad, sealed, source="unit")


# ------------------------------------------------------ a doctored ini, and IO

def test_doctored_application_ini_is_caught_by_platform_ini(tmp_path, sealed):
    """application.ini is a text file anyone can edit. platform.ini is the
    second fact that has to agree, which is what the manager arm relies on
    (it has no protocol channel)."""
    entry = build_tree(tmp_path / "doctored", version=SEALED_VERSION, build_id=SEALED_BUILD,
                       plat_version=OLD_VERSION, plat_build=OLD_BUILD)
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, sealed, source="unit")
    assert "platform.ini" in str(exc.value)


def test_missing_executable_is_reported_as_missing(tmp_path, sealed):
    """Message quality: a path that does not exist must say so, not blame
    omni.ja. This is the message a user gets after a typo in binary_path=."""
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(tmp_path / "nowhere" / entry_rel(), sealed, source="binary_path=typo")
    msg = str(exc.value)
    assert "no executable" in msg.lower(), msg
    assert "omni.ja" not in msg, msg


def test_unreadable_omni_is_refused_not_ignored(tmp_path, sealed):
    """A truncated omni.ja is a silent stealth loss if it is merely skipped."""
    entry = build_tree(tmp_path / "truncated")
    (resources_of(tmp_path / "truncated") / "omni.ja").write_bytes(b"not a zip at all")
    with pytest.raises(EngineMismatch):
        verify_engine(entry, sealed, source="unit")


# ------------------------------------------- five legs, five BuildIDs (defect 1)
#
# Measured on the five published firefox-18 archives, 2026-07-25: one Version
# (151.0) and five different application.ini BuildIDs, because they are five
# independent CI builds. A seal with one scalar build_id matched the leg whose
# value happened to be copied into it and REFUSED THE LAUNCH on the other four
# platforms. Everything below is host-independent: the leg a tree belongs to is
# read off the executable it was handed, not off the machine running the test.

LEG_BUILDS = {
    "linux-arm64": "20260724001621",
    "linux-x86_64": "20260724001829",
    "macos-arm64": "20260724001606",
    "macos-x86_64": "20260724001555",
    "win-x86_64": "20260724001949",
}
FIVE_LEG_ASSETS = {
    "firefox-151.0-stealth-linux-arm64.tar.gz": {
        "platform": "linux", "arch": "arm64", "build_id": LEG_BUILDS["linux-arm64"],
        "sha256": "11" * 32, "size": 1, "entry_rel": "firefox", "omni_sha256": ""},
    "firefox-151.0-stealth-linux-x86_64.tar.gz": {
        "platform": "linux", "arch": "x86_64", "build_id": LEG_BUILDS["linux-x86_64"],
        "sha256": "22" * 32, "size": 1, "entry_rel": "firefox", "omni_sha256": ""},
    "firefox-151.0-stealth-macos-arm64.tar.gz": {
        "platform": "darwin", "arch": "arm64", "build_id": LEG_BUILDS["macos-arm64"],
        "sha256": "33" * 32, "size": 1,
        "entry_rel": "Firefox.app/Contents/MacOS/firefox", "omni_sha256": "aa" * 32},
    "firefox-151.0-stealth-macos-x86_64.tar.gz": {
        "platform": "darwin", "arch": "x86_64", "build_id": LEG_BUILDS["macos-x86_64"],
        "sha256": "44" * 32, "size": 1,
        "entry_rel": "Firefox.app/Contents/MacOS/firefox", "omni_sha256": "bb" * 32},
    "firefox-151.0-stealth-win-x86_64.zip": {
        "platform": "win32", "arch": "x86_64", "build_id": LEG_BUILDS["win-x86_64"],
        "sha256": "55" * 32, "size": 1, "entry_rel": "firefox.exe", "omni_sha256": "cc" * 32},
}


@pytest.fixture
def five_legs(tmp_path):
    """A release seal shaped like a real one: one Version, five BuildIDs."""
    p = tmp_path / "five.seal.json"
    p.write_text(json.dumps({
        "schema": 2, "tag": "firefox-18", "upstream_version": SEALED_VERSION,
        "source_commit": "0123456789abcdef",
        "playwright": {"min": "1.55.0", "max": "1.61.0"}, "assets": FIVE_LEG_ASSETS,
    }, sort_keys=True), encoding="utf-8")
    return load_seal(p)


def build_leg_tree(root: Path, entry_rel: str, *, build_id: str,
                   version: str = SEALED_VERSION, marked: int = 4, omni: bool = True) -> Path:
    """A tree for a NAMED leg, independent of the host platform."""
    entry = root / entry_rel
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(b"MZ\x90\x00")
    res = entry.parent
    if entry.parent.name == "MacOS":
        res = entry.parent.parent / "Resources"
        res.mkdir(parents=True, exist_ok=True)
    (res / "application.ini").write_text(APP_INI.format(v=version, b=build_id), encoding="utf-8")
    (res / "platform.ini").write_text(PLAT_INI.format(v=version, b=build_id), encoding="utf-8")
    if omni:
        with zipfile.ZipFile(res / "omni.ja", "w") as zf:
            for i, name in enumerate(JUGGLER_ENTRIES):
                zf.writestr(name, b"'zoom.stealth.hw_seed'" if i < marked else b"// vanilla")
    else:  # the Linux layout: juggler loose in the tree, no omni.ja at all
        for i, name in enumerate(JUGGLER_ENTRIES):
            p = res / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"'zoom.stealth.hw_seed'" if i < marked else b"// vanilla")
    return entry


@pytest.mark.parametrize("leg,entry_rel,omni", [
    # Every leg, from any host: which leg a tree IS comes from the tree, so this
    # runs identically on the five platforms rather than only on the one the
    # seal's scalar happened to name.
    ("linux-arm64", "firefox", False),
    ("linux-x86_64", "firefox", False),
    ("macos-arm64", "Firefox.app/Contents/MacOS/firefox", True),
    ("macos-x86_64", "Firefox.app/Contents/MacOS/firefox", True),
    ("win-x86_64", "firefox.exe", True),
])
def test_every_published_leg_verifies_against_the_one_seal(tmp_path, five_legs, leg,
                                                           entry_rel, omni):
    """Defect 1, directly: with one scalar build_id, four of these five are a
    refusal at launch on a correct, freshly downloaded engine."""
    entry = build_leg_tree(tmp_path / leg, entry_rel, build_id=LEG_BUILDS[leg], omni=omni)
    assert verify_engine(entry, five_legs, source=f"leg {leg}") == entry


def test_a_leg_carrying_another_legs_build_id_is_refused(tmp_path, five_legs):
    """The known-bad input for the fix: per-asset must not degrade into "any
    BuildID in the seal". A Windows tree stamped with the Linux leg's BuildID is
    a tree from a different build and must not launch."""
    entry = build_leg_tree(tmp_path / "wrong-leg", "firefox.exe",
                           build_id=LEG_BUILDS["linux-x86_64"])
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, five_legs, source="binary_path=<wrong leg>")
    msg = str(exc.value)
    assert LEG_BUILDS["linux-x86_64"] in msg and LEG_BUILDS["win-x86_64"] in msg, msg


def test_the_asset_pins_the_comparison_to_exactly_one_leg(tmp_path, five_legs):
    """The download path knows which leg it fetched, so it checks against that
    one and not against "some Linux build"."""
    asset = five_legs.asset_for("linux", "x86_64")
    entry = build_leg_tree(tmp_path / "arm-under-x86", "firefox",
                           build_id=LEG_BUILDS["linux-arm64"], omni=False)
    # Without the asset all that is knowable from the tree is "a Linux leg", and
    # both Linux legs are Linux legs. With it, the comparison is one value.
    assert verify_engine(entry, five_legs, source="unit") == entry
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, five_legs, source="fresh download", asset=asset)
    assert LEG_BUILDS["linux-x86_64"] in str(exc.value)


def test_expected_build_ids_narrow_to_the_leg(five_legs):
    for name, a in FIVE_LEG_ASSETS.items():
        got = five_legs.expected_build_ids(platform_key=a["platform"], arch=a["arch"])
        assert got == (a["build_id"],), (name, got)
    assert set(five_legs.expected_build_ids(platform_key="linux")) == {
        LEG_BUILDS["linux-arm64"], LEG_BUILDS["linux-x86_64"]}


def test_a_schema_1_seal_is_refused_with_a_reason(tmp_path):
    """A seal written before the split carries one BuildID for five legs, so it
    cannot be read forward. Refuse it, and say why."""
    p = tmp_path / "old.seal.json"
    p.write_text(json.dumps({
        "schema": 1, "tag": "firefox-18", "upstream_version": SEALED_VERSION,
        "build_id": SEALED_BUILD, "source_commit": "",
        "playwright": {"min": "1.55.0", "max": "1.61.0"}, "assets": {},
    }, sort_keys=True), encoding="utf-8")
    with pytest.raises(SealError) as exc:
        load_seal(p)
    msg = str(exc.value)
    assert "schema 1" in msg and "BuildID" in msg, msg


def test_an_asset_without_a_build_id_is_refused(tmp_path):
    """The per-asset BuildID is the authority, so a seal that omits one has
    nothing to verify that leg against. Silently defaulting to the top-level
    value is exactly the bug, so the loader must not accept the shape at all."""
    assets = {k: dict(v) for k, v in FIVE_LEG_ASSETS.items()}
    assets["firefox-151.0-stealth-linux-x86_64.tar.gz"].pop("build_id")
    p = tmp_path / "gappy.seal.json"
    p.write_text(json.dumps({
        "schema": 2, "tag": "firefox-18", "upstream_version": SEALED_VERSION,
        "build_id": SEALED_BUILD, "source_commit": "",
        "playwright": {"min": "1.55.0", "max": "1.61.0"}, "assets": assets,
    }, sort_keys=True), encoding="utf-8")
    with pytest.raises(SealError) as exc:
        load_seal(p)
    assert "linux-x86_64" in str(exc.value), str(exc.value)


# ------------------------------------- the unpacked juggler layout (defect 2)
#
# Measured on firefox-18: the two Linux archives contain ZERO *.ja members and
# ship chrome/juggler/ loose in the tree (linux_release.sh tars the pre-package
# dist/bin layout). A reader that only knows omni.ja refuses every Linux launch.

def test_a_loose_juggler_tree_is_read_not_refused(tmp_path, five_legs):
    entry = build_leg_tree(tmp_path / "loose", "firefox",
                           build_id=LEG_BUILDS["linux-x86_64"], omni=False)
    ident = read_engine_identity(entry)
    assert ident.juggler_layout == "loose"
    assert ident.juggler_present is True
    assert ident.marked_entries == len(JUGGLER_ENTRIES)
    assert verify_engine(entry, five_legs, source="unit") == entry


def test_a_loose_stock_tree_is_still_refused_on_provenance(tmp_path, five_legs):
    """The known-bad input for the fallback: reading the other layout must not
    turn into accepting anything that has no omni.ja. An unpatched tree scores
    0/4 in the loose layout too, and is refused there too."""
    entry = build_leg_tree(tmp_path / "loose-stock", "firefox",
                           build_id=LEG_BUILDS["linux-x86_64"], omni=False, marked=0)
    ident = read_engine_identity(entry)
    assert ident.juggler_layout == "loose" and ident.marked_entries == 0
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, five_legs, source="unit")
    msg = str(exc.value).lower()
    assert "marker" in msg or "patched" in msg, msg


def test_a_tree_with_neither_layout_is_refused(tmp_path, five_legs):
    """No omni.ja AND no chrome/juggler/: the absence is not a pass."""
    root = tmp_path / "empty"
    entry = build_leg_tree(root, "firefox", build_id=LEG_BUILDS["linux-x86_64"], omni=False)
    shutil.rmtree(root / "chrome")
    ident = read_engine_identity(entry)
    assert ident.juggler_layout == "none" and ident.juggler_present is False
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, five_legs, source="unit")
    assert "juggler" in str(exc.value).lower()


# ------------------------------- the refusal carries its reason as data (D4)
#
# Two callers render ONE line out of a refusal: the adoption log line in
# download.py and engine_status(), which the manager paints next to a red dot.
# Both did `e.args[0].splitlines()[3].strip()`. Index 3 of the rendered message
# is `engine says: Firefox X build Y` - an observation, not the problem - so
# every refusal was reported with a line that reads like a success, and any edit
# to the layout moved which line they showed.

def test_the_refusal_carries_every_problem_as_structured_data(tmp_path, sealed):
    entry = build_tree(tmp_path / "warm", version=OLD_VERSION, build_id=OLD_BUILD)
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, sealed, source="cache hit firefox-18")
    e = exc.value

    from invisible_core.seal import engine_problems
    expected = engine_problems(read_engine_identity(entry), sealed)
    assert list(e.problems) == expected, e.problems
    assert e.problems, "a refusal with no reason attached"
    assert e.entry == entry
    assert e.seal_tag == sealed.tag


def test_the_one_line_summary_is_the_problem_not_the_observation(tmp_path, sealed):
    """The exact defect: the line the old index selected reads like a success."""
    entry = build_tree(tmp_path / "warm", version=OLD_VERSION, build_id=OLD_BUILD)
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, sealed, source="cache hit firefox-18")
    e = exc.value

    old_line = str(e).splitlines()[3].strip()
    assert old_line.startswith("engine says"), \
        "the fixture no longer reproduces the defect - re-point it at the layout"
    assert "engine says" not in e.summary, e.summary
    assert e.summary != old_line
    assert e.problems[0] in e.summary
    # More than one thing is wrong with this tree, and the one line says so
    # rather than pretending the first is the whole story.
    assert f"(+{len(e.problems) - 1} more)" in e.summary, e.summary


def test_the_summary_survives_an_edit_to_the_message_layout(tmp_path, sealed):
    """A line index is a coupling to the prose. `.problems` is not: prove it by
    asserting the summary is computed from the data, with no reference to how
    many lines the message happens to have."""
    entry = build_tree(tmp_path / "warm", version=OLD_VERSION, build_id=OLD_BUILD)
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(entry, sealed, source="unit")
    e = exc.value
    rebuilt = EngineMismatch("a completely different one-line message",
                             problems=e.problems, entry=e.entry, seal_tag=e.seal_tag)
    assert rebuilt.summary == e.summary


def test_a_missing_executable_summarises_as_missing(tmp_path, sealed):
    """The other raise site. It has no problem list of its own, so it supplies
    one instead of leaving the summary to fall back on prose."""
    with pytest.raises(EngineMismatch) as exc:
        verify_engine(tmp_path / "nowhere" / entry_rel(), sealed, source="binary_path=typo")
    e = exc.value
    assert e.problems, "a refusal with no reason attached"
    assert "no executable" in e.summary.lower(), e.summary
    assert "\n" not in e.summary, "a summary is one line"


def test_a_summary_is_always_one_line(tmp_path, sealed):
    """Whatever goes wrong, the string a UI renders is renderable."""
    for name, kwargs in [
        ("old", dict(version=OLD_VERSION, build_id=OLD_BUILD)),
        ("stock", dict(marked=0)),
        ("nojuggler", dict(juggler=False)),
    ]:
        entry = build_tree(tmp_path / name, **kwargs)
        with pytest.raises(EngineMismatch) as exc:
            verify_engine(entry, sealed, source="unit")
        s = exc.value.summary
        assert s and "\n" not in s and s == s.strip(), (name, repr(s))


# ----------------------------------------- the remedies we print (D5)

def test_no_refusal_tells_a_user_to_install_from_git(tmp_path, sealed):
    """A `pip install git+https://...` installs a PEP 508 direct reference,
    which carries no version specifier: it bypasses the `invisible-core==N.N.N`
    both consumers declare and leaves `pip check` with nothing to compare. Every
    remedy this package prints has to be the index form."""
    import invisible_core.seal as seal_mod

    src = Path(seal_mod.__file__).read_text(encoding="utf-8")
    offenders = [l.strip() for l in src.splitlines() if "pip install" in l and "git+" in l]
    assert not offenders, f"a git-install remedy is back in seal.py: {offenders}"
    assert "invisible-core" in seal_mod.CORE_INSTALL_HINT
    assert "git+" not in seal_mod.CORE_INSTALL_HINT
    assert "git+" not in seal_mod.CORE_REINSTALL_HINT

    # and the two messages that actually carry them
    p = tmp_path / "s1.json"
    p.write_text(json.dumps({
        "schema": 1, "tag": "firefox-18", "upstream_version": SEALED_VERSION,
        "build_id": SEALED_BUILD, "assets": {},
    }, sort_keys=True), encoding="utf-8")
    with pytest.raises(SealError) as exc:
        load_seal(p)
    assert seal_mod.CORE_INSTALL_HINT in str(exc.value), str(exc.value)

    with pytest.raises(SealError) as exc:
        load_seal(tmp_path / "does-not-exist.json")
    assert seal_mod.CORE_REINSTALL_HINT in str(exc.value), str(exc.value)
