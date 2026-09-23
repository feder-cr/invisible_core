"""The claim gate, and the gate on the gate.

A README that promises to beat everything was live on 2026-09-22 and the check
that found it ran by hand, from a private workbench. These tests are what make
the shared module a gate rather than a script:

  * every known-bad sentence is refused and every must-not-fire one is not;
  * the corpus cannot be quietly emptied;
  * this repository is silent under its own gate;
  * a declared file that does not exist is refused, not read as clean;
  * the command's exit code says which of those happened.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from invisible_core import claims

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _refused(text: str) -> bool:
    return bool(claims.scan_unsupportable(text) or claims.scan_fraud(text))


@pytest.mark.parametrize("name,text", claims.KNOWN_BAD, ids=[n for n, _ in claims.KNOWN_BAD])
def test_every_known_bad_sentence_is_refused(name, text):
    assert _refused(text), name


@pytest.mark.parametrize("name,text", claims.MUST_NOT_FIRE,
                         ids=[n for n, _ in claims.MUST_NOT_FIRE])
def test_no_must_not_fire_sentence_is_refused(name, text):
    assert not _refused(text), name


def test_the_corpus_covers_each_class_it_claims_to():
    """An emptied corpus passes every test above. The floor is the number of
    variation classes the module documents, not a number picked to pass."""
    assert len(claims.KNOWN_BAD) >= 7
    assert len(claims.MUST_NOT_FIRE) >= 8


def test_an_open_claim_is_still_reported():
    """The report half: a relaxed gate that stopped seeing anything is the
    failure mode of a gate that only informs."""
    assert claims.scan_claims("It bypasses Cloudflare bot detection.")
    assert not _refused("It bypasses Cloudflare bot detection.")


def test_this_repository_is_silent_under_its_own_gate():
    refused, _reported, missing = claims.judge(_ROOT, claims.files_for(_ROOT))
    assert not missing, missing
    assert not refused, refused


def test_the_default_perimeter_is_the_readme(tmp_path):
    assert claims.files_for(tmp_path) == ("README.md",)
    (tmp_path / "pyproject.toml").write_bytes(b"[project]\nname = 'x'\n")
    assert claims.files_for(tmp_path) == ("README.md",)


def test_a_declared_file_that_does_not_exist_is_refused(tmp_path):
    (tmp_path / "pyproject.toml").write_bytes(
        b"[tool.invisible.claims]\nfiles = ['README.md', 'docs/gone.md']\n")
    (tmp_path / "README.md").write_bytes(b"A patched Firefox for Python.\n")
    _refused_, _reported, missing = claims.judge(tmp_path, claims.files_for(tmp_path))
    assert missing == ["docs/gone.md"]
    assert claims.main(["--root", str(tmp_path)]) == 1


def test_the_command_refuses_a_bad_readme_and_passes_a_good_one(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_bytes(b"# x\n\nOpen source, and it passes every bot detection test.\n")
    assert claims.main(["--root", str(tmp_path)]) == 1
    readme.write_bytes(b"# x\n\nIt bypasses Cloudflare bot detection on the sites we measured.\n")
    assert claims.main(["--root", str(tmp_path)]) == 0


def test_the_selftest_passes_as_a_subprocess():
    """`--selftest` is what consumer CI runs, so it is proven the way it runs."""
    r = subprocess.run([sys.executable, "-m", "invisible_core.claims", "--selftest"],
                       cwd=str(_ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL GOOD" in r.stdout
