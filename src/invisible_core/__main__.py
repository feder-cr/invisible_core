"""python -m invisible_core: seal generation, cache diagnosis, and repair.

`doctor` reports. `doctor --fix` repairs, and the two things it may repair are
deliberately unequal:

  * The engine cache is ours. It is content-keyed, it can always be re-derived
    from the seal, and nothing a user wrote lives in it, so --fix clears the
    sealed tag and re-runs ensure_binary(), which already downloads, verifies
    the sha256, extracts and stamps. None of that is reimplemented here.
  * The Python environment is NOT ours. --fix never installs, upgrades or
    reinstalls a distribution, in-process or out. It prints the literal command
    and exits non-zero. On this project's own machines the packages that still
    have a checkout here (invisible-core, invisible-playwright) are editable
    installs pointing at working trees, so a --force-reinstall would replace
    live source with a copy from the index and destroy uncommitted work. That
    case is detected by name and refused by name.

Repair runs at most once. If it does not resolve the problem, doctor says so
and stops: a diagnostic that retries hides the thing it was asked to find.

THE INSTALL SEAM IS ON THE HOT PATH, NOT BESIDE IT
--------------------------------------------------
The first version of this had an INSTALL_RUNNER that nothing ever called. The
test suite replaced it with a recorder and asserted the recorder stayed empty,
which sounds like a proof and is not one: a mutation that wired
`subprocess.run(["pip", "install", "--force-reinstall", ...])` straight into
--fix left the whole suite green, because the recorder was watching a door
nobody was using.

So the seam is now the only place a pip command is allowed to EXIST. Every
remedy that mentions one calls INSTALL_RUNNER(cmd, execute=False) and prints
what it hands back; the default runner formats the command when execute is
False and raises when it is True. That makes the recorder's evidence real in
both directions - it sees each command the doctor formed, and it sees that not
one of them was ever asked to run. tests/test_doctor_fix.py adds a tripwire over
subprocess, os.system and friends for the case somebody bypasses the seam
entirely, and a source scan so the bypass cannot be written in the first place.

MISSING EVIDENCE OF AN EDITABLE INSTALL IS NOT EVIDENCE OF ABSENCE
------------------------------------------------------------------
The editable check used to read one unvalidated file, direct_url.json, and any
gap in it - a legacy `setup.py develop` install that predates PEP 610, a
truncated file, a record with no dir_info - concluded "not editable" and printed
the --force-reinstall command that overwrites the owner's working tree. It
failed in the unsafe direction three different ways.

_editable_of now answers editable / not-editable / UNKNOWN, and only
not-editable unlocks the install command. Positive evidence comes from four
independent places, so no single missing file decides anything:

  1. PEP 610 direct_url.json with dir_info.editable true;
  2. legacy residue: an .egg-link, or .egg-info metadata sitting outside
     site-packages, which is what `setup.py develop` leaves behind;
  3. an `__editable__*` .pth or finder shim in site-packages;
  4. the importable files resolving OUTSIDE site-packages, and whether that
     location sits inside a git working tree.

Absent or unparseable evidence is UNKNOWN, and UNKNOWN withholds the command
exactly like a confirmed editable does - it just says why it cannot tell.

"COULD NOT CHECK" IS ITS OWN VERDICT, WITH ITS OWN EXIT CODE
------------------------------------------------------------
doctor has three outcomes and three exit codes:

  0  every check the doctor knows how to make ran, and all of them passed;
  1  something is wrong, and it is named;
  2  a check could not be made at all.

The third one is not decoration. A consumer that declares no comparable
`invisible-core==` requirement used to be printed as "not checkable" and then
skipped, so the run still ended on `verdict: OK` and exit 0 - a state the doctor
had explicitly failed to verify, reported as a state it had verified to be fine.
Every path that cannot compare something now records it and lands on 2.

The pin is parsed by invisible_core.pin.pin_from_requirements, which is what
each consumer's import-time check goes through too. One parser, one answer: a
second copy of that regex lived here and drifted from it in two measurable ways
(it dropped any requirement carrying an environment marker, and it did not
understand the parenthesised `invisible-core (==N)` dialect a building backend
may emit). Both differences read as "no pin declared", which - before the third
verdict existed - was printed as OK.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ._pin import CORE_NAME as CORE_DIST
from ._pin import format_command
from ._pin import pin_from_requirements
from ._version import __version__ as DERIVED_CORE_VERSION
from .download import (cache_root, clear_cache, engine_status, ensure_binary,
                       iter_cached_engines)
from .seal import (JUGGLER_ENTRIES, SUPPORTED_SEAL_SCHEMA, active_seal,
                   engine_problems, read_engine_identity, resource_root)

# `invisible-firefox` stays here after its repository was deleted on
# 2026-08-18: this list drives an `importlib.metadata` lookup against
# WHATEVER IS ACTUALLY INSTALLED, not against a checkout, and the package is
# still on PyPI (13 versions, unyanked). Anyone who installed it before the
# deletion still has an environment `doctor` should be able to diagnose - a
# pin gone stale does not stop mattering because the source repo is gone.
CONSUMER_DISTS = ("invisible-playwright", "invisible-firefox")

# doctor: everything verified, nothing to repair.
EXIT_OK = 0
# doctor: something is wrong and was named.
EXIT_PROBLEM = 1
# doctor: something could NOT be verified. Distinct from both of the above on
# purpose - see the module docstring.
EXIT_UNVERIFIED = 2

# Characters that make a shell do something other than pass the token through.
# The command formatter is `_pin.format_command`. This module carried a
# byte-identical second copy of it AND of its `_NEEDS_QUOTING` set - recorded as
# open item 9 in 18-gate-inventory.md, "same class as the parser forks".
#
# Measured before removing it: the two agreed on all nine shapes tried,
# including a path with a space, an extras specifier and an embedded semicolon.
# They agreed today. Two copies of a quoting rule is a pair that will not agree
# forever, and this one decides whether the remedy a human pastes back is a
# command that runs or one that loses half a path.
_format_command = format_command


def _refuse_to_install(cmd: Sequence[str], *, execute: bool) -> str:
    """The single choke point every pip command in this module goes through.

    execute=False means "format this for a human to run", which is the only
    thing doctor ever asks for. execute=True means somebody wired an installer
    into a diagnostic, and it raises rather than running it: on this project's
    machines the packages with a checkout here are editable installs pointing
    at live working trees, so an install here can destroy uncommitted work.

    The seam is deliberately used for FORMATTING as well as for running. If it
    only fired on execution it would be dead code in normal operation, and dead
    code is exactly what let the earlier version of this file be mutated into an
    installer with the tests still green.
    """
    if execute:
        raise RuntimeError(
            "invisible-core doctor must never install a Python package, and it just "
            "tried to run: " + _format_command(cmd))
    return _format_command(cmd)


INSTALL_RUNNER = _refuse_to_install


def _offer_install(cmd: Sequence[str]) -> str:
    """Turn a pip command into text for the user. Never runs it.

    Every install command in this module is built here and nowhere else, so
    "the doctor made no install call" is a fact about a code path that is
    actually exercised on every mismatch, rather than about an unused hook.
    """
    return INSTALL_RUNNER(list(cmd), execute=False)


# ─────────────────────────────────────────────────────────────────────────
#  The Python side: what is installed, what is declared, what is editable
# ─────────────────────────────────────────────────────────────────────────

from ._env import (
    DistFacts,
    EDITABLE,
    Editability,
    NOT_EDITABLE,
    UNKNOWN,
    _dist_facts,
    _display_path,
    _editable_of,
    _git_worktree_above,
    _locate_module,
    _pep610_verdict,
    _under_site_packages,
)


@dataclass(frozen=True)
class PinProblem:
    consumer: str
    want: str          # the pin the consumer declares
    have: str          # what the core's own files derive, i.e. what will run
    recorded: str      # what the install record claims
    editability: Editability


@dataclass(frozen=True)
class PinUnverified:
    """A consumer whose pin the doctor could NOT compare.

    This is the third outcome, and it is not a problem and not a pass. A
    consumer that declares no `invisible-core==`, declares it only behind an
    extra, declares two disagreeing ones, or whose metadata is unreadable,
    leaves the doctor with nothing to compare - and "I could not check" printed
    as `verdict: OK` is the exact false green this whole check exists to remove.
    """
    consumer: str
    status: str        # the PinDeclaration status: no_pin / ambiguous / no_record
    detail: str


def _package_section(facts_of=None
                     ) -> Tuple[List[str], List[PinProblem], List[PinUnverified]]:
    """Render the `packages` block, and return what was wrong and what was
    unverifiable, as two separate lists.

    Compares the DERIVED core version (recomputed from seal.json by the code
    that will actually execute) against the pin each installed consumer
    declares. The install record is read too, but only to classify the failure:
    a record that agrees with the pin while the files do not is a partial
    install, and that one needs --force-reinstall where the plain case does not.

    The pin itself is parsed by invisible_core.pin.pin_from_requirements, the
    same call the consumers' own import-time check goes through. This module
    used to carry a second, weaker copy: it dropped every requirement carrying
    an environment marker and did not understand the parenthesised
    `invisible-core (==N)` dialect that a building backend may emit, so both
    spellings fell through to "declares no pin" - which, before the third
    outcome existed, was then reported as OK. Two parsers means two verdicts on
    the same metadata; there is now one, fed from the facts gathered here.
    """
    facts_of = facts_of or _dist_facts
    core = facts_of(CORE_DIST)
    editability = _editable_of(core)
    recorded = core.version if core.present else ""

    lines: List[str] = []
    origin = ""
    if editability.state == EDITABLE:
        origin = f"  [editable {editability.path}]"
    elif editability.state == UNKNOWN:
        origin = "  [origin UNKNOWN, treated as editable]"
    elif core.present and core.direct_url:
        origin = f"  [{core.direct_url.get('url', 'local')}]"
    elif core.present:
        origin = "  [index]"
    lines.append(f"  {CORE_DIST:<21}{DERIVED_CORE_VERSION:<9}(derived from seal.json){origin}")
    if not core.present:
        lines.append(f"  {'':<21}no install record for this distribution "
                     f"(running from a source checkout)")
    elif recorded != DERIVED_CORE_VERSION:
        lines.append(f"  {'':<21}install record says {recorded}  (STALE RECORD: pip and "
                     f"pip check read this, the files are what runs)")

    problems: List[PinProblem] = []
    unverified: List[PinUnverified] = []
    for name in CONSUMER_DISTS:
        f = facts_of(name)
        if not f.present:
            # Absent is not unverified: there is no claim to check, and the
            # doctor knows that for a fact. Only a consumer that IS here and
            # cannot be compared is a hole in the diagnosis.
            lines.append(f"  {name:<21}not installed")
            continue
        decl = pin_from_requirements(f.requires, dist_name=name)
        if decl.status != "pinned" or not decl.version:
            detail = decl.detail or f"the declaration is {decl.status}"
            lines.append(f"  {name:<21}{f.version:<9}NOT CHECKABLE "
                         f"({decl.status}): no {CORE_DIST}== version to compare")
            lines.append(f"  {'':<21}{'':<9}{detail}")
            unverified.append(PinUnverified(name, decl.status, detail))
            continue
        pin = decl.version
        ok = pin == DERIVED_CORE_VERSION
        lines.append(f"  {name:<21}{f.version:<9}declares {CORE_DIST}=={pin} "
                     f"-> {'OK' if ok else 'MISMATCH'}")
        if not ok:
            problems.append(PinProblem(
                consumer=name, want=pin, have=DERIVED_CORE_VERSION, recorded=recorded,
                editability=editability))
    return lines, problems, unverified


def _pin_remedy(p: PinProblem) -> List[str]:
    """What to print for one pin problem.

    The install command is withheld unless the core is POSITIVELY known not to
    be an editable install. Running it against an editable one replaces the
    owner's working tree with a copy from the index, and the ways this check
    gets fooled all look like missing evidence rather than like a wrong answer,
    so missing evidence withholds the command too.
    """
    e = p.editability
    out = [
        f"  {p.consumer} requires {CORE_DIST}=={p.want}",
        f"  the files on disk are {CORE_DIST} {p.have}  (invisible_core/seal.json)",
    ]
    if p.recorded and p.recorded != p.have:
        out.append(f"  the install record says {CORE_DIST} {p.recorded}  "
                   f"(so pip and pip check call this environment healthy)")
    if not e.safe_to_reinstall:
        if e.state == EDITABLE:
            out.append(f"  {CORE_DIST} is an EDITABLE install at "
                       f"{e.path or 'an unrecorded path'}")
            for line in e.evidence:
                out.append(f"    evidence: {line}")
        else:
            out.append(f"  {CORE_DIST}: cannot tell whether this is an EDITABLE install.")
            out.append(f"    {e.why}")
            out.append("    Treating it as editable. Missing evidence is not evidence of")
            out.append("    an index install, and guessing wrong here destroys a tree.")
        out += [
            "  Not offering a reinstall and not running one: it would replace that",
            "  working tree with a copy from the index, uncommitted work included.",
            "  Fix the declaration instead, in the consumer's pyproject.toml:",
            f"    {CORE_DIST}=={p.want}   ->   {CORE_DIST}=={p.have}",
            "  or roll the core's seal back to the release that consumer was built against.",
        ]
        return out
    force = ["--force-reinstall"] if p.recorded == p.want else []
    if force:
        out.append("  A plain install is a no-op here: pip believes the pinned version is "
                   "already present.")
    # The ONLY place in this module where a pip command comes into existence.
    cmd = [Path(sys.executable).name, "-m", "pip", "install", *force,
           f"{CORE_DIST}=={p.want}"]
    out += [
        "  doctor does not install Python packages. Run this yourself:",
        "    " + _offer_install(cmd),
    ]
    out += _interpreter_notes()
    return out


def _interpreter_notes() -> List[str]:
    """Facts about the interpreter that command would land in. Advisory: the
    doctor is not the thing running it."""
    notes = []
    if sys.prefix == sys.base_prefix:
        notes.append(f"  note: {sys.executable} is not a virtual environment.")
    try:
        import sysconfig
        if (Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").exists():
            notes.append("  note: this Python is marked externally managed (PEP 668); "
                         "use a virtual environment.")
    except Exception:
        pass
    return notes


# ─────────────────────────────────────────────────────────────────────────
#  Repair
# ─────────────────────────────────────────────────────────────────────────

def _repair_engine(seal) -> int:
    """Clear the sealed tag and re-download it. ONE attempt, then report.

    Only the tree the seal names is touched. Other tags in the cache belong to
    other releases: they read MISMATCH against this seal by definition and are
    not a defect to repair.
    """
    if seal.is_local:
        print("  the active seal is a LOCAL seal with no published assets, so there is "
              "nothing to download.")
        print(f"  point binary_path= / INVPW_BINARY_PATH at the tree {seal.origin} was "
              f"generated from.")
        return 1
    print(f"  clearing the cached tree for {seal.tag}")
    for d in clear_cache(seal.tag):
        print(f"    removed {d}")
    try:
        entry = ensure_binary(seal=seal, status=lambda p: print(f"    {p}..."))
    except Exception as e:
        print(f"  repair FAILED: {e}", file=sys.stderr)
        print("  Not retrying: a second identical attempt would fail identically.")
        return 1
    ok, detail = engine_status(seal)
    if not ok:
        print(f"  the repair ran but the engine still reports: {detail}")
        print("  Not retrying. Report this with the lines above.")
        return 1
    print(f"  engine repaired: {entry}")
    return 0


# ─────────────────────────────────────────────────────────────────────────
#  Commands
# ─────────────────────────────────────────────────────────────────────────

def _cmd_seal(a) -> int:
    entry = Path(a.binary)
    ident = read_engine_identity(entry)
    if not ident.version or not ident.build_id:
        print(f"error: no readable application.ini next to {entry}", file=sys.stderr)
        return 1
    seal = active_seal()
    out = {
        # One tree, one build, so the top-level build_id IS the whole truth here.
        # A release seal has no top-level value: its five legs are five builds.
        "comment": "LOCAL seal generated from an unpacked tree. Not a release.",
        "schema": SUPPORTED_SEAL_SCHEMA, "tag": a.tag, "upstream_version": ident.version,
        "build_id": ident.build_id, "source_commit": "",
        "playwright": {"min": seal.playwright_min, "max": seal.playwright_max},
        "assets": {},
    }
    blob = json.dumps(out, sort_keys=True, separators=(",", ":")) + "\n"
    Path(a.out).write_text(blob, encoding="utf-8")
    print(f"wrote {a.out}: {a.tag} Firefox {ident.version} build {ident.build_id}")
    print(f"use it with: INVISIBLE_SEAL_FILE={a.out}")
    return 0


def _cmd_doctor(a) -> int:
    seal = active_seal()
    print(f"seal      : {seal.describe()}  [{seal.origin}]")
    ok, detail = engine_status(seal)
    print(f"engine    : {'OK' if ok else 'PROBLEM'} - {detail}")
    print(f"cache root: {cache_root()}")
    for d, ident in iter_cached_engines():
        if ident is None:
            print(f"  {d.name}: no readable engine (incomplete tree?)")
            continue
        probs = engine_problems(ident, seal)
        verdict = "COHERENT" if not probs else "MISMATCH"
        print(f"  {d.name}: Firefox {ident.version} build {ident.build_id} "
              f"juggler {ident.marked_entries}/{len(JUGGLER_ENTRIES)} "
              f"({ident.juggler_layout}) -> {verdict}")
        for p in probs:
            print(f"      {p}")
        if a.deep and not probs:
            from .download import _sha256_file
            omni = resource_root(d / ident.entry.name) / "omni.ja"
            try:
                asset = seal.asset_for(sys.platform, __import__("platform").machine())
            except NotImplementedError:
                asset = None
            if asset is None:
                print("      omni.ja NOT CHECKED (the seal has no asset for this platform)")
            elif not asset.omni_sha256:
                # An absent digest is not a passed check: say which it is.
                print("      omni.ja NOT CHECKED (this leg ships no omni.ja, so the seal has "
                      "no payload digest for it)")
            elif not omni.exists():
                print("      omni.ja MISSING (the seal records a digest for this leg)")
            else:
                got = _sha256_file(omni)
                print(f"      omni.ja {'OK' if got.lower() == asset.omni_sha256.lower() else 'CONTENT MISMATCH'}")

    print("packages  :")
    pkg_lines, pin_problems, unverified = _package_section()
    for line in pkg_lines:
        print(line)

    # Three outcomes, not two. A problem outranks an unverified check (it is a
    # thing we DID establish), and both outrank OK - but "OK" is now reserved
    # for a run where every check the doctor knows how to make actually ran.
    n_problems = (0 if ok else 1) + len(pin_problems)
    unchecked = ""
    if unverified:
        unchecked = (f"{len(unverified)} thing{'' if len(unverified) == 1 else 's'} "
                     f"could NOT be verified: the Python packages")
    if n_problems:
        what = []
        if not ok:
            what.append("the sealed engine")
        if pin_problems:
            what.append("the Python packages")
        summary = (f"{n_problems} {'problem' if n_problems == 1 else 'problems'}: "
                   + ", ".join(what))
        # Both at once: the problem sets the exit code, but the unread check
        # still has to appear on the verdict line, or a reader who stops there
        # takes "1 problem" to mean everything else was verified.
        if unchecked:
            summary += f"; {unchecked}"
        state = "problem"
    elif unverified:
        summary = unchecked
        state = "unverified"
    else:
        summary = "OK"
        state = "ok"
    print(f"verdict   : {summary}")

    for u in unverified:
        print(f"  {u.consumer}: {u.detail}")
    if unverified:
        print("  A pin that cannot be read is NOT a pin that holds. doctor reports it as")
        print("  unverified, and exits 2 when that is the worst finding of the run:")
        print("  printing OK over a check that never ran is the failure this removes.")
        print(f"  Fix it by declaring an exact {CORE_DIST}== requirement in that")
        print("  consumer's pyproject.toml, then reinstalling it.")

    if not a.fix:
        if state == "problem":
            print("run with --fix to repair what can be repaired "
                  "(the engine cache; never the Python environment).")
        elif state == "unverified":
            print("--fix cannot help here: doctor does not repair what it could not "
                  "verify, and it never edits a Python environment.")
        return {"ok": EXIT_OK, "problem": EXIT_PROBLEM,
                "unverified": EXIT_UNVERIFIED}[state]

    if state == "ok":
        print("fix       : nothing to repair.")
        return EXIT_OK

    rc = EXIT_OK
    if pin_problems:
        print("fix       : the Python packages are NOT repaired by doctor.")
        for p in pin_problems:
            for line in _pin_remedy(p):
                print(line)
        rc = EXIT_PROBLEM
    if not ok:
        print("fix       : repairing the sealed engine")
        rc = _repair_engine(seal) or rc
    if rc == EXIT_OK and unverified:
        # Everything that WAS a problem got repaired, but a check still did not
        # run. Exiting 0 here would hand back the false green through the fix
        # path instead of the report path.
        print("fix       : nothing above repairs an unverifiable pin.")
        rc = EXIT_UNVERIFIED
    return rc


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m invisible_core")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seal", help="generate a local seal from an unpacked build")
    s.add_argument("--binary", required=True)
    s.add_argument("--tag", default="local")
    s.add_argument("-o", "--out", default="local.seal.json")
    s.set_defaults(fn=_cmd_seal)
    d = sub.add_parser(
        "doctor",
        help="report every cached engine and the invisible-core pin against the seal "
             "(exit 1 if anything is incoherent, exit 2 if a check could not be "
             "made at all)")
    d.add_argument("--deep", action="store_true", help="also hash omni.ja")
    d.add_argument("--fix", action="store_true",
                   help="re-download a broken sealed engine. Never installs, upgrades "
                        "or reinstalls a Python package: those are printed as commands "
                        "for you to run.")
    d.set_defaults(fn=_cmd_doctor)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
