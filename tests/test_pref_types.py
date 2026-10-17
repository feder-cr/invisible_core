"""Every pref the core declares has the TYPE the engine reads it with.

A pref with the right name and the wrong type is not an error anywhere: Gecko
keeps a user value whose type does not match the reader, the read fails, and
the code goes on with its fallback - which for the values this package exists
to declare is the HOST. Nothing crashes, nothing logs, and from Python the
declaration looks perfect.

The instance that made this a gate (28-valori-dell-host.md A17):
`ui.textScaleFactor` was the STRING "1.0" from 2026-08-10 to 49.46.0, reasoned
from `Preferences::GetFloat`, which parses a string. But LookAndFeel reads its
float table with `Preferences::GetInt` and divides by 100. The read failed, the
native code answered with the host's text scale, and the scale multiplies the
device pixel ratio: measured on Linux under GDK_DPI_SCALE=1.25, a persona
declaring dpr 1 answered 1.25. The string had itself been the cure for an
earlier wrong type, a Python float that crashed the second context. Two wrong
types in a row on one pref, each fixed by reasoning about the reader instead of
reading it.

So the type is not remembered here, it is READ from the engine the seal pins,
from the places that decide it, in the order of their authority:

  1. `StaticPrefList.yaml` - libpref refuses a user value of another type;
  2. LookAndFeel's own tables in `widget/nsXPLookAndFeel.cpp`, the readers
     A17 was about: ints and floats both through GetInt, colours and font
     names as strings, font sizes through GetFloat;
  3. the default in the engine's pref files (all.js, firefox.js, branding):
     a user value of another type than the default is refused too;
  4. the accessor that reads the name, in C++ or JS, with the name written
     out or held in a constant of the same file.

A pref none of them reads is DEAD, and that fails here as well: it is a line
of the core that looks like a decision and moves nothing. Three were found the
day this gate was written and removed (`general.useragent.locale`,
`browser.search.geoSpecificDefaults`, `network.predictor.enabled`).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

import pytest

from invisible_core import prefs as P
from invisible_core._fpforge import generate_profile

from _engine_source import EngineSource

pytestmark = pytest.mark.unit

_SOURCE = EngineSource.pinned()
_needs_source = pytest.mark.skipif(_SOURCE.unavailable() is not None,
                                   reason=_SOURCE.unavailable() or "")

BOOL, INT, FLOAT, STR = "bool", "int", "float", "str"

_CODE = ("*.cpp", "*.h", "*.js", "*.mjs", "*.jsm")
_PREF_FILES = (
    "modules/libpref/init/all.js",
    "browser/app/profile/firefox.js",
    "browser/branding/unofficial/pref/firefox-branding.js",
)
_ACCESSOR_KIND = {
    "GetBool": BOOL, "getBoolPref": BOOL,
    "GetInt": INT, "GetUint": INT, "getIntPref": INT,
    "GetFloat": FLOAT,
    "GetCString": STR, "GetString": STR, "GetLocalizedCString": STR,
    "GetLocalizedString": STR, "getCharPref": STR, "getStringPref": STR,
}
_ACCESSOR = "(" + "|".join(sorted(_ACCESSOR_KIND)) + ")"

#: Readers no pattern can see, because the name reaches the accessor through a
#: function PARAMETER. Each one names the file that holds the literal and the
#: read, and `test_every_hand_typed_reader_is_still_there` checks the literal
#: is still in that file, so an entry cannot outlive its reader.
_READ_THROUGH_A_PARAMETER: Dict[str, Tuple[str, str]] = {
    # leggiChiaveSilenziosa(pref) -> Services.prefs.getStringPref(pref, "")
    "zoom.stealth.apikey.mozilla":
        (STR, "toolkit/components/urlformatter/URLFormatter.sys.mjs"),
    "zoom.stealth.apikey.google_location_service":
        (STR, "toolkit/components/urlformatter/URLFormatter.sys.mjs"),
    "zoom.stealth.apikey.google_safebrowsing":
        (STR, "toolkit/components/urlformatter/URLFormatter.sys.mjs"),
}


def _static_kind(t: str) -> Optional[str]:
    t = t.lower()
    for word, kind in (("bool", BOOL), ("float", FLOAT), ("int", INT), ("string", STR)):
        if word in t:
            return kind
    return None


def _value_kind(literal: str) -> Optional[str]:
    v = literal.strip()
    if v in ("true", "false"):
        return BOOL
    if re.fullmatch(r"-?\d+", v):
        return INT
    if v[:1] in "\"'":
        return STR
    return None


def _laf(source: EngineSource) -> Dict[str, str]:
    text = source.show("widget/nsXPLookAndFeel.cpp") or ""

    def table(name: str) -> List[str]:
        m = re.search(r"static const char " + name + r"\[\]\[\d+\] = \{(.*?)\};", text, re.S)
        assert m, (f"{name} is not in widget/nsXPLookAndFeel.cpp at {source.commit}: "
                   "the engine changed the shape this gate reads, look again")
        return re.findall(r'"([^"]+)"', m.group(1))

    out: Dict[str, str] = {}
    for name in table("sIntPrefs") + table("sFloatPrefs"):
        out[name] = INT                 # GetFloatValue: GetInt, then / 100
    for name in table("sColorPrefs"):
        out[name] = out[name + ".dark"] = STR
    for name in table("sFontPrefs"):
        out[name] = STR
        out[name + ".size"] = out[name + ".weight"] = FLOAT
        out[name + ".italic"] = BOOL
    return out


def reader_kinds(source: EngineSource, names) -> Dict[str, Tuple[Set[str], str]]:
    """For each name, the types the engine reads it with and where that was
    learned. A name absent from the result has no reader at all."""
    found: Dict[str, Tuple[Set[str], str]] = {}
    yaml = source.show("modules/libpref/init/StaticPrefList.yaml") or ""
    assert yaml, "StaticPrefList.yaml is not readable at %s" % source.commit
    for m in re.finditer(r"^-\s+name:\s*(\S+)\s*\n\s+type:\s*(\S+)", yaml, re.M):
        kind = _static_kind(m.group(2))
        if kind and m.group(1) in names:
            found[m.group(1)] = ({kind}, "StaticPrefList.yaml")
    for name, kind in _laf(source).items():
        if name in names and name not in found:
            found[name] = ({kind}, "nsXPLookAndFeel.cpp")
    for path in _PREF_FILES:
        for m in re.finditer(r'^\s*(?:pref|sticky_pref|lockPref)\(\s*"([^"]+)"\s*,\s*([^)\n]+)',
                             source.show(path) or "", re.M):
            kind = _value_kind(m.group(2).rstrip(", "))
            if kind and m.group(1) in names and m.group(1) not in found:
                found[m.group(1)] = ({kind}, path)
    hits = source.grep(_ACCESSOR + r"""\(\s*["'][^"']+["']""", _CODE)
    assert hits is not None, "git grep failed at %s" % source.commit
    by_accessor: Dict[str, Set[str]] = {}
    for m in re.finditer(_ACCESSOR + r"""\(\s*["']([^"']+)["']""", hits):
        by_accessor.setdefault(m.group(2), set()).add(_ACCESSOR_KIND[m.group(1)])
    rest = set()
    for name in names:
        if name in found:
            continue
        if name in by_accessor:
            found[name] = (by_accessor[name], "an accessor with the literal")
        elif name in _READ_THROUGH_A_PARAMETER:
            kind, path = _READ_THROUGH_A_PARAMETER[name]
            found[name] = ({kind}, path)
        else:
            rest.add(name)
    for name, kinds in _through_a_constant(source, rest).items():
        found[name] = (kinds, "an accessor through a constant")
    return found


def _through_a_constant(source: EngineSource, names: Set[str]) -> Dict[str, Set[str]]:
    """`const kPref = "name"` (or `static const char k[] = "name"`) and then
    `getBoolPref(kPref)` in the same file. One search for every name: a
    search of the engine costs seconds, and a name each was most of the
    gate's time."""
    if not names:
        return {}
    hits = source.lines_with(sorted(names), _CODE)
    assert hits is not None, "git grep failed at %s" % source.commit
    out: Dict[str, Set[str]] = {}
    texts: Dict[str, str] = {}
    for path, line in hits:
        for m in re.finditer(r"""(\w+)\s*(?:\[\])?\s*=\s*["']([^"']+)["']""", line):
            if m.group(2) not in names:
                continue
            body = texts.setdefault(path, source.show(path) or "")
            for a in re.finditer(_ACCESSOR + r"\(\s*" + re.escape(m.group(1)) + r"\b", body):
                out.setdefault(m.group(2), set()).add(_ACCESSOR_KIND[a.group(1)])
    return out


def python_kinds(value) -> Set[str]:
    """What a Python value is to libpref once `_pref_literal` writes it."""
    if isinstance(value, bool):
        return {BOOL}
    if isinstance(value, int):
        return {INT}
    if isinstance(value, str):
        try:
            float(value)
            return {STR, FLOAT}
        except ValueError:
            return {STR}
    return {"python " + type(value).__name__}


def misdeclared(emitted: Dict[str, list], readers) -> Tuple[List[str], List[str]]:
    """(wrongly typed, read by nothing), one line each."""
    wrong, dead = [], []
    for name in sorted(emitted):
        if name not in readers:
            dead.append(name)
            continue
        kinds, where = readers[name]
        for value in emitted[name]:
            if not (python_kinds(value) & kinds):
                wrong.append("%s = %r is %s; the engine reads it as %s (%s)"
                             % (name, value, "/".join(sorted(python_kinds(value))),
                                "/".join(sorted(kinds)), where))
                break
    return wrong, dead


def _emitted(monkeypatch) -> Dict[str, list]:
    """Every pref any session can be composed with, and every value it takes:
    both platforms' branches, both themes, the persona switches that change
    WHICH prefs are written, and the session layers above the fingerprint."""
    out: Dict[str, list] = {}
    socks = {"server": "socks5://127.0.0.1:1080", "username": "u", "password": "p"}
    # Seeds move the sampled values; these pins move WHICH prefs exist or the
    # branch a value comes from, so one seed is enough for them.
    switches = [{"hardware.accessibility_overrides": True},
                {"hardware.max_touch_points": 10}, {"desktop.overlay_scrollbars": False},
                {"hardware.storage_enabled": False}, {"hardware.fake_media_devices": False}]
    runs = [(seed, pin, vd) for seed in (0, 42, 1561645783)
            for pin in ({}, {"dark_theme": True}) for vd in (False, True)]
    runs += [(42, pin, False) for pin in switches]

    def add(composed):
        for k, v in composed.items():
            values = out.setdefault(k, [])
            if v not in values:
                values.append(v)

    for platform in ("win32", "linux"):
        monkeypatch.setattr(P.sys, "platform", platform)
        for seed, pin, vd in runs:
            add(P.compose_session_prefs(
                generate_profile(seed, pin=pin), locale="it-IT", timezone="Europe/Rome",
                virtual_display=vd, proxy=socks, humanize=1.5,
                show_cursor=True, survive_hard_kill=True).prefs)
    return out


@_needs_source
def test_every_declared_pref_has_the_type_the_engine_reads_and_a_reader(monkeypatch):
    emitted = _emitted(monkeypatch)
    readers = reader_kinds(_SOURCE, set(emitted))
    wrong, dead = misdeclared(emitted, readers)
    assert not wrong, (
        "these prefs are declared with a type the engine does not read them "
        "with, so the read fails and the engine falls back - to the host, for "
        "the values this package declares:\n  " + "\n  ".join(wrong))
    assert not dead, (
        "nothing in the engine the seal pins (%s) reads these prefs, so they "
        "move nothing: remove them, or, if the reader takes the name through a "
        "parameter, add it to _READ_THROUGH_A_PARAMETER with its file:\n  %s"
        % (_SOURCE.commit, "\n  ".join(dead)))


@_needs_source
@pytest.mark.parametrize("name,value", [
    ("ui.textScaleFactor", "1.0"),        # A17: a float table read with GetInt
    ("ui.textScaleFactor", 1.0),          # the 2026-08-10 crash: no float type
    ("ui.useOverlayScrollbars", True),    # an int table, not a bool
    ("layout.css.devPixelsPerPx", 1.25),  # a static float pref is a STRING
    ("layout.frame_rate", "60"),          # a static int pref
    ("ui.font.menu.size", 12),            # GetFloat parses text
    ("zoom.stealth.apikey.mozilla", 1),   # a parameter-read string
])
def test_the_gate_catches_a_known_wrong_type(name, value):
    """The known-bad inputs, one per source of the type, so a source that
    stops answering turns this red instead of making the gate vacuous."""
    readers = reader_kinds(_SOURCE, {name})
    assert name in readers, "%s lost its reader: the gate cannot judge it" % name
    wrong, dead = misdeclared({name: [value]}, readers)
    assert wrong and not dead, (name, value, readers[name])


@_needs_source
def test_an_invented_pref_is_reported_as_read_by_nothing():
    invented = "zoom.stealth.this_pref_was_invented_by_a_test"
    wrong, dead = misdeclared({invented: [1]}, reader_kinds(_SOURCE, {invented}))
    assert dead == [invented] and not wrong


@_needs_source
@pytest.mark.parametrize("name", sorted(_READ_THROUGH_A_PARAMETER))
def test_every_hand_typed_reader_is_still_there(name):
    kind, path = _READ_THROUGH_A_PARAMETER[name]
    text = _SOURCE.show(path)
    assert text and name in text, (
        "%s no longer names %s at %s: the hand-typed reader is gone, so the "
        "entry is a type nobody reads" % (path, name, _SOURCE.commit))
