"""The public config API's humanize default, and what it hands a caller.

`get_default_stealth_prefs` is for somebody driving the patched binary with
their own Playwright - nobody inside this project's own packages calls it
(three when this was written - invisible_core, invisible_playwright,
invisible_firefox; two since the last was deleted 2026-08-18). That makes
it the one surface where the in-binary trajectory generator is still the
default, and the only place a caller can be handed it without being told.

The value itself is deliberately NOT changed: flipping a public default in
silence is the failure this codebase criticises elsewhere. What is enforced
here is that the choice stays visible.

Why it matters: the binary's generator builds every stroke from compiled-in
constants, so the shape is identical across every install. For one account that
is imperfect realism; for a fleet it ties the accounts together.
`invisible-playwright` moved off that path in 0.4.0 and sets the pref False. A
caller using this function has no such option, which is exactly why the
docstring has to say so.
"""
from __future__ import annotations

import inspect

import pytest

from invisible_core import config

pytestmark = pytest.mark.unit


def test_the_default_still_enables_the_in_binary_generator():
    """Pinned so that changing it becomes a decision somebody makes on purpose.

    A third-party integration upgrading invisible-core must not have its
    pointer behaviour change underneath it without a release note.
    """
    prefs = config.get_default_stealth_prefs(seed=42)
    assert prefs["stealthfox.humanize"] is True
    assert prefs["stealthfox.humanize.maxTime"] == "1.5"


def test_disabling_it_leaves_no_timing_prefs_behind():
    """False must mean off, not "off but still carrying a duration"."""
    prefs = config.get_default_stealth_prefs(seed=42, humanize=False)
    assert prefs["stealthfox.humanize"] is False
    assert "stealthfox.humanize.maxTime" not in prefs


def test_a_float_caps_the_motion_rather_than_reading_as_merely_truthy():
    prefs = config.get_default_stealth_prefs(seed=42, humanize=0.4)
    assert prefs["stealthfox.humanize"] is True
    assert prefs["stealthfox.humanize.maxTime"] == "0.4"


def test_the_docstring_says_what_the_default_actually_hands_over():
    """Why this is a test and not a comment.

    If the warning is deleted the default silently becomes a trap again, and
    nothing else in the suite would notice. The wrapper's own move away from
    this path is the strongest evidence that the caller deserves to be told.
    """
    # Whitespace-normalised: a phrase that happens to straddle a line break is
    # still the same statement, and reflowing a docstring must not turn this
    # red. The first version of this test matched raw substrings and failed on
    # its own docstring for exactly that reason.
    doc = " ".join((inspect.getdoc(config.get_default_stealth_prefs) or "").split())
    for phrase in ("shared by every install", "LINKAGE KEY", "0.4.0"):
        assert phrase in doc, (
            f"the humanize docstring no longer says {phrase!r}; a caller can "
            f"now take the shared-shape trajectory without being told")
