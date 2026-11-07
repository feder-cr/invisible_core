"""Every `scripts/<name>.py` the package names is a script this repository has.

⛔ KNOWN-BAD. The publish refusal for a local seal told the operator to run
`python scripts/roll_seal.py <firefox-N>`, and seal.py's docstring said the seal
was copied in by it: the script does not exist. An operator following the
refusal hit "No such file" at the one moment the instruction mattered. The seal
is copied from the release asset (`gh release download firefox-N -p seal.json`).
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "invisible_core"
#: Scripts named on purpose that live elsewhere, each with where.
ELSEWHERE = {
    "make_seal.py": "the patch series (platform/ci/src/scripts)",
    "gen_bundle_font_manifest.py": "the patch series (platform/ci/src/scripts)",
    "run_e2e.py": "each consumer (invisible_playwright/scripts)",
    "sync_core_pin.py": "the local workbench, outside every repository (hooks.py)",
    "gen_x.py": "an example path inside english.py's own test table",
}
SERIES_SCRIPTS = set(ELSEWHERE)


def _named_scripts():
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name in re.findall(r"\bscripts/([A-Za-z0-9_]+\.py)\b", text):
            yield path.relative_to(ROOT).as_posix(), name


def test_every_named_script_exists():
    missing = sorted({(where, name) for where, name in _named_scripts()
                      if name not in SERIES_SCRIPTS and not (ROOT / "scripts" / name).exists()})
    assert missing == [], "the package names scripts this repository does not have: %r" % missing


def test_the_check_sees_the_old_name():
    """The scan itself: the known-bad name would have been caught."""
    assert re.findall(r"\bscripts/([A-Za-z0-9_]+\.py)\b", "run python scripts/roll_seal.py now") == ["roll_seal.py"]
    assert not (ROOT / "scripts" / "roll_seal.py").exists()
