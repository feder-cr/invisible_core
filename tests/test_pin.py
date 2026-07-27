"""The core-pin comparison (src/invisible_core/pin.py).

WHY THESE TESTS LOOK EXPENSIVE. The previous version of this suite monkeypatched
probe_core, installed_core_version, recorded_core_version and editable_core_path,
so the only thing under test was the pure verdict function. A mutation that
rewrote installed_core_version() to return the INSTALLER'S RECORD instead of the
version derived from the files left that suite fully green - and that mutation is
the whole point of the check, because a clobbered site-packages is precisely the
environment where the record is right and the files are wrong.

So the fact-gathering functions are exercised against real site-packages layouts
built under tmp_path: a real invisible_core package (this repo's own pin.py and
_version.py, copied in), a real seal.json, real .dist-info directories with real
Requires-Dist lines, and a real direct_url.json. importlib.metadata reads those
the same way it reads an installed environment. Anything that has to import the
stub core runs in a subprocess with the stub first on sys.path, so the real
installation is never disturbed, and every one of those subprocesses reports
back which file it actually imported (a stub that failed to shadow would make
every assertion below vacuous).

The pure verdict function is still tested per branch, at the bottom, where it is
labelled as such.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from invisible_core import _pin

pytestmark = pytest.mark.unit

REAL_PKG = Path(_pin.__file__).resolve().parent


def child_env(**overrides) -> dict:
    """The environment every subprocess in this file runs under.

    The runtime repair is ON by default in shipped code, and most of the layouts
    below are deliberately broken ones. Without this, a single forgotten
    subprocess would run a real `pip install --force-reinstall` against whatever
    environment the suite happens to be running in. The repair is exercised on
    purpose further down, through a recording installer, never through pip.
    """
    env = dict(os.environ)
    env[_pin.AUTOFIX_ENV] = "off"
    env.pop(_pin.AUTOFIX_ATTEMPTED_ENV, None)
    env.update(overrides)
    return env


# ── building a site-packages that importlib.metadata will actually read ──────

def make_core(
    site: Path,
    *,
    tag: str = "firefox-18",
    revision: int = 0,
    with_version: bool = True,
    init_version: str | None = None,
) -> Path:
    """A real-enough invisible_core under `site`.

    pin.py and _version.py are COPIES OF THE SHIPPED FILES, so the derivation
    under test is the real one: seal.json's tag number, plus CORE_REVISION out of
    _version.py, is what installed_core_version() has to report.

    init_version plants a package-level __version__ (which in the real package is
    importlib.metadata.version, i.e. the installer's record). It exists so one
    test can prove that installed_core_version() does NOT reach for it.
    """
    pkg = site / "invisible_core"
    pkg.mkdir(parents=True, exist_ok=True)
    init = "# stub core: only what the pin check touches, no third-party imports\n"
    if init_version is not None:
        init += f'__version__ = "{init_version}"\n'
    (pkg / "__init__.py").write_text(init, encoding="utf-8")
    # BOTH files. The implementation is `pin.py` since 2026-07-27 and `_pin.py`
    # is a two-line alias kept for consumers built against <= 18.6.0; a
    # synthetic install that copies only one of them is not a shape any real
    # install has. Copying the shipped files rather than writing a stub is the
    # point of this fixture - the derivation under test must be the real one.
    shutil.copy2(REAL_PKG / "pin.py", pkg / "pin.py")
    shutil.copy2(REAL_PKG / "_pin.py", pkg / "_pin.py")
    if with_version:
        text = (REAL_PKG / "_version.py").read_text(encoding="utf-8")
        text = re.sub(r"(?m)^CORE_REVISION\s*=\s*\d+", f"CORE_REVISION = {revision}", text)
        (pkg / "_version.py").write_text(text, encoding="utf-8")
        (pkg / "seal.json").write_text(json.dumps({"tag": tag}), encoding="utf-8")
    (pkg / "seal.py").write_text(
        "def active_seal():\n    return None\n", encoding="utf-8")
    return pkg


def make_distinfo(
    site: Path,
    name: str,
    version: str,
    requires: tuple[str, ...] = (),
    direct_url: dict | None = None,
) -> Path:
    """A real .dist-info directory. Requires-Dist lines are written verbatim, so
    the parser is fed exactly what a building backend would have emitted."""
    d = site / f"{name.replace('-', '_')}-{version}.dist-info"
    d.mkdir(parents=True, exist_ok=True)
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    lines += [f"Requires-Dist: {r}" for r in requires]
    (d / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if direct_url is not None:
        (d / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")
    return d


def run_probe(site: Path, body: str) -> dict:
    """Run `body` in a subprocess with `site` first on sys.path.

    The body fills `out`. The helper always asserts which invisible_core got
    imported, so a stub that failed to shadow the installed core fails the test
    instead of quietly passing it.
    """
    code = (
        textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(site)!r})
        from invisible_core import _pin
        out = {{"__pin_file__": _pin.__file__}}
        """)
        + textwrap.dedent(body)
        + '\nprint("JSON:" + json.dumps(out, default=str))\n'
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=child_env())
    assert r.returncode == 0, r.stdout + r.stderr
    payload = [ln for ln in r.stdout.splitlines() if ln.startswith("JSON:")]
    assert payload, r.stdout + r.stderr
    out = json.loads(payload[-1][len("JSON:"):])
    assert str(site) in out["__pin_file__"], (
        "the stub core did not shadow the installed one, every assertion below "
        "would be vacuous: " + out["__pin_file__"])
    return out


# ── HAVE is the files, never the record ──────────────────────────────────────

