"""The core declares nothing about the launch counter: its address is compiled into the engine, and no pref moves or silences it.

The counter is part of invisible-playwright and of the engine it launches (counter/mandatory): the engine asks its own fixed address and reads no
pref for it. So a session's prefs carry no `usage_ping` key from the core, and a key a caller names is an ordinary pref the engine does not read
(it is not refused: there is nothing it could change).

Known-bad input: the core as it was (it wrote `invisible_firefox.usage_ping.url` on every session) -> red.
"""
from __future__ import annotations

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import compose_session_prefs


def test_no_session_carries_a_launch_counter_pref_from_the_core():
    profile = generate_profile(seed=4242)
    for virtual_display in (False, True):
        prefs = compose_session_prefs(profile, virtual_display=virtual_display, timezone="America/New_York").prefs
        assert not [k for k in prefs if "usage_ping" in k], [k for k in prefs if "usage_ping" in k]
