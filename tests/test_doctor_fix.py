"""`doctor --fix`: what it repairs, what it refuses, and that it never loops.

Hermetic by construction. Every fact the doctor reads is replaced here: the
seal, engine_status, iter_cached_engines, cache_root, ensure_binary, clear_cache
and the distribution metadata. Nothing in these tests can reach the real engine
cache, the network, or the interpreter this suite runs in.

Two things in here are gates rather than tests, and both exist because the
first version of this file proved nothing:

  * THE INSTALL SEAM. It used to be a hook nothing called, watched by a recorder
    asserted to be empty - which a mutation wiring `subprocess.run(["pip",
    "install", "--force-reinstall", ...])` straight into --fix passed with the
    whole suite green. The seam is now on the hot path (every pip command is
    FORMATTED through it), so the recorder sees each command that was formed and
    sees that none was ever asked to run. Two more layers below: a tripwire over
    every process-spawning primitive in the standard library, and an AST scan
    that fails if the module so much as imports one.
  * THE EDITABLE CHECK. It used to read one unvalidated file and answer
    yes/no, so a legacy `setup.py develop` install, a truncated direct_url.json
    and a record with no dir_info each concluded "not editable" and printed the
    --force-reinstall command that overwrites the owner's working tree. Each of
    those three is a test below.

Two later measurements shaped the rest of this file:

  * THE TRIPWIRE IS AUTOUSE. It used to be armed inside one test, and the test
    that drives the seam with execute=True sat outside it. Against a seam
    mutated to stop raising, that test really did spawn pip; the run survived
    only because the distribution does not resolve on the index yet. Nothing in
    this module may start a process, so the tripwire is now armed for every test
    in it rather than for the ones somebody remembered.
  * A GAP IN THE EDITABLE FIXTURES. The whole suite stayed green against a
    mutation making _pep610_verdict answer NOT_EDITABLE instead of UNKNOWN when
    direct_url.json is absent, because every fixture with no direct_url.json
    also had a module_path that decided the verdict on its own. The combination
    that mutation actually reaches - no direct_url.json AND no locatable
    importable files, i.e. a partially clobbered site-packages - is
    test_a_clobbered_site_packages_*, and it goes red under it.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
from pathlib import Path

import pytest

from invisible_core import __main__ as doctor
from invisible_core import _pin
from invisible_core.__main__ import EDITABLE, NOT_EDITABLE, UNKNOWN, DistFacts

CORE = "invisible-core"
WRAPPER = "invisible-playwright"
MANAGER = "invisible-firefox"

# What the core's own files derive from seal.json, i.e. what would actually run.
DERIVED = doctor.DERIVED_CORE_VERSION
# Any version that is NOT that one. Computed, so a seal roll cannot make these
# tests pass for the wrong reason.
OTHER = "17.0.0" if DERIVED != "17.0.0" else "16.0.0"

# A synthetic editable URL. Deliberately NOT the maintainer's real checkout path:
# this file ships inside the core sdist, so a real path would publish the local
# workbench layout and would make the test meaningless on any other machine.
EDITABLE_URL = "file:///project/src/invisible_core"
EDITABLE_DIR = "/project/src/invisible_core"
SITE = "/venv/lib/python3.12/site-packages"


# ─────────────────────────────────────────────────────────────────── fakes


class FakeSeal:
    tag = "firefox-18"
    origin = "packaged"
    is_local = False

    def describe(self) -> str:
        return "firefox-18 (Firefox 151.0 build 20260724001949, seal abcdef012345)"


class Recorder:
    """Stands in for anything that would mutate the world. Records, never acts."""

    def __init__(self, result=None, raises=None):
        self.calls = []
        self._result = result
        self._raises = raises

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        if self._raises is not None:
            raise self._raises
        return self._result


class InstallSeam:
    """The recorder that stands in for INSTALL_RUNNER.

    It records every command the doctor forms and whether execution was asked
    for, then delegates to the real refusal so the printed text is the text the
    shipped code would print. `.executed` staying empty is the assertion; it is
    a real one only because `.formatted` does NOT stay empty on the paths that
    build a command.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, *, execute):
        self.calls.append((list(cmd), execute))
        return doctor._refuse_to_install(cmd, execute=execute)

    @property
    def formatted(self):
        return [c for c, ex in self.calls if not ex]

    @property
    def executed(self):
        return [c for c, ex in self.calls if ex]


def wrapper_pinning(version: str) -> DistFacts:
    return DistFacts(WRAPPER, True, "0.3.5",
                     (f"{CORE}=={version}", "playwright>=1.55,<=1.61.0"))


def index_core(record: str) -> DistFacts:
    """An ordinary index install: no direct_url.json, files in site-packages.

    This is the ONLY shape the doctor is allowed to hand an install command to,
    so it is spelled out in full rather than left to defaults.
    """
    return DistFacts(CORE, True, record, (), None,
                     direct_url_raw=None,
                     metadata_dir=f"{SITE}/invisible_core-{record}.dist-info",
                     module_path=f"{SITE}/invisible_core")