def test_the_reported_version_is_derived_from_the_files_not_the_install_record(tmp_path):
    """The claim the whole assertion rests on. Files derive 19.0.0, the
    installer's record says 18.0.0, and the package even carries a
    __version__ = "18.0.0" attribute (which in the real package IS the record).
    installed_core_version() must say 19.0.0 anyway."""
    site = tmp_path / "site"
    make_core(site, tag="firefox-19", revision=0, init_version="18.0.0")
    make_distinfo(site, "invisible-core", "18.0.0")

    out = run_probe(site, """
        out["have"] = _pin.installed_core_version()
        out["recorded"] = _pin.recorded_core_version()
        import invisible_core
        out["attr"] = invisible_core.__version__
    """)
    assert out["have"] == "19.0.0", out
    assert out["recorded"] == "18.0.0", out
    assert out["attr"] == "18.0.0", out


def test_the_reported_version_moves_with_core_revision_too(tmp_path):
    """The other half of the derivation: a core-only release bumps
    CORE_REVISION, not the seal tag, and the reported version has to follow."""
    site = tmp_path / "site"
    make_core(site, tag="firefox-18", revision=3)
    make_distinfo(site, "invisible-core", "18.0.0")
    out = run_probe(site, 'out["have"] = _pin.installed_core_version()')
    assert out["have"] == "18.3.0", out


def test_a_clobbered_site_packages_is_refused_even_though_pip_check_calls_it_healthy(
    tmp_path,
):
    """End to end on real facts: the consumer declares 18.0.0, the record says
    18.0.0 (so pip and pip check are satisfied), the files derive 19.0.0. This
    is the only layer that can see it, so it has to."""
    site = tmp_path / "site"
    make_core(site, tag="firefox-19")
    make_distinfo(site, "invisible-core", "18.0.0")
    make_distinfo(site, "fake-consumer", "1.2.3", ("invisible-core==18.0.0",))

    out = run_probe(site, """
        try:
            _pin.assert_core_pin("fake-consumer")
            out["raised"] = None
        except ImportError as e:
            out["raised"] = str(e)
        out["report"] = _pin.pin_report("fake-consumer")
    """)
    msg = out["raised"]
    assert msg, "a clobbered site-packages was reported as healthy"
    assert "18.0.0" in msg and "19.0.0" in msg, msg
    assert "--force-reinstall" in msg, msg
    assert out["report"]["verdict"] == "violated", out["report"]


def test_installed_core_version_never_falls_back_to_the_install_record(tmp_path):
    """The exact regression: the docstring promised "never from
    importlib.metadata" while the fallback imported invisible_core.__version__,
    which IS importlib.metadata.version(). On a core whose _version module is
    gone - a partial copy, i.e. the case this function exists for - the fallback
    handed back the record and the environment reported as HEALTHY.

    With the fallback removed the version is unknown, and unknown is reported as
    unknown inside the ordinary mismatch message - it is not a separate branch,
    because a real core that cannot derive its version cannot be imported either
    (invisible_core.__init__ reaches _version), and that state belongs to the
    consumers' import floor."""
    site = tmp_path / "site"
    make_core(site, with_version=False, init_version="18.0.0")
    make_distinfo(site, "invisible-core", "18.0.0")
    make_distinfo(site, "fake-consumer", "1.2.3", ("invisible-core==18.0.0",))

    out = run_probe(site, """
        out["have"] = _pin.installed_core_version()
        out["report"] = _pin.pin_report("fake-consumer")
        try:
            _pin.assert_core_pin("fake-consumer")
            out["raised"] = None
        except ImportError as e:
            out["raised"] = str(e)
    """)
    assert out["have"] is None, out
    assert out["report"]["verdict"] == "violated", out["report"]
    assert out["raised"], "an undeterminable core version was reported as healthy"
    files_line = next(
        ln for ln in out["raised"].splitlines()
        if "files on disk" in ln or ln.strip().startswith("installed"))
    assert "undeterminable" in files_line, files_line
    assert "18.0.0" not in files_line, (
        "the installer's record was rendered as the version that will run: "
        + files_line)
    assert "_version.py" in out["raised"], out["raised"]


def test_a_matching_pair_built_from_real_files_clears_the_check(tmp_path):
    """A floor, not a wall."""
    site = tmp_path / "site"
    make_core(site, tag="firefox-18", revision=0)
    make_distinfo(site, "invisible-core", "18.0.0")
    make_distinfo(site, "fake-consumer", "1.2.3", ("invisible-core==18.0.0",))

    out = run_probe(site, """
        _pin.assert_core_pin("fake-consumer")
        out["report"] = _pin.pin_report("fake-consumer")
    """)
    assert out["report"]["verdict"] == "holds", out["report"]
    assert out["report"]["have"] == "18.0.0"
    assert out["report"]["want"] == "18.0.0"


# ── the states this module is NOT allowed to claim it handles ────────────────

def test_the_module_no_longer_claims_to_diagnose_an_unimportable_core():
    """probe_core() reported a "no_seal" state and pin_problem() carried a branch
    for it, and neither could be reached from a real installation: a core with no
    seal cannot be imported at all, because invisible_core.__init__ reaches
    _version.py, which reads seal.json. Anything inside the core runs only after
    that succeeded.

    Both are gone, and the state is reported by the consumers' import floor
    instead (invisible_playwright/tests/test_seal_floor.py and
    invisible_firefox/tests/test_seal_floor.py). This test is what stops the dead
    branch from being reintroduced the next time somebody reads the message and
    wonders where it went."""
    assert not hasattr(_pin, "probe_core")
    assert "no_seal" not in SOURCE
    assert "core_state" not in SOURCE


def test_an_editable_core_is_recognised_from_its_direct_url_json(tmp_path):
    """PEP 610. The path is read out of a real direct_url.json, because that is
    the file that decides whether the user is offered a --force-reinstall that
    would overwrite their working tree."""
    site = tmp_path / "site"
    make_core(site)
    target = tmp_path / "checkout" / "invisible_core"
    target.mkdir(parents=True)
    make_distinfo(
        site, "invisible-core", "18.0.0",
        direct_url={"url": target.as_uri(), "dir_info": {"editable": True}},
    )
    out = run_probe(site, 'out["editable"] = _pin.editable_core_path()')
    assert out["editable"] is not None
    assert Path(out["editable"]).resolve() == target.resolve(), out


