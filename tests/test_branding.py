"""The branding a page reads is declared by the core, files and all.

A page reaches the browser's branding through chrome://branding/content/ and
resource://usercontext-content/branding/, and what it reads there tells a build
from the retail one. The core ships the files as a zip archive per set (the
retail brand and ours), accepts an archive of the caller's own, and declares
where it is in `zoom.stealth.branding.archive`. The engine keeps no stealth
copy, serves the archive as jar: - the channel class the retail serves its
branding with, which a file: channel is told apart from - grants its content
processes read access to exactly that file, and refuses to launch when it is
missing or holds anything but the files at its root.
"""
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core._fpforge.profile import (
    BRANDING,
    BRANDING_FILES,
    BRANDING_SETS,
    _PIN_TO_RAW,
    _validate_pin_key,
    branding_archive,
)
from invisible_core.prefs import translate_profile_to_prefs

PREF = "zoom.stealth.branding.archive"


def _file_of(uri: str) -> Path:
    return Path(url2pathname(urlparse(uri).path))


def _write_archive(path: Path, entries: dict) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


@pytest.fixture
def unofficial_entries():
    with zipfile.ZipFile(branding_archive("unofficial")) as z:
        return {n: z.read(n) for n in z.namelist()}


@pytest.fixture
def own_branding(tmp_path, unofficial_entries):
    return _write_archive(tmp_path / "mine.zip", unofficial_entries)


@pytest.mark.unit
@pytest.mark.parametrize("branding", BRANDING_SETS)
def test_every_shipped_set_holds_exactly_the_branding_files_at_its_root(branding):
    archive = branding_archive(branding)
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        # Exactly, and at the root: the engine refuses an archive holding
        # anything else, and a page could load whatever an extra entry is.
        assert sorted(names) == sorted(BRANDING_FILES)
        assert all(not info.is_dir() for info in z.infolist())
    # The licence travels with the set, but OUTSIDE the archive a page reads.
    assert (archive.parent / "LICENSE").is_file()
    assert sorted(p.name for p in archive.parent.iterdir()) == ["LICENSE", "branding.zip"]


@pytest.mark.unit
def test_the_default_is_the_retail_brand():
    assert BRANDING == "official"
    assert generate_profile(seed=42).branding == "official"


@pytest.mark.unit
@pytest.mark.parametrize("seed", [1, 42, 7777, 123456])
def test_the_branding_is_invariant_not_sampled(seed):
    # A brand belongs to the build a session claims to be, not to the machine.
    assert generate_profile(seed=seed).branding == BRANDING


@pytest.mark.unit
@pytest.mark.parametrize("branding", BRANDING_SETS)
def test_every_shipped_set_can_be_pinned_and_is_declared_as_its_archive(branding):
    _validate_pin_key("branding")
    assert _PIN_TO_RAW["branding"] == "branding"
    profile = generate_profile(seed=42, pin={"branding": branding})
    uri = translate_profile_to_prefs(profile)[PREF]
    # The form the engine accepts (StealthBrandingIsDeclaredArchive): a file:
    # URI of a file, without a backslash or a '!', which would end the archive
    # part of the jar: URI the engine builds from it.
    assert uri.startswith("file:///") and not uri.endswith("/")
    assert "\\" not in uri and "!" not in uri
    assert _file_of(uri) == branding_archive(branding)


@pytest.mark.unit
def test_the_directory_pref_is_gone():
    # Renamed before it ever shipped: a directory is the wrong channel class.
    assert "zoom.stealth.branding.dir" not in translate_profile_to_prefs(generate_profile(seed=42))


@pytest.mark.unit
def test_an_archive_of_ones_own_is_declared(own_branding):
    profile = generate_profile(seed=42, pin={"branding": str(own_branding)})
    assert profile.branding == str(own_branding.resolve())
    assert _file_of(translate_profile_to_prefs(profile)[PREF]) == own_branding.resolve()


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["", "Official", "retail", "relative/brand.zip", 1, None])
def test_a_value_that_names_no_branding_is_refused_at_birth(bad):
    with pytest.raises(ValueError, match="neither a set this package ships"):
        generate_profile(seed=42, pin={"branding": bad})


@pytest.mark.unit
def test_a_missing_archive_is_refused_at_birth(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        generate_profile(seed=42, pin={"branding": str(tmp_path / "nope.zip")})


@pytest.mark.unit
def test_a_directory_is_refused_at_birth(tmp_path):
    # The form this field had before the archive: a page tells file: from jar:.
    with pytest.raises(ValueError, match="does not exist"):
        generate_profile(seed=42, pin={"branding": str(tmp_path)})


@pytest.mark.unit
def test_a_file_that_is_not_a_zip_is_refused_at_birth(tmp_path):
    bad = tmp_path / "brand.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="is not a zip"):
        generate_profile(seed=42, pin={"branding": str(bad)})


@pytest.mark.unit
def test_an_archive_missing_a_file_is_refused_at_birth(tmp_path, unofficial_entries):
    del unofficial_entries["aboutDialog.css"]
    archive = _write_archive(tmp_path / "mine.zip", unofficial_entries)
    with pytest.raises(ValueError, match=r"missing \['aboutDialog.css'\]"):
        generate_profile(seed=42, pin={"branding": str(archive)})


@pytest.mark.unit
def test_an_archive_with_an_extra_file_is_refused_at_birth(tmp_path, unofficial_entries):
    # A page could load it, where the retail answers with an error.
    archive = _write_archive(tmp_path / "mine.zip", {**unofficial_entries, "README.txt": b"x"})
    with pytest.raises(ValueError, match=r"extra \['README.txt'\]"):
        generate_profile(seed=42, pin={"branding": str(archive)})


@pytest.mark.unit
def test_an_archive_with_a_nested_entry_is_refused_at_birth(tmp_path, unofficial_entries):
    archive = _write_archive(tmp_path / "mine.zip", {**unofficial_entries, "more/x.png": b"x"})
    with pytest.raises(ValueError, match=r"not at the root \['more/x.png'\]"):
        generate_profile(seed=42, pin={"branding": str(archive)})
