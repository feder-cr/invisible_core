"""The preloaded New Tab page is the retail's unless the caller asks for less.

A retail Firefox keeps a New Tab page preloaded in a process of its own (a privileged-about process of about 200 MB, and the process of a
Mozilla site beside it), and the browser the core describes does the same: the core writes NOTHING by default. `preload_newtab=False` is the
caller's declaration, by name, and writes `browser.newtab.preload` false (neither process is born: measured on the candidate engine, three runs
of three); `True` says the retail's value aloud; a pref of the caller's own in `extra_prefs` wins over both, as for every other layer.
"""
import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import compose_session_prefs

pytestmark = pytest.mark.unit
KEY = "browser.newtab.preload"


def _prefs(**kw):
    return compose_session_prefs(generate_profile(seed=4242), timezone="America/New_York", **kw).prefs


def test_by_default_the_core_writes_no_preference_about_the_preload():
    assert KEY not in _prefs(), "the default is the retail's: the core says nothing"
    assert KEY not in _prefs(preload_newtab=None)


def test_asked_to_turn_it_off_the_core_declares_it_off():
    assert _prefs(preload_newtab=False)[KEY] is False


def test_asked_to_keep_it_the_core_says_the_retails_value_aloud():
    assert _prefs(preload_newtab=True)[KEY] is True


@pytest.mark.parametrize("asked", [False, True])
def test_a_pref_of_the_callers_own_wins_over_the_option(asked):
    mine = not asked
    assert _prefs(preload_newtab=asked, extra_prefs={KEY: mine})[KEY] is mine


def test_asking_changes_that_pref_and_nothing_else():
    plain, off = _prefs(), _prefs(preload_newtab=False)
    assert {k for k in set(plain) | set(off) if plain.get(k) != off.get(k)} == {KEY}
