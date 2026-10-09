"""The profile helpers every wrapper imports from `invisible_core.juggler`.

They moved here from invisible-playwright with the Juggler client (decision
D85), and their tests with them: until 0.30.0 the wrapper reached them through
its server's re-export.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler import domain_matches, host_of, remove_profile

pytestmark = pytest.mark.unit


def test_a_cookie_domain_with_a_LEADING_DOT_matches_subdomains():
    """⛔ A leading dot means "and every subdomain". Comparing the two strings
    directly is the version that looks right and returns an empty list, so
    `context.cookies(urls=[...])` would answer nothing for a site-wide
    cookie."""
    assert host_of("https://shop.example.com:8443/a/b") == "shop.example.com"
    assert domain_matches(".example.com", "shop.example.com")
    assert domain_matches("example.com", "example.com")
    assert not domain_matches(".example.com", "notexample.com"), (
        "a suffix match without the dot boundary: badexample.com would pass")
    assert not domain_matches("", "example.com")


def test_removing_a_profile_NEVER_raises():
    """⛔ It runs while the session is already going away, and on Windows a
    file can still be held for a moment after the process that owned it exits.
    A profile left behind costs megabytes; an exception here would be a
    shutdown that fails for a reason nobody cares about."""
    remove_profile("C:/this/path/does/not/exist/at/all")
    remove_profile("")
