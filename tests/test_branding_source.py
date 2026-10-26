"""The shipped branding archives are what scripts/gen_branding_archives.py made
from the Mozilla tag in branding/SOURCE.json, and nothing edited them since.

A page reads the branding, so it must be the retail's of the Firefox the engine
is built from; the archive is derived from Mozilla's tree, never edited. The
Firefox 157 port found the 151 archive in the core because nothing tied the two
together: the engine's page-surface gate refused aboutDialog.css. SOURCE.json is
that tie, and this test keeps it true.
"""
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from invisible_core._fpforge.profile import BRANDING_FILES, BRANDING_SETS, branding_archive

ROOT = Path(__file__).resolve().parent.parent
SOURCE = json.loads((ROOT / "src" / "invisible_core" / "branding" / "SOURCE.json")
                    .read_text(encoding="utf-8"))


def _generator():
    spec = importlib.util.spec_from_file_location(
        "gen_branding_archives", ROOT / "scripts" / "gen_branding_archives.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_source_names_a_mozilla_release_tag():
    assert SOURCE["mozilla_tag"].startswith("FIREFOX_") and SOURCE["mozilla_tag"].endswith("_RELEASE")
    assert sorted(SOURCE["sets"]) == sorted(BRANDING_SETS)


@pytest.mark.parametrize("branding_set", BRANDING_SETS)
def test_archive_holds_exactly_what_source_records(branding_set):
    with zipfile.ZipFile(branding_archive(branding_set)) as z:
        got = {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}
    assert sorted(got) == sorted(BRANDING_FILES)
    assert got == SOURCE["sets"][branding_set]


@pytest.mark.parametrize("branding_set", BRANDING_SETS)
def test_archive_is_the_generators_bytes(branding_set):
    """Rebuilding from the archive's own files gives the same bytes: the
    archive came out of the generator, not out of a hand-made zip."""
    path = branding_archive(branding_set)
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    assert _generator().archive_bytes(files) == path.read_bytes()


def test_jar_mn_entries_are_read_as_the_build_reads_them():
    jar = ("browser.jar:\n% content branding %content/branding/ contentaccessible=yes\n"
           "  content/branding/about.png\n"
           "  content/branding/icon16.png                    (../default16.png)\n")
    assert _generator().jar_entries(jar) == {"about.png": "about.png",
                                             "icon16.png": "../default16.png"}
