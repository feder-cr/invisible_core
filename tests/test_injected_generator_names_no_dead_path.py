"""The injected-script generator has no default bundle path pointing at a folder that does not exist.

⛔ KNOWN-BAD. `--bundle` defaulted to `_driver/package/lib/coreBundle.js`, a
folder no tree has had since the Node driver went: the default could never be
right, and it read as if the script were broken rather than the bundle gone.
Now a bundle must be named (--bundle or INVPW_DRIVER_BUNDLE); without one the
script stops with exit 2 and says how to get one.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit
ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "gen_injected_source.py"


def test_no_default_path_into_a_missing_folder():
    assert "_driver" not in SCRIPT.read_text(encoding="utf-8").split('ap.add_argument("--out"')[0].split("def main")[1]


def test_without_a_bundle_it_stops_and_says_how_to_get_one():
    env = {k: v for k, v in os.environ.items() if k != "INVPW_DRIVER_BUNDLE"}
    r = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no driver bundle named" in r.stdout and "INVPW_DRIVER_BUNDLE" in r.stdout
