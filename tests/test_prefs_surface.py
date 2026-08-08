"""What `translate_profile_to_prefs` actually emits.

THIS FUNCTION HAD NO TEST. Measured before writing this file: `prefs.py` sat at
15% statement coverage and `translate_profile_to_prefs` executed zero lines in
the whole suite - its only appearance was a `monkeypatch.setattr` replacing it
(`tests/test_launch.py:28`). Every `zoom.stealth.*` pref the patched binary
reads is produced here, so the body could be replaced with `return {}` and the
suite stayed green while shipping a browser with no spoofing at all.

Two failures had already happened behind that gap, both found by reading the
Firefox source rather than by any test:

  * `media.mediasource.{webm,mp4}.enabled` are not Firefox pref names. They are
    absent from `modules/libpref/init/StaticPrefList.yaml`, so the per-seed
    codec sampling was inert and every identity reported the same codec surface
    - an invariant across the fleet, which is the opposite of the intent;
  * `zoom.stealth.screen.avail_{width,height}` are undeclared AND ignored:
    `nsScreen::GetAvailRect` derives the available rect from
    `zoom_stealth_screen_width/height` minus a fixed taskbar.

So the tests here are about NAMES and REACHABILITY, not about values. A pref
whose name the binary does not read is indistinguishable, from Python, from one
that works perfectly.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import translate_profile_to_prefs

pytestmark = pytest.mark.unit


def _prefs(seed: int = 42, **kw):
    return translate_profile_to_prefs(generate_profile(seed), **kw)


# ── the function runs at all, and produces the surface it claims ────────────

def test_it_emits_the_stealth_namespace_rather_than_an_empty_dict():
    prefs = _prefs()
    stealth = [k for k in prefs if k.startswith("zoom.stealth.")]
    assert len(stealth) >= 20, (
        f"only {len(stealth)} zoom.stealth.* prefs emitted: {sorted(stealth)}")


@pytest.mark.parametrize("name", [
    # One per subsystem the binary gates on. If any of these stops being
    # emitted, that spoof is silently off and nothing else in the suite knows.
    "zoom.stealth.screen.width",
    "zoom.stealth.screen.height",
    "zoom.stealth.webgl.renderer",
    "zoom.stealth.webgl.vendor",
    "zoom.stealth.fpp.hw_seed",
    "zoom.stealth.audio.max_channel_count",
    "general.platform.override",
    "general.useragent.override",
])
def test_the_load_bearing_prefs_are_present(name):
    assert name in _prefs(), f"{name} is no longer emitted"


def test_the_codec_prefs_use_names_firefox_actually_declares():
    """The bug this file was written after.

    `media.mediasource.webm.enabled` / `.mp4.enabled` do not exist in Firefox,
    so setting them did nothing and the sampled codec diversity never reached a
    page. The real switches are `media.webm.enabled` / `media.mp4.enabled`.
    """
    prefs = _prefs()
    assert "media.webm.enabled" in prefs
    assert "media.mp4.enabled" in prefs
    for dead in ("media.mediasource.webm.enabled", "media.mediasource.mp4.enabled"):
        assert dead not in prefs, (
            f"{dead} is back; it is not a Firefox pref name and setting it is a "
            f"no-op that reads exactly like a working spoof")


def test_the_undeclared_screen_prefs_stay_out():
    """`nsScreen::GetAvailRect` ignores these and derives the rect from
    screen.width/height. Emitting them costs nothing but implies a control that
    does not exist, which is how they survived unnoticed."""
    prefs = _prefs()
    for dead in ("zoom.stealth.screen.avail_width", "zoom.stealth.screen.avail_height"):
        assert dead not in prefs, f"{dead} is back and the binary does not read it"


# ── the property the whole product rests on ────────────────────────────────

def test_the_same_seed_produces_byte_identical_prefs():
    """Seed reproducibility, asserted on the SHIPPED prefs rather than on the
    profile object. Callers rely on it, and the gate that covers it
    (`fppro_consistency.py`) lives in the workbench and needs a browser - so
    inside this package nothing asserted it at all."""
    assert _prefs(7) == _prefs(7)


def test_two_seeds_differ_in_the_fields_that_identify_a_machine():
    """Otherwise every install ships one identity, which is worse than none."""
    a, b = _prefs(1), _prefs(2)
    differing = {k for k in a if a[k] != b.get(k)}
    assert differing, "two seeds produced identical prefs"
    assert any(k.startswith("zoom.stealth.webgl.") for k in differing), (
        f"the GPU persona did not move between seeds: {sorted(differing)[:8]}")


# ── cross-check against the real Firefox source, where it is available ──────

_FF_SRC = pathlib.Path(os.environ.get("STEALTH_FIREFOX_SRC", "C:/ff/source"))


#: Where a pref literal can live. Both search paths use this one list.
_SOURCE_GLOBS = ("*.cpp", "*.h", "*.js", "*.jsm", "*.mjs", "*.yaml", "*.idl")

_PREF_LITERAL = r"zoom[._]stealth[._][A-Za-z0-9_.]+"


def _normalise(hits) -> set[str]:
    """C++ StaticPrefs mangles dots to underscores; keep both spellings."""
    found: set[str] = set()
    for hit in hits:
        found.add(hit.replace("_stealth_", ".stealth.").replace("zoom_", "zoom."))
        found.add(hit)
    return found


def _names_via_git() -> set[str] | None:
    """Ask git for the literals. None when the tree is not a git work tree.

    WHY THIS EXISTS. The version below walks the tree in Python, and on the
    workbench that is `C:/ff/source`: **412,691 files, 132,465 of them matching
    the extension list, 0.86 GB to decode.** Measured 2026-07-28: **189 s with a
    warm file cache, and over TEN MINUTES cold** - one test costing more wall
    clock than the other 806 put together, in the suite the core's pre-push hook
    runs. The cost is not the regex, it is 132,465 individual file opens on
    Windows, each one seen by the on-access scanner. `git grep` over the same
    globs answers in **8 s**.

    A twenty-minute pre-push gate is a gate people learn to push past, so the
    speed is the correctness issue here, not a nicety.

    The two paths were compared, not assumed equivalent: with the git path
    disabled the suite gives the identical verdict on the workbench tree, only
    slower.

    It is also a STRICTER question, not a looser one. git sees tracked files plus
    untracked-and-not-ignored ones, so what it drops relative to the filesystem
    walk is exactly what `.gitignore` covers - `obj-*`, which is GENERATED from
    the tree it is being compared against. A name found only in build output
    cannot be a name the source reads. Fewer names found means more prefs
    reported missing, so any error this introduces fails closed.
    """
    import subprocess

    try:
        probe = subprocess.run(
            ["git", "-C", str(_FF_SRC), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(_FF_SRC), "grep", "--untracked", "-hoIE",
             _PREF_LITERAL, "--", *_SOURCE_GLOBS],
            capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    # git grep exits 1 for "no matches". In a tree that has StaticPrefList.yaml
    # - which is what gates this whole test - no matches means the invocation is
    # wrong, not that the engine reads no prefs. Fall back rather than hand back
    # an empty set that would report every pref as dead.
    if out.returncode not in (0, 1) or not out.stdout.strip():
        return None
    return _normalise(out.stdout.split())


def _names_by_walking() -> set[str]:
    """The fallback: read the tree from Python. Slow - see `_names_via_git`."""
    import re

    pattern = re.compile(_PREF_LITERAL)
    suffixes = {g[1:] for g in _SOURCE_GLOBS}
    hits: list[str] = []
    for path in _FF_SRC.rglob("*"):
        if path.suffix not in suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "zoom" not in text:
            continue
        hits.extend(pattern.findall(text))
    return _normalise(hits)


#: The search costs 8 seconds and two tests want the same answer, so it is
#: memoised - but on `_FF_SRC`, not on nothing, because two tests below point
#: `_FF_SRC` at throwaway trees and must not be served the workbench's answer.
_READABLE_CACHE: dict[pathlib.Path, frozenset] = {}


def _names_the_binary_reads() -> set[str]:
    """Every `zoom.stealth.*` string literal in the Firefox tree.

    NOT just StaticPrefList.yaml. A pref read from JS with
    `getBoolPref(name, default)` needs no static declaration and works fine -
    the first version of this check used the yaml alone and flagged
    `zoom.stealth.debugger.force_detach`, which devtools/server/actors/thread.js
    reads perfectly well. Searching for the literal is the honest definition of
    "the binary reads this".
    """
    key = _FF_SRC
    if key not in _READABLE_CACHE:
        _READABLE_CACHE[key] = frozenset(_names_via_git() or _names_by_walking())
    return set(_READABLE_CACHE[key])


@pytest.mark.skipif(
    not (_FF_SRC / "modules" / "libpref" / "init" / "StaticPrefList.yaml").is_file(),
    reason=(
        "no Firefox source tree beside this checkout. This cross-check is a "
        "workbench convenience: the authoritative version runs where the "
        "binary is built. Set STEALTH_FIREFOX_SRC to point at one."
    ),
)
def test_every_stealth_pref_emitted_is_one_the_binary_reads():
    """The check that would have caught every dead-pref bug at the source.

    A `zoom.stealth.*` name the binary never reads is a spoof that silently
    does nothing, and from Python the two are indistinguishable. Four had
    already shipped that way: the two codec names, and screen.dpr and
    webgl.msaa, which appear in NO file of the tree at all.
    """
    readable = _names_the_binary_reads()
    emitted = {k for k in _prefs() if k.startswith("zoom.stealth.")}
    missing = sorted(k for k in emitted if k not in readable
                     and k.replace(".", "_") not in readable)
    assert not missing, (
        "these prefs are emitted but appear nowhere in the engine source, so "
        f"they are no-ops that look like working spoofs: {missing}")


@pytest.mark.skipif(
    not (_FF_SRC / "modules" / "libpref" / "init" / "StaticPrefList.yaml").is_file(),
    reason="no Firefox source tree beside this checkout",
)
def test_the_cross_check_would_actually_report_a_dead_pref():
    """Its known-bad input, and the reason it needs one.

    The check above passes when nothing is wrong, which is also what it does if
    `_names_the_binary_reads()` returns everything - and the search moved to
    `git grep` on 2026-07-28 for speed, so "the new search path is too generous"
    is now a way for this gate to go quietly vacuous. A fabricated name must come
    back absent, and the real ones present, or the search is answering the wrong
    question.
    """
    readable = _names_the_binary_reads()
    assert readable, "the search found no pref literals at all in the engine tree"
    invented = "zoom.stealth.this_pref_was_invented_by_a_test"
    assert invented not in readable, (
        "a name that exists nowhere in the engine was reported as readable, so "
        "the cross-check cannot fail and every emitted pref looks alive")
    # And a spot-check in the other direction: hw_seed is read from three C++
    # sites, so a search that misses it is missing real reads.
    assert ("zoom.stealth.fpp.hw_seed" in readable
            or "zoom_stealth_fpp_hw_seed" in readable), (
        "the search missed zoom.stealth.fpp.hw_seed, which three C++ sites read; "
        "it is under-reporting, and every pref will look dead")


def test_the_git_search_refuses_an_empty_answer_instead_of_returning_one(tmp_path,
                                                                        monkeypatch):
    """An empty result must fall back, never be handed on as "nothing is read".

    A git invocation that stops matching - a glob typo, a git version that
    spells a flag differently - exits 0 or 1 with no output. Returning that
    empty set makes EVERY emitted pref look dead, which is a wall of false
    failures; the shape to avoid is the opposite one, where an empty set is
    treated as a clean answer. Either way the caller must not receive it.
    """
    import subprocess

    repo = tmp_path / "empty-tree"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "nothing.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")

    monkeypatch.setattr(sys.modules[__name__], "_FF_SRC", repo)
    assert _names_via_git() is None, (
        "a tree with no pref literals produced a non-None result; the caller "
        "would use it as the readable set and report every pref as dead")


def test_the_git_search_is_not_silently_skipping_the_tree(tmp_path, monkeypatch):
    """And the positive half: a tree that DOES carry a literal is read.

    Without this, the test above is satisfied by a `_names_via_git` that always
    returns None - which would restore the ten-minute walk while every test
    stayed green.
    """
    import subprocess

    repo = tmp_path / "one-hit"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "probe.cpp").write_text(
        'Preferences::GetBool("zoom.stealth.fpp.hw_seed", false);\n', encoding="utf-8")

    monkeypatch.setattr(sys.modules[__name__], "_FF_SRC", repo)
    found = _names_via_git()
    assert found and "zoom.stealth.fpp.hw_seed" in found, found


# ── hw_seed doubles as an off-switch, so it must never be zero ──────────────

def test_no_seed_ever_produces_a_zero_hardware_seed():
    """`zoom.stealth.fpp.hw_seed` is not only a seed - it is a gate.

    Three C++ sites read it and act only when it is > 0: maxTouchPoints
    (Navigator.cpp), pointer/hover (nsMediaFeatures.cpp) and the audio noise
    (AnalyserNode.cpp). A session whose value is 0 therefore keeps a clean
    render hash AND silently reverts to the host's real touch, pointer and
    audio behaviour - on touch-capable Windows hardware, a capability appearing
    where the persona says there is none.

    0 used to be in CLEAN_RENDER_SEEDS because it is genuinely clean for the
    render hash. Measured before it was removed: 223 of 2000 seeds, 11.2% of
    identities. A value cannot be both a seed and an off-switch.
    """
    from invisible_core._webgl_personas import CLEAN_RENDER_SEEDS, render_noise_seed

    # The pool length is part of the contract too: render_noise_seed indexes
    # with `% len(...)`, so changing the LENGTH remaps every identity. The
    # first fix removed 0 outright and moved 445 of 500 seeds - an 89% change
    # in canvas render hash to repair an 11% defect. Replacing the slot keeps
    # every other index where it was.
    assert len(CLEAN_RENDER_SEEDS) == 9, (
        f"the pool is {len(CLEAN_RENDER_SEEDS)} long, not 9; changing the "
        f"length remaps every seed's render hash, not just the broken ones")
    assert 0 not in CLEAN_RENDER_SEEDS, (
        "0 is back in the pool; every seed mapping to it loses touch, pointer "
        "and audio spoofing while looking perfectly configured")
    zeros = [s for s in range(3000) if render_noise_seed(s) == 0]
    assert not zeros, f"{len(zeros)} seeds still produce hw_seed 0, e.g. {zeros[:5]}"


def test_the_emitted_hardware_seed_is_positive_for_every_seed():
    """Asserted on the PREF, not on the pool, so a future indirection that
    reintroduces zero on the way out is caught too."""
    for seed in (0, 1, 42, 777, 123456):
        value = _prefs(seed)["zoom.stealth.fpp.hw_seed"]
        assert isinstance(value, int) and value > 0, (
            f"seed {seed} emits hw_seed {value!r}; the C++ guards read that as "
            f"'spoofing off' for touch, pointer and audio")


def test_the_pool_still_offers_real_per_session_diversity():
    """Removing a value must not collapse the pool to something that stops
    separating sessions."""
    from invisible_core._webgl_personas import render_noise_seed

    seen = {render_noise_seed(s) for s in range(3000)}
    assert len(seen) >= 6, f"only {len(seen)} distinct hardware seeds: {sorted(seen)}"


# ── the Windows system-font surface (a Profile field, not a constant) ───────
#
# `font: menu` and friends resolve through ui.font.*, and nsXPLookAndFeel tries
# those prefs BEFORE asking the platform. With them absent Gecko answers from
# its own per-OS defaults, which in the Unix block name "Sans" at 13.3333px - a
# family no Windows machine has, on a build whose every other signal says
# Windows. Measured 2026-08-07: that one disagreement drove FpJS Pro to
# tampering=True on Linux with Windows clean, same seed and same IP.

_UI_ELEMENTS = (
    "caption", "icon", "menu", "message-box", "small-caption", "status-bar",
    "-moz-pull-down-menu", "-moz-button", "-moz-list", "-moz-field",
)
_MONO_LANGS = ("ar", "el", "he", "x-cyrillic", "x-unicode", "x-western")


def test_every_system_font_element_is_emitted():
    prefs = _prefs()
    missing = [f"ui.font.{e}" for e in _UI_ELEMENTS if f"ui.font.{e}" not in prefs]
    assert not missing, f"system-font prefs not emitted: {missing}"
    missing_sizes = [f"ui.font.{e}.size" for e in _UI_ELEMENTS
                     if f"ui.font.{e}.size" not in prefs]
    assert not missing_sizes, f"sizes not emitted: {missing_sizes}"


def test_the_ui_font_sizes_are_strings_because_gecko_parses_float_prefs_from_text():
    """The type IS the behaviour here, which is why it gets its own test.

    Preferences::GetFloat reads a float pref from its STRING form. Declared as
    a bare int the pref does not fail and does not warn - it is ignored, and
    the UI silently falls back to StyleFONT_MEDIUM_PX (16px). A page reading
    `getComputedStyle(el).fontSize` after `el.style.font = "menu"` sees 16px
    where Windows says 12px. Nothing else in the suite would notice.
    """
    prefs = _prefs()
    for element in _UI_ELEMENTS:
        value = prefs[f"ui.font.{element}.size"]
        assert isinstance(value, str), (
            f"ui.font.{element}.size is {type(value).__name__} {value!r}; it "
            f"must be a string or Preferences::GetFloat ignores it")
        assert value.strip() and float(value) > 0


def test_the_monospace_default_size_is_an_int_and_is_the_windows_13():
    prefs = _prefs()
    for lang in _MONO_LANGS:
        value = prefs[f"font.size.monospace.{lang}"]
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"font.size.monospace.{lang} is {type(value).__name__}")
        assert value == 13, (
            f"font.size.monospace.{lang} is {value}; Firefox ships 13 on "
            f"Windows and 12 in its Unix block, and we always claim Windows")


def test_the_ui_font_family_is_never_a_host_derived_name():
    """The specific failure this exists for: "Sans".

    Any name that is not a real Windows family is a tell, but "Sans" is the one
    that actually shipped - it is what fontconfig answers, and it reached a
    page through `font: menu` on every Linux run before 2026-08-07.
    """
    prefs = _prefs()
    forbidden = {"sans", "sans-serif", "serif", "monospace", "system-ui", ""}
    for element in _UI_ELEMENTS:
        family = prefs[f"ui.font.{element}"]
        assert family.strip().lower() not in forbidden, (
            f"ui.font.{element} = {family!r} is a generic or host-derived name")


def test_the_font_surface_does_not_vary_with_the_seed():
    """Unlike gpu/screen/hardware, and deliberately.

    Sampling this would manufacture diversity that does not exist: every
    Windows machine answers Segoe UI at 12px. A fleet whose system font varied
    per identity would be the signal, not the camouflage. The gpu assertion is
    here so the test cannot pass by the profile being constant overall.
    """
    from invisible_core._fpforge import generate_profile
    a, b = generate_profile(1), generate_profile(999_999)
    assert a.font == b.font, f"font surface varied: {a.font} vs {b.font}"
    assert a.gpu != b.gpu, "two seeds produced the same GPU - test is not probing"


def test_the_font_surface_is_pinnable_like_every_other_group():
    from invisible_core._fpforge import generate_profile
    profile = generate_profile(42, pin={"font.ui_family": "Tahoma",
                                        "font.monospace_size": 11})
    prefs = translate_profile_to_prefs(profile)
    assert prefs["ui.font.menu"] == "Tahoma"
    assert prefs["font.size.monospace.ar"] == 11


def test_a_caller_override_still_wins_over_the_font_layer():
    """`extra_prefs` is applied last for every other surface; fonts are not
    special. An A/B harness has to be able to unset this to measure what the
    tell looked like."""
    prefs = _prefs(extra_prefs={"ui.font.menu": "Arial"})
    assert prefs["ui.font.menu"] == "Arial"


def test_the_font_manifest_travels_with_the_profile():
    """Families, per-face metrics, aliases, ladder and the per-script fallback
    lists all live in one field, so the profile is the single object that says
    what Windows looks like."""
    from invisible_core._fpforge import generate_profile
    manifest = generate_profile(42).font.manifest
    assert manifest.count("\nF|") >= 60, "famiglie assenti dal manifest"
    assert manifest.count("\nA|") >= 40, "tabella alias assente"
    assert manifest.count("\nS|") >= 100, "tabella fallback per script assente"
    assert "\nL|" in manifest, "scala di copertura assente"
    prefs = _prefs()
    assert prefs["zoom.stealth.fonts.manifest"] == manifest


def test_the_manifest_is_ascii_and_carries_no_control_bytes():
    """The C++ reader is a getline plus a split, kept deliberately trivial, and
    the manifest is now a Python literal rather than a file - which is exactly
    where this project has corrupted text before, by letting an escape sequence
    through. A raw string keeps the two backslashes in the alias comment as
    backslashes; this checks the result rather than trusting the prefix."""
    from invisible_core._fpforge.profile import FONT_MANIFEST
    assert FONT_MANIFEST.isascii()
    # chr(), not escapes: a literal like the one this line used to hold
    # EVALUATES to a control character, and test_marker_vocabulary rejects that
    # on sight - correctly, because it cannot tell a test naming the byte from a
    # Windows path that lost its backslash. It caught this file on the first run.
    for code in (8, 12, 0, 13):   # backspace, formfeed, NUL, carriage return
        assert chr(code) not in FONT_MANIFEST, (
            f"byte di controllo 0x{code:02X} nel manifest")
    assert "HKLM\\" in FONT_MANIFEST, (
        "i backslash del commento sul registro sono stati mangiati: il letterale "
        "non e' piu' raw")
    assert FONT_MANIFEST.endswith("\n")