def test_a_non_editable_direct_url_is_not_an_editable_core(tmp_path):
    """`pip install ./invisible_core` also writes direct_url.json, without
    dir_info.editable. Treating it as editable would suppress the reinstall
    remedy for a user who has no working tree to protect."""
    site = tmp_path / "site"
    make_core(site)
    make_distinfo(
        site, "invisible-core", "18.0.0",
        direct_url={"url": (tmp_path / "sdist").as_uri(), "dir_info": {}},
    )
    out = run_probe(site, 'out["editable"] = _pin.editable_core_path()')
    assert out["editable"] is None, out


def test_an_index_install_has_no_direct_url_and_is_not_editable(tmp_path):
    site = tmp_path / "site"
    make_core(site)
    make_distinfo(site, "invisible-core", "18.0.0")
    out = run_probe(site, 'out["editable"] = _pin.editable_core_path()')
    assert out["editable"] is None, out


def test_an_editable_mismatch_is_never_handed_a_reinstall_command(tmp_path):
    """On this project's own machines all three packages are editable installs
    pointing at working trees. A --force-reinstall there replaces live source
    with a copy from the index, uncommitted work included."""
    site = tmp_path / "site"
    make_core(site, tag="firefox-19")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    make_distinfo(
        site, "invisible-core", "18.0.0",
        direct_url={"url": checkout.as_uri(), "dir_info": {"editable": True}},
    )
    make_distinfo(site, "fake-consumer", "1.2.3", ("invisible-core==18.0.0",))
    out = run_probe(site, """
        try:
            _pin.assert_core_pin("fake-consumer")
            out["raised"] = None
        except ImportError as e:
            out["raised"] = str(e)
    """)
    msg = out["raised"]
    assert msg, "an editable core two releases away was accepted"
    assert "--force-reinstall" not in msg, msg
    assert "editable" in msg and "INVISIBLE_CORE_PIN=allow" in msg, msg


# ── the declared pin, parsed out of real METADATA ────────────────────────────

def declared(tmp_path, monkeypatch, *requires: str, name: str = "fake-consumer"):
    """Read the declaration the way the shipped code does: importlib.metadata
    over a real .dist-info on sys.path. No stub of the metadata layer."""
    site = tmp_path / "site"
    make_distinfo(site, name, "1.2.3", requires)
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    return _pin.pin_declaration(name)


@pytest.mark.parametrize("spelling", [
    "invisible-core==18.0.0",
    # The parenthesised dialect. Legal Requires-Dist, emitted by several
    # building backends, and it used to read as "no pin declared" - which was
    # then reported as healthy.
    "invisible-core (==18.0.0)",
    "invisible_core==18.0.0",
    "Invisible.Core == 18.0.0",
    "invisible-core[extras]==18.0.0",
])
def test_every_legal_spelling_of_the_pin_is_understood(tmp_path, monkeypatch, spelling):
    d = declared(tmp_path, monkeypatch, spelling, "platformdirs>=4")
    assert d.status == "pinned", d
    assert d.version == "18.0.0", d


def test_an_environment_marker_on_the_pin_line_does_not_switch_the_check_off(
    tmp_path, monkeypatch,
):
    """scripts/sync_core_pin.py PRESERVES an environment marker when it rewrites
    the pin, so a marker on that line is a supported edit. The old parser
    skipped every requirement containing a semicolon on the assumption that a
    semicolon meant "an extra, never the core pin", which made that one legal
    edit silently disable the assertion."""
    d = declared(
        tmp_path, monkeypatch,
        'invisible-core==18.0.0; python_version >= "3.11"',
        "platformdirs>=4",
    )
    assert d.status == "pinned", d
    assert d.version == "18.0.0", d


def test_a_requirement_behind_an_extra_is_not_the_pin(tmp_path, monkeypatch):
    """`extra == "dev"` means a default install never resolved it, so there is
    nothing to compare. Reported as not checkable, not as a pass."""
    d = declared(tmp_path, monkeypatch,
                 'invisible-core==18.0.0; extra == "dev"', "pytest>=7")
    assert d.status == "no_pin", d
    assert d.version is None
    assert "extra" in (d.detail or ""), d


def test_a_direct_reference_declares_no_comparable_version(tmp_path, monkeypatch):
    d = declared(tmp_path, monkeypatch,
                 "invisible-core @ git+https://example.invalid/x.git@abc")
    assert d.status == "no_pin", d
    assert d.version is None
    assert "no == version" in (d.detail or ""), d


def test_no_core_requirement_at_all_is_reported_as_such(tmp_path, monkeypatch):
    d = declared(tmp_path, monkeypatch, "platformdirs>=4", "requests>=2")
    assert d.status == "no_pin", d
    assert "no invisible-core requirement" in (d.detail or ""), d


def test_two_disagreeing_pins_are_ambiguous_not_a_pass(tmp_path, monkeypatch):
    d = declared(
        tmp_path, monkeypatch,
        'invisible-core==18.0.0; sys_platform == "win32"',
        'invisible-core==19.0.0; sys_platform == "linux"',
    )
    assert d.status == "ambiguous", d
    assert d.version is None
    assert "18.0.0" in (d.detail or "") and "19.0.0" in (d.detail or ""), d


def test_a_source_checkout_with_no_install_record_declares_nothing():
    d = _pin.pin_declaration("this-distribution-does-not-exist")
    assert d.status == "no_record", d
    assert d.version is None


# ── "not checkable" is never "the pin holds" ─────────────────────────────────

