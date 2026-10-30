"""The prefs that depend on the brand are the core's decision, never the build's.

The engine is built with Mozilla's unofficial branding, whose pref file
(browser/branding/unofficial/pref/firefox-branding.js) differs from the official
one. Federico, 2026-10-30: the brand is dictated by the core, all of it. So
every pref in which the two files differ is either carried by the engine on
Mozilla's own line (STEALTH-HOOK, the update prefs) or declared in _BASELINE;
the core's value is the official one unless DECIDED_OTHERWISE says why.

Read at the engine's commit (engine_port.json during a port, else the seal),
like every engine-source test: a new Firefox that adds or changes a branding
pref fails here instead of passing silently.
"""
from __future__ import annotations

import re

import pytest

from _engine_source import EngineSource
from invisible_core.prefs import _BASELINE

pytestmark = pytest.mark.unit
_SOURCE = EngineSource.pinned()

#: The core's own decision where it is not the official value, with the reason
#: in prefs.py next to the pref.
DECIDED_OTHERWISE = {"startup.homepage_welcome_url": ""}

#: In the official file, read by nothing in the engine's Firefox (the pref-type
#: gate, tests/test_pref_types.py, refuses a declared pref without a reader).
READ_BY_NOTHING = {"app.releaseNotesURL.prompt"}


def _release_prefs(text: str) -> dict:
    """pref("name", value) lines of a branding pref file as a release build
    reads them: the `#if MOZ_UPDATE_CHANNEL == beta` / `#elifdef MOZ_ESR` /
    `#else` chain resolved to its `#else` branch."""
    out, branch = {}, None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#if") or s.startswith("#elif"):
            branch = "skip"
            continue
        if s.startswith("#else"):
            branch = "take"
            continue
        if s.startswith("#endif"):
            branch = None
            continue
        if branch == "skip":
            continue
        m = re.match(r'pref\("([^"]+)",\s*(.+?)\);', s)
        if m:
            raw = m.group(2).strip()
            if raw.startswith('"'):
                out[m.group(1)] = raw[1:-1]
            elif raw in ("true", "false"):
                out[m.group(1)] = raw == "true"
            else:
                out[m.group(1)] = int(raw)
    return out


def _files():
    off = _SOURCE.show("browser/branding/official/pref/firefox-branding.js")
    unoff = _SOURCE.show("browser/branding/unofficial/pref/firefox-branding.js")
    assert off and unoff, f"branding pref files not in {_SOURCE.commit}"
    return _release_prefs(off), _release_prefs(unoff), unoff


@pytest.mark.skipif(_SOURCE.unavailable() is not None, reason=_SOURCE.unavailable() or "")
def test_every_pref_the_brand_moves_is_decided_by_the_core_or_carried_by_the_engine():
    official, unofficial, unoff_text = _files()
    hooked = {n for n in unofficial
              if re.search(r'pref\("%s",[^\n]*STEALTH-HOOK' % re.escape(n), unoff_text)}
    loose = []
    for name in sorted(set(official) | set(unofficial)):
        if official.get(name) == unofficial.get(name) or name in hooked or name in READ_BY_NOTHING:
            continue
        if name not in _BASELINE:
            loose.append(f"{name}: official {official.get(name)!r}, build {unofficial.get(name)!r}, core silent")
            continue
        want = DECIDED_OTHERWISE.get(name, official.get(name))
        if _BASELINE[name] != want:
            loose.append(f"{name}: core {_BASELINE[name]!r}, expected {want!r}")
    assert not loose, "brand prefs not decided by the core:\n" + "\n".join(loose)


def test_the_core_never_declares_the_unofficial_brands_values_by_accident():
    assert _BASELINE["devtools.selfxss.count"] == 0
    assert set(DECIDED_OTHERWISE) <= set(_BASELINE)
