"""The engine runs with Fission and one content process per site (firefox-62).

The Juggler of firefox-62 reaches a frame in another process like any other: a command goes to the root that
holds the frame, the parent announces the frames, a frame tree is kept per root. So the baseline no longer pins
the single-process model that firefox-47..61 needed (`webContentIsolationStrategy` 0), and declares what retail
Firefox on desktop runs with:

  fission.autostart                          True   Fission on
  fission.webContentIsolationStrategy        1      IsolateEverything: one remote type per site
  dom.ipc.processCount.webIsolated           4      the desktop default

`fission.autostart.session` is not declared: the parent writes it at startup (nsAppRunner.cpp), so a value here
would be overwritten, or read as a decision it is not. Each pref is declared once, on its own line.
"""
import re
from pathlib import Path

import pytest

from invisible_core import prefs as P
from invisible_core._fpforge import generate_profile

STRATEGY_1 = {
    "fission.autostart": True,
    "fission.webContentIsolationStrategy": 1,
    "dom.ipc.processCount.webIsolated": 4,
}


@pytest.mark.unit
def test_the_baseline_runs_every_site_in_its_own_process():
    for name, value in STRATEGY_1.items():
        assert P._BASELINE[name] == value, name
        assert type(P._BASELINE[name]) is type(value), name


@pytest.mark.unit
def test_the_session_pref_is_the_parents_to_write():
    assert "fission.autostart.session" not in P._BASELINE


@pytest.mark.unit
def test_a_launch_gets_them_with_no_extra_prefs():
    prefs = P.translate_profile_to_prefs(generate_profile(seed=42), timezone="America/New_York")
    for name, value in STRATEGY_1.items():
        assert prefs[name] == value, name


@pytest.mark.unit
def test_a_caller_can_still_turn_it_off_explicitly():
    prefs = P.translate_profile_to_prefs(
        generate_profile(seed=42), timezone="America/New_York",
        extra_prefs={"fission.webContentIsolationStrategy": 0, "fission.autostart": False})
    assert prefs["fission.webContentIsolationStrategy"] == 0
    assert prefs["fission.autostart"] is False


@pytest.mark.unit
def test_each_pref_is_declared_once():
    source = Path(P.__file__).read_text(encoding="utf-8")
    for name in list(STRATEGY_1) + ["fission.autostart.session"]:
        declarations = re.findall(r'^\s*"%s"\s*:' % re.escape(name), source, flags=re.M)
        assert len(declarations) == (0 if name == "fission.autostart.session" else 1), (name, len(declarations))