@pytest.mark.parametrize("requires,expected_status", [
    ((), "no_pin"),
    (("invisible-core @ git+https://example.invalid/x.git",), "no_pin"),
    (('invisible-core==18.0.0; extra == "dev"',), "no_pin"),
    (("invisible-core==18.0.0", "invisible-core==19.0.0"), "ambiguous"),
])
def test_an_unusable_declaration_reports_not_checkable_with_a_reason(
    tmp_path, monkeypatch, requires, expected_status,
):
    """The rule this file exists to keep: a pin the code could not find or could
    not parse must never come back as "the pin holds". It comes back as
    not-checkable with the reason, which is the same distinction
    `python -m invisible_core doctor` prints."""
    site = tmp_path / "site"
    make_distinfo(site, "fake-consumer", "1.2.3", requires)
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()

    report = _pin.pin_report("fake-consumer")
    assert report["declared_status"] == expected_status, report
    assert report["verdict"] == "not-checkable", report
    assert report["reason"], report
    # And it still does not raise: an old consumer must keep importing.
    _pin.assert_core_pin("fake-consumer")


def test_the_real_installation_reports_a_pin_that_holds():
    """Not a tautology: this reads the installed wrapper's real Requires-Dist
    and this repo's real seal, and is the test that catches a seal rolled
    without scripts/sync_core_pin.py being run."""
    report = _pin.pin_report("invisible-playwright")
    assert report["verdict"] in ("holds", "not-checkable"), report
    if report["verdict"] == "not-checkable":
        pytest.skip("invisible-playwright is not installed here: " + str(report["reason"]))
    assert report["have"] == report["want"], report


# ── the pure verdict function, per branch ────────────────────────────────────
#
# Labelled as such: these prove the WORDING of each branch. What is fed into
# them is proved by the layouts above, which is the half the old suite skipped.

def verdict(**kw):
    base = dict(dist_name="invisible-playwright", want="18.0.0", have="19.0.0",
                recorded="19.0.0", dist_version="0.3.5")
    base.update(kw)
    return _pin.pin_problem(**base)


def test_a_plain_mismatch_names_both_versions_and_the_remedy():
    msg = verdict()
    assert msg and "18.0.0" in msg and "19.0.0" in msg
    assert 'pip install "invisible-core==18.0.0"' in msg
    assert "--no-deps" in msg


def test_a_stale_install_record_is_told_to_force_reinstall():
    """pip and pip check both read the record, so both call this environment
    healthy. A plain install is a no-op because pip believes the pin is already
    satisfied; only --force-reinstall repairs it."""
    msg = verdict(recorded="18.0.0")
    assert msg and 'pip install --force-reinstall "invisible-core==18.0.0"' in msg
    assert "pip check" in msg


def test_the_mismatch_message_names_the_file_the_version_comes_from():
    """It used to blame invisible_core/seal.json alone. The version is
    <seal tag>.<CORE_REVISION>.0 and CORE_REVISION is a literal in _version.py,
    so the user has to be told which file to open."""
    for msg in (verdict(), verdict(recorded="18.0.0"),
                verdict(editable_path=r"C:\some\where")):
        assert "_version.py" in msg, msg
        assert "CORE_REVISION" in msg, msg


def test_an_undeterminable_version_is_rendered_honestly_not_as_a_pass():
    """The have=None case is folded into the ordinary mismatch message instead of
    living in its own branch. What must never happen is the empty string being
    printed as if it were a version, or the comparison silently passing."""
    msg = verdict(have=None, recorded="0.2.0")
    assert msg, "an undeterminable installed version compared equal to the pin"
    assert "undeterminable" in msg, msg
    assert "None" not in msg, msg
    assert 'pip install "invisible-core==18.0.0"' in msg


def test_a_matching_pair_is_silent():
    assert verdict(have="18.0.0", recorded="18.0.0") is None


def test_no_declared_pin_is_silent_but_is_not_a_pass():
    """pin_problem has nothing to say; pin_report is the one that has to call it
    not-checkable, and that is asserted above against real metadata."""
    assert verdict(want=None) is None


def test_the_escape_hatch_warns_instead_of_raising(tmp_path, monkeypatch):
    """INVISIBLE_CORE_PIN=allow mirrors INVISIBLE_PLAYWRIGHT_SKEW=allow. It is a
    real bypass, so it stays noisy: a warning on every import, not a silent
    pass."""
    import warnings

    site = tmp_path / "hatch_site"
    make_distinfo(site, "fake-consumer", "1.2.3", ("invisible-core==0.0.1",))
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    monkeypatch.setenv("INVISIBLE_CORE_PIN", "allow")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _pin.assert_core_pin("fake-consumer")
    assert len(caught) == 1
    assert issubclass(caught[0].category, RuntimeWarning)
    assert "0.0.1" in str(caught[0].message)


# ── the module keeps its own constraints ─────────────────────────────────────

# The IMPLEMENTATION, not the alias. `_pin.py` is two lines of
# `sys.modules[__name__] = pin` since 2026-07-27; scanning it for the pip
# seam found zero occurrences and passed nothing - a source gate pointed at
# the wrong file reads exactly like a clean source.
SOURCE = (REAL_PKG / "pin.py").read_text(encoding="utf-8")


def test_every_pip_command_goes_through_the_one_seam():
    """This module DOES install now, so "it never shells out" is no longer the
    property to assert. The property that replaced it is that there is exactly
    one place where a command is built and exactly one place where it can be
    run, because a second one would not be covered by any of the guards.

    subprocess is reached only from inside the seam, and pip is never invoked
    in-process (pip.main leaves the resolver's state in the importing process)."""
    assert SOURCE.count("import subprocess") == 1, SOURCE.count("import subprocess")
    assert "pip.main" not in SOURCE
    assert "check_call" not in SOURCE
    body = SOURCE.split("def _run_pip(")[1].split("\nINSTALL_RUNNER")[0]
    assert "import subprocess" in body, "subprocess is reached outside the seam"