def editable_core(record: str) -> DistFacts:
    payload = {"url": EDITABLE_URL, "dir_info": {"editable": True}}
    return DistFacts(CORE, True, record, (), payload,
                     direct_url_raw=json.dumps(payload),
                     metadata_dir=f"{SITE}/invisible_core-{record}.dist-info",
                     module_path=EDITABLE_DIR)


def healthy_table() -> dict:
    return {CORE: index_core(DERIVED),
            WRAPPER: wrapper_pinning(DERIVED),
            MANAGER: DistFacts(MANAGER, False)}


@pytest.fixture(autouse=True)
def no_process(monkeypatch):
    """Armed for EVERY test in this module, not for the ones that ask.

    doctor is a diagnostic: no path through it, and no path through a test of
    it, may start a process. Arming this per-test was how
    test_the_install_seam_refuses_execution_and_only_formats_otherwise came to
    call the seam with execute=True outside any tripwire - and a seam mutated to
    stop raising then really did hand `pip install --force-reinstall
    invisible-core` to subprocess, against the developer's own interpreter.

    Every standard-library way to start a process is replaced for the duration.
    The recorder is returned so a test can assert on it; the raise is what stops
    a real install before it happens.
    """
    spawned = []

    def trip(name):
        def _f(*a, **kw):
            spawned.append((name, a, kw))
            raise AssertionError(f"a process was started via {name}: {a!r}")
        return _f

    for mod, attr in ((subprocess, "run"), (subprocess, "Popen"),
                      (subprocess, "call"), (subprocess, "check_call"),
                      (subprocess, "check_output"), (os, "system"), (os, "popen")):
        monkeypatch.setattr(mod, attr, trip(f"{mod.__name__}.{attr}"), raising=False)
    for attr in ("execv", "execvp", "execve", "spawnv", "spawnvp", "posix_spawn"):
        monkeypatch.setattr(os, attr, trip(f"os.{attr}"), raising=False)
    return argparse.Namespace(spawned=spawned)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Wire the doctor to fakes and hand back the knobs, so each test states
    only the one fact it is about."""
    state = argparse.Namespace(
        seal=FakeSeal(),
        engine=[(True, "firefox-18 (Firefox 151.0)")],  # queue of engine_status results
        facts=healthy_table(),
        ensure=Recorder(result=tmp_path / "cache" / "firefox-18" / "firefox.exe"),
        clear=Recorder(result=[]),
        install=InstallSeam(),
        trees=[],
        problems_of=lambda ident, seal: [],
    )

    def engine_status(seal=None):
        return state.engine[0] if len(state.engine) == 1 else state.engine.pop(0)

    monkeypatch.setattr(doctor, "active_seal", lambda: state.seal)
    monkeypatch.setattr(doctor, "engine_status", engine_status)
    monkeypatch.setattr(doctor, "iter_cached_engines", lambda: iter(list(state.trees)))
    monkeypatch.setattr(doctor, "engine_problems", lambda i, s: state.problems_of(i, s))
    monkeypatch.setattr(doctor, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.setattr(doctor, "ensure_binary", lambda *a, **kw: state.ensure(*a, **kw))
    monkeypatch.setattr(doctor, "clear_cache", lambda *a, **kw: state.clear(*a, **kw))
    monkeypatch.setattr(doctor, "INSTALL_RUNNER", state.install)
    # NOTE: nothing here stands in for the pin parser. The requirement strings
    # in state.facts[...].requires go through the SHIPPED
    # invisible_core._pin.pin_from_requirements, so a spelling that parser stops
    # understanding shows up here as a changed doctor verdict rather than as a
    # test of a fake that agrees with itself.
    monkeypatch.setattr(doctor, "_dist_facts",
                        lambda n: state.facts.get(n, DistFacts(n, False)))
    return state


def run(*argv) -> int:
    return doctor.main(["doctor", *argv])


# ──────────────────────────────── D4: the install seam is real, not decorative


def test_the_install_seam_is_on_the_hot_path_and_is_never_asked_to_run(env, capsys):
    """The seam must be EXERCISED, or "the recorder stayed empty" proves nothing.

    Against the version where INSTALL_RUNNER was assigned once and called from
    nowhere, this fails on the first assertion: no command ever reached the
    seam, so the recorder's silence was silence about a door nobody used.
    """
    env.facts = {CORE: index_core(DERIVED),
                 WRAPPER: wrapper_pinning(OTHER),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run("--fix")
    out = capsys.readouterr().out

    assert env.install.formatted, "no pip command went through the seam, so the seam is dead"
    assert env.install.formatted == [[Path(doctor.sys.executable).name, "-m", "pip",
                                      "install", f"{CORE}=={OTHER}"]]
    assert env.install.executed == [], "doctor asked for an install to RUN"
    # The text the user sees is the text the seam produced, so a bypass that
    # printed its own command would be caught here too.
    assert f'-m pip install "{CORE}=={OTHER}"' in out
    assert rc == 1


def test_the_install_seam_refuses_execution_and_only_formats_otherwise(no_process):
    """The known-bad input for the seam itself, driven behind the tripwire.

    This is the ONE call in the suite that asks the seam to execute, so it is
    the one call that must never be made in the open. It used to be: against a
    seam mutated to run the command instead of raising, pytest spawned pip with
    `--force-reinstall invisible-core` against the developer's own environment,
    and the only reason nothing was overwritten is that the name does not
    resolve on the index yet. Publishing removes that accident.

    The autouse tripwire is asserted rather than assumed: `no_process.spawned`
    stays empty because nothing ran, and if the seam is ever mutated into an
    installer the tripwire fails the test instead of the installer succeeding.
    """
    cmd = ["pip", "install", "--force-reinstall", CORE]
    with pytest.raises(RuntimeError) as e:
        doctor._refuse_to_install(cmd, execute=True)
    assert "never install a Python package" in str(e.value)
    assert doctor._refuse_to_install(cmd, execute=False) == \
        f'pip install --force-reinstall {CORE}'
    assert doctor.INSTALL_RUNNER is doctor._refuse_to_install
    assert no_process.spawned == [], "the seam handed a command to a real process"


def test_the_tripwire_is_armed_for_every_test_in_this_module(no_process):
    """The gate on the gate. A tripwire nobody can show firing is a decoration.

    Known-bad input: a direct subprocess.run from inside a test body. It must be
    stopped here, because that is exactly the shape the seam-refusal test was
    running unprotected.
    """
    with pytest.raises(AssertionError, match="a process was started via subprocess.run"):
        subprocess.run(["pip", "--version"])
    assert [n for n, _a, _k in no_process.spawned] == ["subprocess.run"]
    no_process.spawned.clear()   # this one was ours, not the doctor's


def test_no_doctor_path_spawns_a_process(env, no_process, capsys):
    """Catches an installer wired in AROUND the seam.

    The tripwire itself is the autouse fixture above; what this test adds is the
    DRIVING: every --fix path the doctor has is walked through it. This is the
    assertion the mutation `subprocess.run(["pip", "install",
    "--force-reinstall", ...])` inside --fix has to get past, and it cannot:
    there is no way to run pip that does not go through one of these.
    """
    tables = [
        # a plain pin mismatch against an index install (the install-shaped path)
        {CORE: index_core(DERIVED), WRAPPER: wrapper_pinning(OTHER),
         MANAGER: DistFacts(MANAGER, False)},
        # a stale record, which is the path that mentions --force-reinstall
        {CORE: index_core(OTHER), WRAPPER: wrapper_pinning(OTHER),
         MANAGER: DistFacts(MANAGER, False)},
        # an editable core, the path that must not even form a command
        {CORE: editable_core(DERIVED), WRAPPER: wrapper_pinning(OTHER),
         MANAGER: DistFacts(MANAGER, False)},
    ]
    for table in tables:
        env.facts = table
        env.engine = [(False, "not downloaded"), (True, "firefox-18 (Firefox 151.0)")]
        run("--fix")
        capsys.readouterr()

    assert no_process.spawned == []
    assert env.install.executed == []


def test_the_doctor_module_cannot_start_a_process_at_all():
    """AST scan, so the bypass cannot be written rather than merely not taken.

    Parsed rather than grepped on purpose: this module's own docstring discusses
    subprocess.run by name, and a text scan that trips on prose is a scan people
    delete.
    """
    tree = ast.parse(Path(doctor.__file__).read_text(encoding="utf-8"))
    banned_modules = {"subprocess", "pty", "multiprocessing", "runpy", "pip"}
    banned_attrs = {"system", "popen", "execv", "execve", "execvp", "execvpe",
                    "spawnv", "spawnve", "spawnvp", "posix_spawn", "fork", "forkpty"}

    offences = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in banned_modules:
                    offences.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in banned_modules:
                offences.append(f"line {node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Attribute) and node.attr in banned_attrs:
            base = getattr(node.value, "id", "")
            if base in ("os", "subprocess", "pip"):
                offences.append(f"line {node.lineno}: {base}.{node.attr}")

    assert offences == [], (
        "invisible_core/__main__.py can start a process. It is a diagnostic; the "
        "only command it may produce is text for a human:\n  " + "\n  ".join(offences))


def test_the_doctor_imports_only_the_reading_half_of_pin():
    """The doctor shares _pin's parser, and _pin also contains an INSTALLER.

    invisible_core._pin grew a real pip runner (_run_pip / INSTALL_RUNNER /
    enforce_core_pin) for the consumers' import floor, which is allowed to
    repair the environment it was imported into. The doctor is not: it reports.
    Since the doctor now imports that module, "this file cannot start a process"
    stops being provable from this file's own imports alone - `from ._pin import
    INSTALL_RUNNER` names nothing banned and calls nothing banned.

    So the import is pinned to the reading half by name. The runtime tripwire
    above still covers the dynamic path (it patches subprocess itself, and
    _pin's runner imports subprocess inside the function, so it resolves the
    patched attribute); this is the static half.
    """
    readers = {"CORE_NAME", "pin_from_requirements", "pin_declaration",
               "declared_core_pin", "PinDeclaration", "parse_requirement",
               "normalise_name", "installed_core_version", "recorded_core_version",
               "editable_core_path", "probe_core", "pin_report", "pin_problem"}
    tree = ast.parse(Path(doctor.__file__).read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").lstrip(".") == "_pin":
            imported += [(node.lineno, alias.name) for alias in node.names]
    assert imported, "the doctor no longer imports _pin: check the parser is still shared"
    offences = [f"line {n}: from ._pin import {name}"
                for n, name in imported if name not in readers]
    assert offences == [], (
        "the doctor imported something from _pin that is not a reader. _pin can "
        "install; doctor may not:\n  " + "\n  ".join(offences))


# ──────────────────────────────────── the editable guard (the critical one)


def test_fix_refuses_to_touch_an_editable_core_and_makes_no_install_call(env, capsys):
    """A version mismatch against an EDITABLE core is reported and refused.

    The tree behind the file: URL is the owner's live source. A pip
    --force-reinstall would replace it with a copy from the index, uncommitted
    work included. doctor must name it, refuse it, and touch nothing.
    """
    env.facts = {CORE: editable_core(DERIVED),
                 WRAPPER: wrapper_pinning(OTHER),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run("--fix")
    out = capsys.readouterr().out

    assert env.install.calls == [], "a pip command was formed for an editable tree"
    assert env.ensure.calls == [], "doctor touched the engine cache over a package problem"
    assert env.clear.calls == []
    assert rc == 1
    assert "EDITABLE install at" in out
    assert "invisible_core" in out.split("EDITABLE install at")[1].splitlines()[0]
    assert "Not offering a reinstall" in out
    assert "--force-reinstall" not in out, "handed the user the command that eats the tree"
    assert "pip install" not in out, "printed an install command for an editable tree"
    assert f"{CORE}=={OTHER}   ->   {CORE}=={DERIVED}" in out


# ─────────────────────── D5: the three ways the editable check was defeated


def legacy_develop_core(record: str) -> DistFacts:
    """`setup.py develop` / `pip install -e` before PEP 610 existed.

    No direct_url.json at all, an .egg-info sitting in the source tree, and the
    importable files outside site-packages. The old check read the absent file
    and answered "not editable".
    """
    return DistFacts(CORE, True, record, (), None,
                     direct_url_raw=None,
                     metadata_dir="/project/src/invisible_core.egg-info",
                     module_path=EDITABLE_DIR,
                     legacy_editable=("legacy invisible_core.egg-info outside "
                                      "site-packages at /project/src",),
                     git_worktree="/project")


def truncated_direct_url_core(record: str) -> DistFacts:
    """A half-written direct_url.json. Parsing fails, evidence is unusable."""
    return DistFacts(CORE, True, record, (), None,
                     direct_url_raw='{"url": "file:///project/src", "dir_in',
                     metadata_dir=f"{SITE}/invisible_core-{record}.dist-info",
                     module_path=f"{SITE}/invisible_core")


def no_dir_info_core(record: str) -> DistFacts:
    """A direct_url.json that parses but carries no dir_info, no vcs_info and no
    archive_info. `(du.get("dir_info") or {}).get("editable")` read this as
    False and printed the reinstall command."""
    payload = {"url": EDITABLE_URL}
    return DistFacts(CORE, True, record, (), payload,
                     direct_url_raw=json.dumps(payload),
                     metadata_dir=f"{SITE}/invisible_core-{record}.dist-info",
                     module_path=f"{SITE}/invisible_core")


def clobbered_site_packages_core(record: str) -> DistFacts:
    """No direct_url.json AND nothing importable to locate.

    The .dist-info is still in site-packages - pip and `pip check` read it and
    call the environment healthy - but the package directory beside it is gone
    or unreadable, so _locate_module answers "". That is a partially clobbered
    site-packages: a copy that overwrote half of it, a broken image layer, an
    interrupted uninstall.

    It is the ONE shape that isolates _pep610_verdict's UNKNOWN. Every other
    fixture with no direct_url.json carries a module_path that decides the
    verdict by itself - inside site-packages it is the confident negative,
    outside it is positive evidence of an editable - so a mutation making the
    absent file mean NOT_EDITABLE changes nothing for them and the suite stayed
    green. Here there is no other evidence, so that mutation turns "I cannot
    tell" into "safe to reinstall" and hands the user --force-reinstall over an
    installation nobody has classified.
    """
    return DistFacts(CORE, True, record, (), None,
                     direct_url_raw=None,
                     metadata_dir=f"{SITE}/invisible_core-{record}.dist-info",
                     module_path="")


def unlocatable_outside_site_packages_core(record: str) -> DistFacts:
    """The same hole from the other side: metadata that is not in site-packages
    either, so not even the .dist-info's location says what kind of install this
    is. A .dist-info (not an .egg-info) rules out the legacy-develop signal, and
    with no importable path there is nothing else left to read."""
    return DistFacts(CORE, True, record, (), None,
                     direct_url_raw=None,
                     metadata_dir=f"/opt/vendored/invisible_core-{record}.dist-info",
                     module_path="")


@pytest.mark.parametrize("factory,expected_state,marker", [
    (legacy_develop_core, EDITABLE, "EDITABLE install at"),
    (truncated_direct_url_core, UNKNOWN, "cannot tell whether this is an EDITABLE"),
    (no_dir_info_core, UNKNOWN, "cannot tell whether this is an EDITABLE"),
    (clobbered_site_packages_core, UNKNOWN, "cannot tell whether this is an EDITABLE"),
    (unlocatable_outside_site_packages_core, UNKNOWN,
     "cannot tell whether this is an EDITABLE"),
])
def test_defeated_editable_evidence_withholds_the_destructive_command(
        env, capsys, factory, expected_state, marker):
    """Each of these three printed `pip install --force-reinstall` before.

    The rule is now that only a POSITIVE not-editable finding unlocks an install
    command; absent or unparseable evidence withholds it and says why.
    """
    facts = factory(DERIVED)
    assert doctor._editable_of(facts).state == expected_state

    env.facts = {CORE: facts, WRAPPER: wrapper_pinning(OTHER),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run("--fix")
    out = capsys.readouterr().out

    assert rc == 1
    assert env.install.calls == [], "a pip command was formed on unproven evidence"
    assert "pip install" not in out
    assert "--force-reinstall" not in out
    assert marker in out
    assert f"{CORE}=={OTHER}   ->   {CORE}=={DERIVED}" in out


def test_only_a_positive_index_install_is_declared_safe_to_reinstall():
    """The classifier, case by case. Everything that is not a clean index
    install must fail closed."""
    assert doctor._editable_of(index_core(DERIVED)).safe_to_reinstall is True

    for facts in (editable_core(DERIVED), legacy_develop_core(DERIVED),
                  truncated_direct_url_core(DERIVED), no_dir_info_core(DERIVED),
                  clobbered_site_packages_core(DERIVED),
                  unlocatable_outside_site_packages_core(DERIVED)):
        e = doctor._editable_of(facts)
        assert e.safe_to_reinstall is False, facts
        assert e.why, "a refusal with no reason is a refusal nobody will believe"

    # dir_info present and explicitly not editable: a local non-editable install.
    payload = {"url": "file:///tmp/x", "dir_info": {}}
    local = DistFacts(CORE, True, DERIVED, (), payload,
                      direct_url_raw=json.dumps(payload),
                      module_path=f"{SITE}/invisible_core")
    assert doctor._editable_of(local).state == NOT_EDITABLE

    # A VCS install has no dir_info and that is CORRECT, not incomplete.
    payload = {"url": "git+https://example.invalid/x.git",
               "vcs_info": {"vcs": "git", "commit_id": "0" * 40}}
    vcs = DistFacts(CORE, True, DERIVED, (), payload,
                    direct_url_raw=json.dumps(payload),
                    module_path=f"{SITE}/invisible_core")
    assert doctor._editable_of(vcs).state == NOT_EDITABLE

    # An editable finder shim in site-packages, with no direct_url at all.
    shim = DistFacts(CORE, True, DERIVED, (), None, direct_url_raw=None,
                     module_path=f"{SITE}/invisible_core",
                     editable_finder=("__editable__.invisible_core-18.0.0.pth",))
    assert doctor._editable_of(shim).state == EDITABLE

    # dir_info of the wrong type is not a "no".
    payload = {"url": EDITABLE_URL, "dir_info": "yes"}
    junk = DistFacts(CORE, True, DERIVED, (), payload,
                     direct_url_raw=json.dumps(payload),
                     module_path=f"{SITE}/invisible_core")
    assert doctor._editable_of(junk).state == UNKNOWN

    # No install record at all: nothing to reinstall over, and nothing to fear.
    assert doctor._editable_of(DistFacts(CORE, False)).state == NOT_EDITABLE


def test_a_clobbered_site_packages_is_unknown_and_an_intact_one_is_not(env, capsys):
    """The discriminator, both sides of it, in one place.

    Same distribution, same absent direct_url.json, one difference: whether the
    importable files can be located. Under the mutation that makes an absent
    direct_url.json mean NOT_EDITABLE, the first half of this test goes red -
    doctor prints `pip install --force-reinstall` over an installation it has
    not classified. The second half is the crying-wolf guard: an ordinary index
    install must keep getting its command, or the fix would just be to withhold
    the command from everybody.
    """
    clobbered = clobbered_site_packages_core(DERIVED)
    assert doctor._editable_of(clobbered).state == UNKNOWN
    assert doctor._editable_of(clobbered).safe_to_reinstall is False

    env.facts = {CORE: clobbered, WRAPPER: wrapper_pinning(OTHER),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run("--fix")
    out = capsys.readouterr().out
    assert rc == 1
    assert env.install.calls == [], "formed a pip command over an unclassified install"
    assert "--force-reinstall" not in out
    assert "cannot tell whether this is an EDITABLE" in out

    intact = index_core(DERIVED)   # the same facts, plus a locatable module
    assert intact.direct_url_raw is None and clobbered.direct_url_raw is None
    assert doctor._editable_of(intact).state == NOT_EDITABLE
    env.facts = {CORE: intact, WRAPPER: wrapper_pinning(OTHER),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run("--fix")
    out = capsys.readouterr().out
    assert rc == 1
    assert env.install.formatted, "withheld the command from a plain index install too"
    assert f'-m pip install "{CORE}=={OTHER}"' in out


def test_a_venv_inside_the_repo_is_not_mistaken_for_an_editable_install():
    """The git-worktree signal on its own would call every install in a .venv/
    checked out inside a project editable. It only counts for files that are
    NOT under site-packages, and that has to stay true."""
    inside = DistFacts(CORE, True, DERIVED, (), None, direct_url_raw=None,
                       metadata_dir="/project/.venv/lib/python3.12/site-packages/"
                                    "invisible_core-18.0.0.dist-info",
                       module_path="/project/.venv/lib/python3.12/site-packages/"
                                   "invisible_core",
                       git_worktree="/project")   # as if the guard had not applied
    assert doctor._editable_of(inside).state == NOT_EDITABLE
    assert doctor._under_site_packages(Path(inside.module_path)) is True


def test_an_unknown_origin_is_labelled_in_the_report(env, capsys):
    env.facts = {CORE: truncated_direct_url_core(DERIVED),
                 WRAPPER: wrapper_pinning(DERIVED),
                 MANAGER: DistFacts(MANAGER, False)}
    run()
    out = capsys.readouterr().out
    assert "origin UNKNOWN, treated as editable" in out


# ─────────────────────────────────────────────────── the Python package arm


def test_fix_reports_a_python_package_mismatch_and_never_installs_it(env, capsys):
    env.facts = {CORE: index_core(DERIVED),
                 WRAPPER: wrapper_pinning(OTHER),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run("--fix")
    out = capsys.readouterr().out

    assert env.install.executed == []
    assert rc == 1
    assert "the Python packages are NOT repaired by doctor" in out
    assert f'-m pip install "{CORE}=={OTHER}"' in out
    assert "--force-reinstall" not in out, "the install record agrees, so plain install works"


def test_a_stale_install_record_is_told_to_force_reinstall(env, capsys):
    """The record says what the pin wants while the files say otherwise, so pip
    believes the pin is already satisfied and a plain install is a no-op. A
    different command, and it is still only printed."""
    env.facts = {CORE: index_core(OTHER),        # record agrees with the pin
                 WRAPPER: wrapper_pinning(OTHER),  # files derive DERIVED, not OTHER
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run("--fix")
    out = capsys.readouterr().out

    assert env.install.executed == []
    assert env.install.formatted == [[Path(doctor.sys.executable).name, "-m", "pip",
                                      "install", "--force-reinstall",
                                      f"{CORE}=={OTHER}"]]
    assert rc == 1
    assert "STALE RECORD" in out
    assert f'-m pip install --force-reinstall "{CORE}=={OTHER}"' in out
    assert "A plain install is a no-op here" in out


# ───────────────────── "could not check" is a third verdict, not a pass


def unpinned_wrapper() -> DistFacts:
    """A bare direct reference: it carries no version specifier at all, so
    there is nothing for the doctor to compare against the derived version."""
    return DistFacts(WRAPPER, True, "0.3.5",
                     (f"{CORE}@ git+https://example.invalid/x.git",
                      "playwright>=1.55,<=1.61.0"))


def test_a_consumer_with_no_exact_pin_is_unverified_and_never_reported_as_ok(
        env, capsys):
    """The false green this exit code exists for.

    Before, this printed "not checkable", skipped the consumer without recording
    anything, and then ended on `verdict: OK` with exit 0 - an environment the
    doctor had explicitly failed to check, handed back as an environment it had
    checked and found healthy. A CI step gating on `python -m invisible_core
    doctor` passed on a wrapper whose core pin nobody had read.
    """
    env.facts = {CORE: index_core(DERIVED),
                 WRAPPER: unpinned_wrapper(),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run()
    out = capsys.readouterr().out

    assert rc == doctor.EXIT_UNVERIFIED == 2
    assert "verdict   : OK" not in out, "an unchecked pin was reported as verified"
    assert "could NOT be verified" in out
    assert "NOT CHECKABLE" in out
    # The reason, named, so the user can act on it.
    assert "no == version" in out or "declares no" in out
    assert WRAPPER in out.split("could NOT be verified")[1]


def test_an_unverified_pin_is_not_repaired_and_still_exits_two_under_fix(env, capsys):
    """--fix must not launder it into "nothing to repair" and exit 0."""
    env.facts = {CORE: index_core(DERIVED),
                 WRAPPER: unpinned_wrapper(),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run("--fix")
    out = capsys.readouterr().out

    assert rc == 2
    assert env.ensure.calls == [] and env.clear.calls == [] and env.install.calls == []
    assert "nothing to repair." not in out
    assert "could NOT be verified" in out


def test_a_repaired_engine_does_not_clear_an_unverified_pin(env, capsys):
    """The engine problem is fixed, so the problem count falls to zero. The pin
    still was not read, and 0 would say it was."""
    env.facts = {CORE: index_core(DERIVED),
                 WRAPPER: unpinned_wrapper(),
                 MANAGER: DistFacts(MANAGER, False)}
    env.engine = [(False, "not downloaded"), (True, "firefox-18 (Firefox 151.0)")]
    rc = run("--fix")
    out = capsys.readouterr().out

    assert len(env.ensure.calls) == 1
    assert "engine repaired" in out
    assert rc == 2, "a successful engine repair reported an unread pin as verified"


def test_a_real_problem_outranks_an_unverified_check(env, capsys):
    """Both at once: the manager mismatches, the wrapper cannot be read. The
    exit code is the PROBLEM's, because that is the finding that was actually
    established, and both are printed."""
    env.facts = {CORE: index_core(DERIVED),
                 WRAPPER: unpinned_wrapper(),
                 MANAGER: DistFacts(MANAGER, True, "0.2.0", (f"{CORE}=={OTHER}",))}
    rc = run()
    out = capsys.readouterr().out

    assert rc == 1
    assert "MISMATCH" in out and "NOT CHECKABLE" in out
    assert "1 problem: the Python packages" in out
    assert WRAPPER in out


def test_a_declaration_that_disagrees_with_itself_is_unverified_not_a_pass(env, capsys):
    """Two invisible-core pins in one Requires-Dist. Picking the first would be
    a coin toss reported as a fact."""
    env.facts = {CORE: index_core(DERIVED),
                 WRAPPER: DistFacts(WRAPPER, True, "0.3.5",
                                    (f"{CORE}=={DERIVED}", f"{CORE}=={OTHER}")),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run()
    out = capsys.readouterr().out

    assert rc == 2
    assert "ambiguous" in out
    assert "verdict   : OK" not in out


# ───────────────────────────────────────────────────────────── the engine arm


def test_fix_repairs_a_mismatched_engine_by_calling_ensure_binary(env, capsys):
    env.engine = [(False, "cached tree is firefox-14, the seal is firefox-18"),
                  (True, "firefox-18 (Firefox 151.0)")]
    rc = run("--fix")
    out = capsys.readouterr().out

    assert len(env.ensure.calls) == 1, "ensure_binary was not the repair path"
    assert env.ensure.calls[0][1]["seal"] is env.seal
    assert env.clear.calls == [(("firefox-18",), {})], "cleared the wrong scope"
    assert env.install.calls == []
    assert rc == 0
    assert "engine repaired" in out


def test_doctor_without_fix_repairs_nothing(env, capsys):
    env.engine = [(False, "not downloaded")]
    rc = run()
    out = capsys.readouterr().out

    assert env.ensure.calls == [] and env.clear.calls == []
    assert rc == 1, "a diagnostic that always exits 0 cannot gate anything"
    assert "run with --fix" in out


def test_fix_does_not_loop_when_the_repair_does_not_resolve_the_problem(env, capsys):
    """ensure_binary returns, but the engine still does not verify. One attempt,
    then report. A retry loop would hide the very thing doctor was asked for."""
    env.engine = [(False, "build id mismatch")]  # sticky: never improves
    rc = run("--fix")
    out = capsys.readouterr().out

    assert len(env.ensure.calls) == 1, "doctor retried a failed repair"
    assert rc == 1
    assert "Not retrying" in out


def test_fix_does_not_retry_when_the_repair_raises(env, capsys):
    env.engine = [(False, "not downloaded")]
    env.ensure = Recorder(raises=RuntimeError("payload mismatch for the sealed asset"))
    rc = run("--fix")
    cap = capsys.readouterr()

    assert len(env.ensure.calls) == 1
    assert rc == 1
    assert "payload mismatch" in cap.err
    assert "Not retrying" in cap.out


def test_fix_refuses_a_local_seal_instead_of_downloading_nothing(env, capsys):
    class LocalSeal(FakeSeal):
        is_local = True
        origin = "INVISIBLE_SEAL_FILE=local.seal.json"

    env.seal = LocalSeal()
    env.engine = [(False, "not downloaded")]
    rc = run("--fix")
    out = capsys.readouterr().out

    assert env.ensure.calls == [] and env.clear.calls == []
    assert rc == 1
    assert "LOCAL seal" in out


# ───────────────────────────────────────────────────────────── healthy case


def test_a_coherent_environment_exits_zero_and_fixes_nothing(env, capsys):
    rc = run("--fix")
    out = capsys.readouterr().out

    assert env.ensure.calls == [] and env.clear.calls == [] and env.install.calls == []
    assert rc == 0
    assert "nothing to repair" in out
    assert "verdict   : OK" in out


def test_the_existing_report_survives_the_new_command(env, capsys, tmp_path):
    """--fix is additive: the seal / engine / cache-root / per-tree report the
    doctor produced before is still produced, and a foreign tag in the cache is
    still reported without being treated as a defect to repair."""
    class Ident:
        version = "150.0.1"
        build_id = "20260624073725"
        marked_entries = 4
        juggler_layout = "omni.ja"
        entry = tmp_path / "firefox.exe"

    d = tmp_path / "cache" / "firefox-14_150.0.1_20260624073725"
    env.trees = [(d, Ident())]
    env.problems_of = lambda i, s: ["version 150.0.1, the seal says 151.0"]
    rc = run()
    out = capsys.readouterr().out

    assert "seal      : firefox-18" in out
    assert "cache root:" in out
    assert "firefox-14_150.0.1_20260624073725: Firefox 150.0.1 build 20260624073725" in out
    assert "-> MISMATCH" in out
    assert "version 150.0.1, the seal says 151.0" in out
    assert rc == 0, "a foreign tag is another release's cache, not a defect"


# ──────────────────────────────────────────────────────── the metadata readers


def test_the_doctor_carries_no_second_copy_of_the_pin_parser():
    """One parser, or two verdicts on the same metadata.

    The doctor used to keep its own `_PIN_RE` + `_core_pin_of` beside
    invisible_core._pin's. They disagreed in two measured ways - the local copy
    dropped every requirement carrying an environment marker and did not
    understand the parenthesised dialect - and both disagreements read as "no
    pin declared", which was then reported as healthy. The two tests below show
    the behaviour; this one shows the copy is gone, so it cannot drift again.
    """
    assert not hasattr(doctor, "_core_pin_of")
    assert not hasattr(doctor, "_PIN_RE")
    assert doctor.pin_from_requirements is _pin.pin_from_requirements
    assert doctor.CORE_DIST == _pin.CORE_NAME

    # And it cannot be written back in unnoticed: any regex in this module whose
    # pattern mentions a version specifier is a requirement parser, and this
    # module is not where requirement parsing lives.
    tree = ast.parse(Path(doctor.__file__).read_text(encoding="utf-8"))
    offences = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if getattr(func, "attr", "") not in ("compile", "match", "search", "fullmatch"):
            continue
        if getattr(getattr(func, "value", None), "id", "") != "re":
            continue
        for arg in node.args[:1]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                    and "==" in arg.value:
                offences.append(f"line {node.lineno}: re.{func.attr}({arg.value!r})")
    assert offences == [], (
        "invisible_core/__main__.py parses a requirement specifier again. That "
        "parser lives in invisible_core/_pin.py, once:\n  " + "\n  ".join(offences))


def test_the_doctor_understands_the_parenthesised_dialect_of_the_pin(env, capsys):
    """`invisible-core (==18.0.0)` is what some building backends write into
    Requires-Dist, and it means exactly what `invisible-core==18.0.0` means.

    The doctor's own regex rejected it, so a wrapper pinned that way was
    reported as declaring no pin - and, before the third verdict existed, as OK.
    Here the pin disagrees with the derived version, so understanding the
    spelling is the difference between MISMATCH and a green run.
    """
    env.facts = {CORE: index_core(DERIVED),
                 WRAPPER: DistFacts(WRAPPER, True, "0.3.5",
                                    (f"{CORE} (=={OTHER})", "playwright>=1.55")),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run()
    out = capsys.readouterr().out

    assert f"declares {CORE}=={OTHER} -> MISMATCH" in out
    assert "NOT CHECKABLE" not in out
    assert rc == 1


def test_an_environment_marker_on_the_pin_line_does_not_hide_it_from_the_doctor(
        env, capsys):
    """A marker that is not an extra keeps the requirement in a default install,
    so the pin is live and must be compared. The doctor's copy skipped every
    line containing a ';', which switched the check off for exactly the lines
    scripts/sync_core_pin.py is allowed to write."""
    env.facts = {CORE: index_core(DERIVED),
                 WRAPPER: DistFacts(WRAPPER, True, "0.3.5",
                                    (f'{CORE}=={OTHER}; python_version >= "3.11"',)),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run()
    out = capsys.readouterr().out

    assert f"declares {CORE}=={OTHER} -> MISMATCH" in out
    assert rc == 1


def test_a_pin_hidden_behind_an_extra_is_still_not_the_runtime_pin(env, capsys):
    """The crying-wolf partner of the two above: `extra == "dev"` really does
    mean the requirement is absent from a default install, so it is NOT the pin,
    and the doctor must say it could not check rather than compare it."""
    env.facts = {CORE: index_core(DERIVED),
                 WRAPPER: DistFacts(WRAPPER, True, "0.3.5",
                                    (f'{CORE}=={OTHER}; extra == "dev"',
                                     "playwright>=1.55")),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run()
    out = capsys.readouterr().out

    assert "MISMATCH" not in out
    assert "NOT CHECKABLE" in out
    assert rc == 2


def test_the_underscore_spelling_is_the_same_distribution(env, capsys):
    """PEP 503 normalisation, through the doctor rather than through the parser
    in isolation."""
    env.facts = {CORE: index_core(DERIVED),
                 WRAPPER: DistFacts(WRAPPER, True, "0.3.5",
                                    (f"invisible_core=={DERIVED}",)),
                 MANAGER: DistFacts(MANAGER, False)}
    rc = run()
    out = capsys.readouterr().out

    assert f"declares {CORE}=={DERIVED} -> OK" in out
    assert rc == 0


def test_dist_facts_reads_the_real_environment_without_crashing():
    """The one test that touches the real interpreter, and it only checks that
    the reader survives whatever it finds. Every other test above feeds it
    synthetic facts on purpose."""
    facts = doctor._dist_facts(CORE)
    assert facts.name == CORE
    if facts.present:
        e = doctor._editable_of(facts)
        assert e.state in (EDITABLE, NOT_EDITABLE, UNKNOWN)
        assert e.why
