"""The first run's terms of use are the core's decision: already accepted, on every host.

On a new profile official Firefox opens the pre-onboarding modal (the terms of
use) over the page, and until someone accepts the page has no focus and gets no
mouse event. Every launch here is a new profile. Federico, 2026-11-02: the core
decides it, and by default nothing appears. Measured on official 159.0 with
coord/firstrun/first_run_probe.py (the reasons are next to the prefs in
prefs.py): `browser.preonboarding.enabled` false alone gives way to the legacy
data-reporting flow (a privacy tab, or a bar at 60 s that takes 40 px of
innerHeight); the two `termsofuse.accepted*` silence both flows.
"""
from __future__ import annotations

import datetime
import re
import sys

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import _BASELINE, translate_profile_to_prefs

from _engine_source import EngineSource

pytestmark = pytest.mark.unit
_SOURCE = EngineSource.pinned()

FIRST_RUN = {
    "browser.preonboarding.enabled": False,
    "termsofuse.acceptedVersion": 4,
    "termsofuse.acceptedDate": "1785573000000",
}


@pytest.mark.parametrize("seed", [1, 7, 12, 29, 404])
@pytest.mark.parametrize("virtual_display", [False, True])
def test_always_emitted_and_equal_on_win32_and_linux(monkeypatch, seed, virtual_display):
    seen = {}
    for platform in ("win32", "linux"):
        monkeypatch.setattr(sys, "platform", platform)
        prefs = translate_profile_to_prefs(generate_profile(seed=seed), timezone="America/New_York",
                                           virtual_display=virtual_display)
        seen[platform] = {k: prefs.get(k, "<absent>") for k in FIRST_RUN}
    assert seen["win32"] == FIRST_RUN
    assert seen["linux"] == seen["win32"]


def test_declared_in_the_baseline_not_by_a_platform_table():
    for k, v in FIRST_RUN.items():
        assert _BASELINE.get(k) == v, k
        assert type(_BASELINE[k]) is type(v), k


def _firefox_js_int(text, name):
    m = re.search(r'^pref\("%s",\s*(\d+)\);' % re.escape(name), text, re.M)
    assert m, "%s not in firefox.js at %s" % (name, _SOURCE.commit)
    return int(m.group(1))


@pytest.mark.skipif(_SOURCE.unavailable() is not None, reason=_SOURCE.unavailable() or "")
def test_the_acceptance_is_still_valid_for_the_engines_firefox():
    """hasUserAcceptedCurrentTOU: a date from OLDEST_ALLOWED_TOU_ACCEPTANCE_YEAR
    on, and a version not below termsofuse.minimumVersion. A Firefox that raises
    the version fails here instead of bringing the modal back silently."""
    firefox_js = _SOURCE.show("browser/app/profile/firefox.js")
    policy = _SOURCE.show("toolkit/components/telemetry/app/TelemetryReportingPolicy.sys.mjs")
    assert firefox_js and policy, "sources not in %s" % _SOURCE.commit
    accepted = _BASELINE["termsofuse.acceptedVersion"]
    assert accepted >= _firefox_js_int(firefox_js, "termsofuse.minimumVersion")
    # the version a person who accepted the current terms holds, not a number above any
    assert accepted == _firefox_js_int(firefox_js, "termsofuse.currentVersion")
    oldest = int(re.search(r"OLDEST_ALLOWED_TOU_ACCEPTANCE_YEAR = (\d+);", policy).group(1))
    when = datetime.datetime.fromtimestamp(int(_BASELINE["termsofuse.acceptedDate"]) / 1000,
                                           datetime.timezone.utc)
    assert when.year >= oldest
    # the switch the pref drives is still read where the modal is decided
    assert 'getBoolPref("browser.preonboarding.enabled"' in policy