def test_the_report_only_path_never_reaches_the_installer(tmp_path, monkeypatch):
    """assert_core_pin / pin_report are what the doctor and the CLI call. They
    diagnose; the repair is enforce_core_pin's alone. A seam call from here would
    make `invisible-playwright version` mutate site-packages."""
    calls = []
    monkeypatch.setattr(_pin, "INSTALL_RUNNER",
                        lambda cmd, *, execute: calls.append((list(cmd), execute)))
    site = tmp_path / "site"
    make_distinfo(site, "fake-consumer", "1.2.3", ("invisible-core==0.0.1",))
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()

    report = _pin.pin_report("fake-consumer")
    assert report["verdict"] == "violated", report
    with pytest.raises(ImportError):
        _pin.assert_core_pin("fake-consumer")
    assert calls == [], calls


def test_the_module_imports_only_the_standard_library_at_module_level():
    """Ordering hazard: a heavy import here turns a core that is broken in some
    other way into a traceback about an internal module instead of the remedy.
    The two sibling imports it needs (_version, seal) stay inside functions."""
    for line in SOURCE.splitlines():
        if re.match(r"^(import|from)\s", line):
            assert "_version" not in line, line
            assert " seal" not in line, line
            assert "requests" not in line and "platformdirs" not in line, line


# ═════════════════════════════════════════════════════════════════════════════
#  ONE requirement parser
# ═════════════════════════════════════════════════════════════════════════════
#
# There used to be six: this module's, the doctor's, the sync script's and one
# per test file, each with its own regex and therefore its own acceptance set.
# They disagreed, and the weakest one decided - the doctor dropped every
# requirement carrying a semicolon, so a legal environment marker on the pin line
# switched its half of the check off. The tests below are about the parser being
# ONE thing, not about it being clever.

# Every dialect any of the six copies ever had to cope with, and what the single
# parser must make of it. Feeding this table to a second implementation is how
# the next copy gets caught.
PIN_DIALECTS = [
    ("invisible-core==18.0.0", "18.0.0"),
    ("invisible-core (==18.0.0)", "18.0.0"),            # parenthesised dialect
    ("invisible_core==18.0.0", "18.0.0"),               # PEP 503 name
    ("Invisible.Core == 18.0.0", "18.0.0"),             # PEP 503 + spacing
    ("invisible-core[extras]==18.0.0", "18.0.0"),
    ('invisible-core==18.0.0; python_version >= "3.11"', "18.0.0"),
    ('invisible-core (== 18.0.0) ; sys_platform == "win32"', "18.0.0"),
    ("invisible-core>=18.0.0", None),                   # a range is not a pin
    ("invisible-core==18.0.0,<19", None),               # nor is a compound
    ("invisible-core", None),
    ("invisible-core @ git+https://example.invalid/x.git@abc", None),
]


@pytest.mark.parametrize("text,expected", PIN_DIALECTS)
def test_the_one_parser_agrees_on_every_dialect(text, expected):
    parsed = _pin.parse_requirement(text)
    assert parsed is not None, text
    assert parsed.name == "invisible-core", parsed
    assert parsed.pinned == expected, parsed


def test_the_parser_reads_a_requirement_that_is_not_the_core_too():
    """It is a requirement parser, not an invisible-core detector. The doctor and
    the sync script hand it whole dependency lists."""
    parsed = _pin.parse_requirement("platformdirs>=4.0 ; sys_platform != 'win32'")
    assert parsed.name == "platformdirs"
    assert parsed.pinned is None
    assert parsed.specifier == ">=4.0"
    assert parsed.marker == "sys_platform != 'win32'"


def test_two_spellings_of_one_requirement_compare_equal():
    """What scripts/sync_core_pin.py's gate turns on. Comparing raw literals made
    it refuse a push over a space and report that the pin "disagrees with the
    core", which is false and is how a maintainer learns to reach for
    --no-verify."""
    same = [
        "invisible-core==19.0.0",
        "invisible_core == 19.0.0",
        "invisible-core (==19.0.0)",
        "Invisible.Core==19.0.0",
    ]
    canon = {_pin.canonical_requirement(s) for s in same}
    assert len(canon) == 1, canon
    assert _pin.canonical_requirement("invisible-core==19.0.0") != \
        _pin.canonical_requirement("invisible-core==18.0.0")


def test_extras_are_normalised_and_sorted_before_comparing():
    assert _pin.canonical_requirement("invisible-core[B,a]==19.0.0") == \
        _pin.canonical_requirement("invisible-core[a, b]==19.0.0")


def test_a_marker_is_kept_and_distinguishes_two_requirements():
    """The pin line is allowed to carry an environment marker, and sync_core_pin
    preserves it. Collapsing it away would make two different requirements look
    like one."""
    with_marker = _pin.canonical_requirement(
        'invisible-core==19.0.0; sys_platform == "win32"')
    assert "sys_platform" in with_marker
    assert with_marker != _pin.canonical_requirement("invisible-core==19.0.0")


def test_something_unparseable_falls_back_to_collapsing_not_to_a_match():
    """A gate built on this must fail closed: whatever it cannot take apart has
    to compare UNEQUAL to a correct pin, never equal to it."""
    junk = "!!! not a requirement !!!"
    assert _pin.parse_requirement(junk) is None
    assert _pin.canonical_requirement(junk) == "!!! not a requirement !!!"
    assert _pin.canonical_requirement(junk) != \
        _pin.canonical_requirement("invisible-core==19.0.0")


