"""The launch counter's address is declared by the core, for every session.

The engine fetches a five-byte release asset once at startup and the asset's
download count is the number of launches. The address was a repository name
compiled into every shipped binary, and it died twice that way: once when the
repository was renamed and its old name reused, once when the repository was
deleted. Since firefox-21 the engine reads it from the pref
`invisible_firefox.usage_ping.url`, so that moving the counter is a line in
`prefs.py` and not a rebuild. This is that line, and the tests that keep it.

The address is the engine's own repository: the thing that pings and the
thing that is counted are the same release series. Engines older than
firefox-21 do not read the pref and keep asking the address compiled into them.

Known-bad: remove the `setdefault` and the first test goes red on every
session; point the constant elsewhere and the second does.
"""
from __future__ import annotations

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import USAGE_PING_URL, compose_session_prefs

KEY = "invisible_firefox.usage_ping.url"


def test_every_session_declares_where_to_report_a_launch():
    profile = generate_profile(seed=4242)
    for cloak in (False, True):
        prefs = compose_session_prefs(profile, cloak=cloak).prefs
        assert prefs[KEY] == USAGE_PING_URL, (
            "a session with cloak=%r would let the engine fall back to the "
            "address compiled into it" % cloak)


def test_the_address_is_the_engine_repository():
    assert USAGE_PING_URL == (
        "https://github.com/feder-cr/firefox_antidetect_patch"
        "/releases/download/usage-counter/launch.txt")


def test_a_caller_can_still_point_it_elsewhere_or_turn_it_off():
    """setdefault, so an explicit override wins: the pref exists to be movable,
    and the enabled switch is the documented way to opt out."""
    profile = generate_profile(seed=7)
    moved = compose_session_prefs(profile, extra_prefs={KEY: "https://example.invalid/x"}).prefs
    assert moved[KEY] == "https://example.invalid/x"
    off = compose_session_prefs(
        profile, extra_prefs={"invisible_firefox.usage_ping.enabled": False}).prefs
    assert off["invisible_firefox.usage_ping.enabled"] is False
