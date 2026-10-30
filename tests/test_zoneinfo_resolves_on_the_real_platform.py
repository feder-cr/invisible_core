"""zoneinfo is imported when the core is, not on first use.

zoneinfo reads the stdlib's build data for its search path (sysconfig TZPATH)
the first time it is imported, keyed by sys.platform. prefs.py and _geo.py
imported it lazily, so it read that data under whatever platform a caller had
set by then: a test that gives a win32 persona by setting sys.platform on a
Linux runner made sysconfig look for win32's build data and raise
ModuleNotFoundError ('_sysconfigdata__win32_x86_64-linux-gnu'), in the series
CI's core-checks (run 41008922836). On Windows the same code passed.

A fresh process, because the test session may already have zoneinfo loaded.
"""
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


def _fresh(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)


def test_importing_the_core_resolves_zoneinfo():
    r = _fresh("import sys, invisible_core.prefs, invisible_core._geo; print('zoneinfo' in sys.modules)")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "True", "zoneinfo is still imported lazily somewhere in prefs/_geo"


@pytest.mark.skipif(sys.platform == "win32", reason="the failure needs a non-Windows stdlib")
def test_a_win32_persona_set_on_linux_still_lists_the_zones():
    r = _fresh(
        "import sys, invisible_core.prefs as p\n"
        "sys.platform = 'win32'\n"
        "assert 'Europe/Rome' in p._iana_zones()\n")
    assert r.returncode == 0, r.stderr