def test_pin_declaration_is_a_thin_shell_over_the_shared_parser(monkeypatch):
    """Identity, not similarity: the metadata reader must not grow its own
    acceptance set. Everything that decides WHICH requirement is the pin has to
    be inside pin_from_requirements, where the doctor and the sync script can
    reach it.

    THE RECORD IS SUPPLIED BY THE TEST, and that is the fix. This used to call
    `pin_declaration("invisible-playwright")` and assert the spy fired - but
    pin_declaration returns `no_record` WITHOUT calling anything when the named
    distribution has no install record. So it passed only on a machine that
    happened to have the wrapper installed beside the core, and was red in every
    clean CI. Naming the core instead is no better: in a source checkout on
    PYTHONPATH the core has no record either. A test of delegation must not
    depend on what is installed at all.
    """
    seen = {}

    def spy(requires, *, dist_name):
        seen["requires"] = list(requires)
        seen["dist_name"] = dist_name
        return _pin.PinDeclaration("9.9.9", "pinned", None)

    monkeypatch.setattr(_pin, "_requires",
                        lambda name: ["platformdirs>=4", "invisible-core==1.2.3"])
    monkeypatch.setattr(_pin, "pin_from_requirements", spy)

    got = _pin.pin_declaration("some-consumer")
    assert got.version == "9.9.9", got
    assert seen["dist_name"] == "some-consumer"
    assert "invisible-core==1.2.3" in seen["requires"], (
        "pin_declaration filtered the requirement list before handing it over, "
        "which is the acceptance logic that has to live in one place")


def test_pin_declaration_reports_no_record_rather_than_inventing_a_version(
        monkeypatch):
    """The other half, and the one that decides whether a missing record is
    safe. Answering None with status `no_record` lets the caller distinguish
    "nothing to enforce" from "healthy"; answering a version would make a
    source checkout look like a satisfied pin."""
    def missing(name):
        raise _pin.PackageNotFoundError(name)

    monkeypatch.setattr(_pin, "_requires", missing)
    got = _pin.pin_declaration("not-installed-anywhere")
    assert got.version is None
    assert got.status == "no_record"
    assert "no install record" in (got.detail or "")


def test_pin_from_requirements_takes_a_plain_list_so_every_caller_can_use_it():
    """The doctor reads Requires-Dist through its own facts object and the sync
    script reads [project].dependencies out of pyproject. Neither can call a
    function that insists on importlib.metadata, and a function they cannot call
    is a function they will re-implement."""
    d = _pin.pin_from_requirements(
        ["platformdirs>=4", 'invisible-core==18.0.0; python_version >= "3.11"'],
        dist_name="some-consumer")
    assert d.status == "pinned" and d.version == "18.0.0", d


OWNED_SOURCES = {
    "invisible_core/_pin.py": REAL_PKG / "_pin.py",
}


def test_no_owned_file_carries_a_second_requirement_regex():
    """The consolidation, asserted as a property rather than as a promise. A new
    `re.compile` whose pattern mentions a requirement operator is a new
    acceptance set, and a new acceptance set is a new disagreement."""
    offenders = []
    for label, path in OWNED_SOURCES.items():
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if "re.compile" not in line:
                continue
            if "==" in line and "_REQ_RE" not in line and "_PINNED_RE" not in line \
                    and "_EXTRA_MARKER_RE" not in line:
                offenders.append(f"{label}:{n}: {line.strip()}")
    assert offenders == [], offenders


# ═════════════════════════════════════════════════════════════════════════════
#  The runtime repair
# ═════════════════════════════════════════════════════════════════════════════
#
# Every test here runs in a subprocess against a real stub site-packages, with a
# RECORDING installer wired into _pin.INSTALL_RUNNER. The recorder keeps the two
# questions apart: "which command was formed" is read off the recorded call,
# "was execution requested" is the execute flag, and "did the environment
# actually change" is asserted against the version derived AFTER the repair, out
# of the files the recorder rewrote. An implementation that prints the command
# and stops fails the third one, which is the mutation this whole section exists
# to catch.
#
# No test here can reach pip: INSTALL_RUNNER is replaced before enforce_core_pin
# is called, and every OTHER subprocess in this file runs with the kill switch on
# (see child_env).

