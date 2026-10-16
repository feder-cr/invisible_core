"""The branding a page reads is declared by the core, files and all.

A page reaches the browser's branding through chrome://branding/content/ and
resource://usercontext-content/branding/, and what it reads there tells a build
from the retail one. The core ships the files (the retail brand and ours),
accepts a directory of the caller's own, and declares where they are in
`zoom.stealth.branding.dir`. The engine keeps no stealth copy, serves the
declared files, grants its content processes read access to exactly that
directory, and refuses to launch when it is missing or holds anything but
regular files.
"""
import shutil
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
    branding_dir,
)
from invisible_core.prefs import translate_profile_to_prefs

PREF = "zoom.stealth.branding.dir"


def _dir_of(uri: str) -> Path:
    return Path(url2pathname(urlparse(uri).path))


@pytest.fixture
def own_branding(tmp_path):
    d = tmp_path / "mine"
    shutil.copytree(branding_dir("unofficial"), d)
    return d


@pytest.mark.unit
@pytest.mark.parametrize("branding", BRANDING_SETS)
def test_every_shipped_set_holds_exactly_the_branding_files(branding):
    d = branding_dir(branding)
    # Exactly: the engine refuses a directory holding anything else, and the
    # content sandbox is opened to that directory and nothing more.
    assert sorted(p.name for p in d.iterdir()) == sorted(BRANDING_FILES)
    assert all(p.is_file() and not p.is_symlink() for p in d.iterdir())
    # The licence travels with the set, but OUTSIDE the directory a page reads.
    assert (d.parent / "LICENSE").is_file()


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
def test_every_shipped_set_can_be_pinned_and_is_declared_as_its_directory(branding):
    _validate_pin_key("branding")
    assert _PIN_TO_RAW["branding"] == "branding"
    profile = generate_profile(seed=42, pin={"branding": branding})
    uri = translate_profile_to_prefs(profile)[PREF]
    # The form the engine accepts: a file: URI of a directory.
    assert uri.startswith("file:///") and uri.endswith("/") and "\\" not in uri
    assert _dir_of(uri) == branding_dir(branding)


@pytest.mark.unit
def test_a_directory_of_ones_own_is_declared(own_branding):
    profile = generate_profile(seed=42, pin={"branding": str(own_branding)})
    assert profile.branding == str(own_branding.resolve())
    assert _dir_of(translate_profile_to_prefs(profile)[PREF]) == own_branding.resolve()


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["", "Official", "retail", "relative/dir", 1, None])
def test_a_value_that_names_no_branding_is_refused_at_birth(bad):
    with pytest.raises(ValueError, match="neither a set this package ships"):
        generate_profile(seed=42, pin={"branding": bad})


@pytest.mark.unit
def test_a_directory_missing_a_file_is_refused_at_birth(own_branding):
    (own_branding / "aboutDialog.css").unlink()
    with pytest.raises(ValueError, match=r"missing \['aboutDialog.css'\]"):
        generate_profile(seed=42, pin={"branding": str(own_branding)})


@pytest.mark.unit
def test_a_directory_with_an_extra_file_is_refused_at_birth(own_branding):
    # A page could load it, where the retail answers with an error.
    (own_branding / "README.txt").write_bytes(b"x")
    with pytest.raises(ValueError, match=r"extra \['README.txt'\]"):
        generate_profile(seed=42, pin={"branding": str(own_branding)})


@pytest.mark.unit
def test_a_directory_with_a_subdirectory_is_refused_at_birth(own_branding):
    # The engine opens the declared directory to the content sandbox, and a
    # subdirectory would widen that grant to whatever it holds.
    (own_branding / "more").mkdir()
    with pytest.raises(ValueError, match=r"extra \['more'\].*not regular files \['more'\]"):
        generate_profile(seed=42, pin={"branding": str(own_branding)})