def run_autofix(
    site: Path,
    *,
    dist: str = "fake-consumer",
    ok: bool = True,
    new_tag: str | None = None,
    boom: bool = False,
    wipe_direct_url: str | None = None,
    core_preimported: bool = False,
    times: int = 1,
    env: dict | None = None,
) -> dict:
    """Drive enforce_core_pin() in a child with a recording installer.

    ok           what the fake install reports back.
    new_tag      when set, the fake install REWRITES the stub's seal.json, which
                 is the only way the derived version can move. Leaving it None
                 with ok=True is the "pip said 0 and changed nothing" case.
    wipe_direct_url  a path the fake install deletes, to reproduce pip replacing
                 the .dist-info an editable core's path was read from.
    """
    child = dict(os.environ)
    child.pop(_pin.AUTOFIX_ENV, None)          # the repair is ON by default
    child.pop(_pin.AUTOFIX_ATTEMPTED_ENV, None)
    child.update(env or {})

    code = textwrap.dedent(f"""
        import io, json, pathlib, sys
        sys.path.insert(0, {str(site)!r})
        from invisible_core import _pin

        CALLS = []

        def recorder(cmd, *, execute):
            CALLS.append({{"cmd": list(cmd), "execute": bool(execute)}})
            if {boom!r}:
                raise OSError("pip could not be started")
            if execute and {ok!r}:
                if {new_tag!r}:
                    (pathlib.Path({str(site)!r}) / "invisible_core" / "seal.json"
                     ).write_text(json.dumps({{"tag": {new_tag!r}}}), encoding="utf-8")
                if {wipe_direct_url!r}:
                    pathlib.Path({wipe_direct_url!r}).unlink()
            return _pin.InstallOutcome(bool(execute and {ok!r}), "recorded", "recorded")

        _pin.INSTALL_RUNNER = recorder
        say = io.StringIO()
        out = {{"__pin_file__": _pin.__file__}}
        out["before"] = _pin.installed_core_version()
        out["raised"] = []
        for _ in range({int(times)}):
            try:
                _pin.enforce_core_pin(
                    {dist!r}, core_preimported={bool(core_preimported)!r}, stream=say)
                out["raised"].append(None)
            except ImportError as e:
                out["raised"].append(str(e))
        out["after"] = _pin.installed_core_version()
        out["calls"] = CALLS
        out["said"] = say.getvalue()
        out["attempted_env"] = _pin.os.environ.get(_pin.AUTOFIX_ATTEMPTED_ENV)
        print("JSON:" + json.dumps(out, default=str))
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=child)
    assert r.returncode == 0, (
        "enforce_core_pin let something escape as a traceback:\n" + r.stdout + r.stderr)
    payload = [ln for ln in r.stdout.splitlines() if ln.startswith("JSON:")]
    assert payload, r.stdout + r.stderr
    out = json.loads(payload[-1][len("JSON:"):])
    assert str(site) in out["__pin_file__"], out["__pin_file__"]
    return out


def mismatched_site(tmp_path, *, name="site", tag="firefox-18", want="19.0.0",
                    direct_url=None):
    """A stub environment whose core is one engine release behind its consumer."""
    site = tmp_path / name
    make_core(site, tag=tag)
    make_distinfo(site, "invisible-core", "18.0.0", direct_url=direct_url)
    make_distinfo(site, "fake-consumer", "1.2.3", (f"invisible-core=={want}",))
    return site


def test_a_mismatch_is_repaired_in_process_and_the_import_continues(tmp_path):
    """The behaviour the owner asked for. Not "it printed a command": the version
    that will run afterwards, derived from the files on disk, is the declared
    one, and enforce_core_pin returned instead of raising. An implementation that
    only prints leaves `after` at 18.0.0 and raises, and fails here."""
    site = mismatched_site(tmp_path)
    out = run_autofix(site, new_tag="firefox-19")

    assert out["before"] == "18.0.0", out
    assert out["after"] == "19.0.0", out
    assert out["raised"] == [None], out["raised"]


def test_the_repair_forms_the_pinned_install_command_and_asks_for_execution(tmp_path):
    """Two assertions, deliberately separate: WHICH command was built, and that
    execution was actually requested. A repair that formats the command and calls
    the seam with execute=False would satisfy the first and not the second."""
    site = mismatched_site(tmp_path)
    out = run_autofix(site, new_tag="firefox-19")

    assert len(out["calls"]) == 1, out["calls"]
    call = out["calls"][0]
    assert call["execute"] is True, call
    assert call["cmd"][1:] == [
        "-m", "pip", "install", "--force-reinstall", "invisible-core==19.0.0"], call
    assert call["cmd"][0].lower().endswith(("python.exe", "python", "python3")) or \
        Path(call["cmd"][0]).exists(), call


def test_the_repair_says_what_it_will_do_before_it_does_it_and_what_it_did_after(
    tmp_path,
):
    """A package that silently mutates the environment it was imported into is
    worse than one that asks. Order matters: the announcement has to precede the
    install, or the first thing the user learns is that it already happened."""
    site = mismatched_site(tmp_path)
    said = run_autofix(site, new_tag="firefox-19")["said"]

    before_at = said.find("repairing it now")
    after_at = said.find("repaired in place")
    assert before_at != -1, said
    assert after_at != -1, said
    assert before_at < after_at, said
    assert "invisible-core==19.0.0" in said, said
    assert "No restart needed" in said, said


def test_an_install_that_reports_success_but_changes_nothing_is_not_a_repair(tmp_path):
    """pip's exit code is not the claim. The claim is that the version about to
    run is the declared one, so the repair re-derives it from the files and
    refuses to call itself done otherwise. Without this, a no-op install
    (a stale wheel cache, an index that served the same artifact) would be
    reported as a fix and the user would run the wrong core believing otherwise."""
    site = mismatched_site(tmp_path)
    out = run_autofix(site, ok=True, new_tag=None)

    assert out["after"] == "18.0.0", out
    assert out["raised"][0], "a no-op install was accepted as a repair"
    assert "still derives" in out["raised"][0], out["raised"][0]


def test_the_repair_refuses_when_the_core_was_already_imported(tmp_path):
    """Someone did `import invisible_core` before importing the consumer. Then
    dropping the package out of sys.modules and re-importing it is not a reload:
    whatever they are holding keeps pointing at the old version, and two versions
    of the config layer are live in one process. Refused, and reported."""
    site = mismatched_site(tmp_path)
    out = run_autofix(site, new_tag="firefox-19", core_preimported=True)

    assert out["calls"] == [], out["calls"]
    assert out["after"] == "18.0.0", out
    msg = out["raised"][0]
    assert msg and "version mismatch" in msg, msg
    assert "already imported" in msg, msg


def test_the_repair_is_attempted_at_most_once_per_process(tmp_path):
    """The loop guard, on the path that would actually loop: a failing repair
    followed by another consumer importing in the same interpreter."""
    site = mismatched_site(tmp_path)
    out = run_autofix(site, ok=False, times=2)

    assert len(out["calls"]) == 1, out["calls"]
    assert all(out["raised"]), out["raised"]
    assert "already attempted" in out["raised"][1], out["raised"][1]


def test_the_loop_guard_is_written_into_the_environment_for_children(tmp_path):
    """A module flag cannot stop a process that re-execs. The env var can, and it
    is set BEFORE the attempt, so a repair that crashes the interpreter still
    leaves the flag behind."""
    site = mismatched_site(tmp_path)
    out = run_autofix(site, ok=False)
    assert out["attempted_env"] == "1", out


def test_a_process_started_with_the_guard_already_set_does_not_attempt(tmp_path):
    site = mismatched_site(tmp_path)
    out = run_autofix(site, new_tag="firefox-19",
                      env={_pin.AUTOFIX_ATTEMPTED_ENV: "1"})

    assert out["calls"] == [], out["calls"]
    assert out["after"] == "18.0.0", out
    assert "already attempted" in out["raised"][0], out["raised"][0]


def test_an_install_failure_degrades_to_the_message_it_replaced(tmp_path):
    """No network, no write permission on site-packages, a locked-down image.
    The user gets the diagnosis and the command, exactly as before this existed,
    plus what was tried. Never a traceback: run_autofix asserts the child exited
    0, which it only does because the failure came back as our ImportError."""
    site = mismatched_site(tmp_path)
    out = run_autofix(site, ok=False)

    msg = out["raised"][0]
    assert msg and "invisible-core version mismatch" in msg, msg
    assert 'pip install "invisible-core==19.0.0"' in msg, msg
    assert "did not succeed" in msg, msg
    assert out["after"] == "18.0.0", out


def test_an_installer_that_raises_is_a_failed_repair_not_a_crash(tmp_path):
    """The seam is allowed to blow up (no pip, no exec permission, a sandbox that
    kills the child). That is still a failed repair, not an unhandled OSError
    arriving in the middle of somebody's import."""
    site = mismatched_site(tmp_path)
    out = run_autofix(site, boom=True)

    msg = out["raised"][0]
    assert msg and "version mismatch" in msg, msg
    assert "OSError" in msg, msg


def test_the_kill_switch_turns_the_repair_back_into_a_report(tmp_path):
    site = mismatched_site(tmp_path)
    out = run_autofix(site, new_tag="firefox-19", env={_pin.AUTOFIX_ENV: "off"})

    assert out["calls"] == [], out["calls"]
    assert out["after"] == "18.0.0", out
    assert "switched off" in out["raised"][0], out["raised"][0]


def test_a_pin_that_holds_installs_nothing(tmp_path):
    """The floor, not a wall. This is also what stops the repair from firing on
    every import of a healthy environment."""
    site = mismatched_site(tmp_path, want="18.0.0")
    out = run_autofix(site)

    assert out["raised"] == [None], out["raised"]
    assert out["calls"] == [], out["calls"]


def test_the_escape_hatch_still_wins_over_the_repair(tmp_path):
    """INVISIBLE_CORE_PIN=allow means "I know, run it anyway". Installing over
    the user's environment after they said that would be the opposite of what
    they asked for."""
    site = mismatched_site(tmp_path)
    out = run_autofix(site, new_tag="firefox-19", env={_pin.SKIP_ENV: "allow"})

    assert out["calls"] == [], out["calls"]
    assert out["raised"] == [None], out["raised"]
    assert out["after"] == "18.0.0", out


# ── the editable case: repaired too, and recoverable ─────────────────────────

def editable_site(tmp_path, checkout: Path):
    site = tmp_path / "site"
    make_core(site, tag="firefox-18")
    dist_info = make_distinfo(
        site, "invisible-core", "18.0.0",
        direct_url={"url": checkout.as_uri(), "dir_info": {"editable": True}})
    make_distinfo(site, "fake-consumer", "1.2.3", ("invisible-core==19.0.0",))
    return site, dist_info


def test_an_editable_core_is_repaired_too(tmp_path):
    """The owner's explicit instruction. An editable install is not a reason to
    stop: it is a reason to say what is being lost."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    site, _ = editable_site(tmp_path, checkout)
    out = run_autofix(site, new_tag="firefox-19")

    assert out["after"] == "19.0.0", out
    assert out["raised"] == [None], out["raised"]
    assert len(out["calls"]) == 1 and out["calls"][0]["execute"] is True, out["calls"]


def test_the_editable_repair_warns_before_it_detaches_the_working_tree(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    site, _ = editable_site(tmp_path, checkout)
    said = run_autofix(site, new_tag="firefox-19")["said"]

    warned_at = said.find("EDITABLE")
    ran_at = said.find("repairing it now")
    assert warned_at != -1, said
    assert warned_at < ran_at, said
    assert str(checkout) in said, said


def test_the_editable_repair_prints_the_command_that_reattaches_the_tree(tmp_path):
    """An editable install points at the owner's source tree, and installing over
    it detaches the environment from that tree: the sources survive, but imports
    stop coming from them and local edits stop having any effect. The undo has to
    be handed over, not left to be worked out."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    site, _ = editable_site(tmp_path, checkout)
    said = run_autofix(site, new_tag="firefox-19")["said"]

    assert f"pip install -e {checkout}" in said, said
    assert "NO LONGER an editable install" in said, said


def test_the_editable_path_is_read_before_the_install_destroys_the_record(tmp_path):
    """pip replaces the .dist-info, and direct_url.json with it. Reading the path
    after the install returns None and the recovery command would silently stop
    being printed, on exactly the machines that need it - this project's own,
    where all three distributions are editable."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    site, dist_info = editable_site(tmp_path, checkout)
    said = run_autofix(
        site, new_tag="firefox-19",
        wipe_direct_url=str(dist_info / "direct_url.json"))["said"]

    assert f"pip install -e {checkout}" in said, said


def test_a_non_editable_repair_prints_no_reattach_command(tmp_path):
    """The crying-wolf half: an index install has no working tree to protect, and
    telling that user to `pip install -e` somewhere would be nonsense."""
    site = mismatched_site(tmp_path)
    said = run_autofix(site, new_tag="firefox-19")["said"]

    assert "pip install -e" not in said, said
    assert "EDITABLE" not in said, said


def test_the_module_holds_no_version_literal():
    """The expected version has exactly one source, the consumer's pyproject.
    A constant here would be the original bug in miniature."""
    offenders = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(SOURCE.splitlines(), 1)
        if re.search(r"invisible[-_]core\s*[=><!~]=\s*[\"']?\d", line, re.I)
    ]
    assert offenders == [], offenders


def test_the_messages_carry_no_em_dashes():
    """Project rule: no em-dashes anywhere, including user-facing error text."""
    assert "\u2014" not in SOURCE and "\u2013" not in SOURCE
