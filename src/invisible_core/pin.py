"""The core-pin comparison and the runtime repair, owned by the core and shared
by every consumer.

WHY IT LIVES HERE. Two distributions consume this package (the Playwright
wrapper and the profile manager) and both ask the same question: is the
invisible-core that is about to execute the one my own metadata declares? Two
copies of that comparison drift, and a diagnosis that differs between the two
products is worse than no diagnosis at all. One comparison, one message set, one
requirement parser, parameterised by the asking distribution's name.

WHY IT EXISTS AT RUNTIME AT ALL. Once a consumer's [project].dependencies
carries an exact `invisible-core==N.M.0`, pip refuses the obvious ways to get
this wrong and `pip check` reports the rest. Two paths still slip through, both
measured:

  * `pip install --no-deps <consumer>` and lockfile installers that resolve each
    distribution separately. Nothing at install time leaves a trace, and nobody
    runs `pip check` inside a built container.
  * a partial install: the files under site-packages/invisible_core were
    replaced but the .dist-info was not (a clobbering copy, a hand-patched image
    layer). pip and `pip check` both read the record, so both call that
    environment healthy. The files are what actually runs.

WHAT IS COMPARED, and why this pair rather than the obvious one.
  WANT     the `==` version parsed back out of the CONSUMER's metadata, i.e.
           out of its pyproject. One source, no constant in code to drift.
  HAVE     invisible_core._version.__version__, recomputed from the packaged
           seal.json on every import. This is the truth of the code that will
           execute, and it is read by importing this package's own sibling
           module, never through importlib.metadata.
  RECORDED importlib.metadata.version("invisible-core"), the installer's
           record. Diagnostics ONLY. It never triggers on its own: asserting on
           it gives a false green on both cases above, and a false red on any
           editable checkout whose dist-info was frozen at install time.

ONE PARSER. `invisible-core==X` is spelled several legal ways in a Requires-Dist
line, and every place that reads one used to carry its own regex with its own
acceptance set. That is this project's original bug in miniature: N copies of one
thing that must agree, and the weakest copy decides. parse_requirement() below is
the only implementation. pin_from_requirements() is the only place that decides
which requirement is the pin, and it takes the requirement strings from whatever
source the caller has (importlib.metadata, a doctor's own facts object, a
pyproject's [project].dependencies), so nothing has a reason to write a second
one.

WHAT THIS MODULE DOES AT IMPORT TIME, AND WHY IT IS ALLOWED TO. enforce_core_pin()
does not just report a mismatch, it repairs it: it installs the declared version
and picks it up in-process, so the user's script keeps running. The window that
makes that sound is narrow and the guards below are what keep it narrow:

  * it runs from the FIRST line of a consumer's package __init__, at which
    instant invisible_core has not been imported by anybody else. The consumer
    captures that fact before importing us and passes it in as
    core_preimported; when it is true the repair is refused, because swapping
    the core underneath objects somebody already holds is not a reload, it is
    two versions alive at once.
  * one attempt per environment, enforced by a module flag AND an env var, so a
    re-exec cannot turn a failing repair into a loop.
  * every failure degrades to the message that was printed before this existed.
    A missing network, a read-only site-packages or a locked-down image gets a
    remedy to run, never a traceback and never a retry.
  * it says what it is about to do before it does it, and what it did after. A
    package that silently mutates the environment it was imported into is worse
    than one that asks.

An EDITABLE core is repaired too, on purpose, and that is the one case where the
repair costs the user something: installing over `pip install -e` detaches the
environment from their working tree. The tree survives, but imports stop coming
from it. So the path is captured BEFORE the install and the exact command that
reattaches it is printed afterwards, prominently.

WHAT THIS MODULE CANNOT SEE, and who owns that. By the time anything in here
runs, invisible_core has already been imported successfully - this module is
part of it. So "the core is not installed", "the core is installed but its own
dependencies are missing" and "the core is installed but so damaged it cannot
derive its own version" are not states this module can ever report on. They
belong to the import floor in each consumer's package __init__, which catches
the failure of `from invisible_core._pin import ...` and classifies it. That
split is deliberate; it is why pin_problem() no longer takes a parameter
describing the health of the core, and why there is no probe of it here.

"NOT CHECKABLE" IS NOT "HEALTHY". A consumer that declares no `invisible-core==`
requirement, or whose install record is missing entirely, leaves nothing to
enforce. That is a graceful skip, not a pass: pin_report() returns
verdict="not-checkable" with the reason, exactly as `python -m invisible_core
doctor` prints it. Only verdict="violated" raises or repairs.

It never checks the seal DIGEST - the consumer has no independent expectation of
the core's bytes without hardcoding one, which is a second source of truth and
the original bug again. Content belongs to the publish gate, where the previous
release's digest is available to compare against.

Keep this module's imports to the standard library, and keep the two sibling
imports it needs inside the functions that need them. If it ever grows a
module-level import of anything heavier, a core that is broken in some other way
produces a traceback about an internal module instead of the remedy.
"""
from __future__ import annotations

import os
import re
import sys
import warnings
from importlib.metadata import (
    PackageNotFoundError,
    distribution as _distribution,
    requires as _requires,
    version as _pkg_version,
)
from typing import Iterable, NamedTuple, Optional, Sequence

CORE_NAME = "invisible-core"

#: Env escape hatch, mirroring INVISIBLE_PLAYWRIGHT_SKEW in the wrapper. When it
#: is "allow" a mismatch warns instead of raising, and nothing is installed.
SKIP_ENV = "INVISIBLE_CORE_PIN"

#: Kill switch for the runtime repair. Set it to off/0/no/false and a mismatch
#: goes back to being reported with the command to run. Needed by any test that
#: builds a deliberately broken environment in a subprocess, and by anyone who
#: does not want a library touching site-packages.
AUTOFIX_ENV = "INVISIBLE_CORE_AUTOFIX"

#: Set by us, to ourselves, immediately BEFORE an attempt. Children inherit it,
#: so a repaired process that re-execs something cannot start the loop again.
AUTOFIX_ATTEMPTED_ENV = "INVISIBLE_CORE_AUTOFIX_ATTEMPTED"

#: How long the repair's pip is allowed to take before it is stopped. The repair
#: runs from the first line of a consumer's __init__ with output captured, so an
#: unbounded pip means an import that hangs with nothing on screen. Generous
#: enough for a slow link (the core is pure Python but pulls a handful of deps),
#: bounded so the failure is a message rather than a silence.
INSTALL_TIMEOUT_ENV = "INVISIBLE_CORE_AUTOFIX_TIMEOUT"
_INSTALL_TIMEOUT_DEFAULT = 300.0


def _install_timeout() -> float:
    """Seconds, from the env var when it parses to a positive number.

    A malformed or non-positive value falls back to the default rather than
    raising or disabling the bound: this is read on the import path, and there
    is no sensible reason for a typo in an env var to break someone's import,
    nor to silently turn the timeout off.
    """
    raw = os.environ.get(INSTALL_TIMEOUT_ENV, "").strip()
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _INSTALL_TIMEOUT_DEFAULT


# ─────────────────────────────────────────────────────────────────────────────
#  The one requirement parser
# ─────────────────────────────────────────────────────────────────────────────
#
# The requirement strings are authored by us, so a regex plus PEP 503 name
# normalisation is enough and `packaging` (not importable in a bare venv holding
# only our wheels) is not needed. What it has to accept is not a matter of
# taste: importlib.metadata hands back the Requires-Dist line as the building
# backend wrote it, and all of these are legal spellings of the same pin.
#
#     invisible-core==N.M.0
#     invisible-core (==N.M.0)                    <- the parenthesised dialect
#     invisible_core == N.M.0                     <- PEP 503, and spacing
#     invisible-core[extra]==N.M.0
#     invisible-core==N.M.0; python_version >= "3.11"
#
# The parenthesised one used to fall through as "no pin declared", which was then
# reported as healthy, and a copy of this parser elsewhere dropped every line
# carrying a semicolon, which switched the whole check off the day a marker was
# added. A spelling this parser does not understand must never read as "the pin
# holds"; see pin_from_requirements().
_REQ_RE = re.compile(
    r"""^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*
         (?:\[(?P<extras>[^\]]*)\])?\s*
         (?P<rest>.*)$""",
    re.VERBOSE | re.DOTALL,
)
_PINNED_RE = re.compile(r"^==(?P<version>[^\s,;()\[\]]+)$")
# `extra == "dev"` means the requirement is not installed unless that extra was
# asked for, so it cannot be the pin the default install resolved. Every OTHER
# environment marker (python_version, sys_platform, ...) is kept: the pin line is
# allowed to carry one and scripts/sync_core_pin.py preserves it on purpose.
_EXTRA_MARKER_RE = re.compile(r"\bextra\s*==")


class Requirement(NamedTuple):
    """One requirement string, reduced to what it MEANS.

    name       PEP 503 normalised, so invisible_core, Invisible.Core and
               invisible-core are one name.
    extras     normalised and sorted, so [b,a] and [A, B] are one extras set.
    specifier  all whitespace removed and one layer of the parenthesised
               dialect stripped, so `(== 1.2.3)` and `==1.2.3` are one
               specifier. Empty for a bare requirement.
    marker     the environment marker, whitespace-collapsed. Empty when absent.
    pinned     the version when the specifier is exactly one `==`, else None.
               None is "declares no comparable version", which covers a bare
               name, a range, and a direct reference (`name @ url`).
    """
    raw: str
    name: str
    extras: tuple
    specifier: str
    marker: str
    pinned: Optional[str]


def normalise_name(name: str) -> str:
    """PEP 503: invisible_core, Invisible.Core and invisible-core are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement(req: str) -> Optional[Requirement]:
    """Parse one requirement string. None when it does not even start with a
    distribution name, which is the only shape this refuses."""
    m = _REQ_RE.match(str(req))
    if m is None:
        return None
    spec_part, _, marker_part = (m.group("rest") or "").partition(";")
    specifier = re.sub(r"\s+", "", spec_part)
    if specifier.startswith("(") and specifier.endswith(")"):
        specifier = specifier[1:-1]
    raw_extras = m.group("extras") or ""
    extras = tuple(sorted(
        normalise_name(e.strip()) for e in raw_extras.split(",") if e.strip()))
    marker = " ".join(marker_part.split())
    pinned = _PINNED_RE.match(specifier)
    return Requirement(
        raw=str(req),
        name=normalise_name(m.group("name")),
        extras=extras,
        specifier=specifier,
        marker=marker,
        pinned=pinned.group("version") if pinned else None,
    )


def canonical_requirement(req: str) -> str:
    """A requirement reduced to a comparable string, so two spellings of the
    same requirement compare equal.

    A name spelled with an underscore, a space around the operator and the
    parenthesised dialect all describe the same requirement, and a gate that
    compares the raw literals refuses a push over a space and then reports that
    the pin "disagrees with the core", which is false. Anything
    parse_requirement cannot take apart falls back to whitespace
    collapsing, which can only ever make two requirements compare UNEQUAL, so a
    gate built on this keeps failing closed.
    """
    parsed = parse_requirement(req)
    if parsed is None:
        return " ".join(str(req).split())
    extras = f"[{','.join(parsed.extras)}]" if parsed.extras else ""
    marker = f"; {parsed.marker}" if parsed.marker else ""
    return f"{parsed.name}{extras}{parsed.specifier}{marker}"


class PinDeclaration(NamedTuple):
    """What the asking distribution declares about invisible-core.

    status is one of:
      "pinned"    - exactly one `invisible-core==X` requirement. version is X.
      "no_record" - the distribution has no install record (a source checkout),
                    so it never declared anything.
      "no_pin"    - installed, but no comparable requirement: either no
                    invisible-core requirement at all, or one with no `==`
                    (a bare direct reference carries no version), or one hidden
                    behind an extra.
      "ambiguous" - more than one invisible-core `==` requirement, disagreeing.
    Only "pinned" is checkable. The other three are reported as such, never as
    a pass.
    """
    version: Optional[str]
    status: str
    detail: Optional[str]


def pin_from_requirements(
    requires: Iterable[str], *, dist_name: str = "this distribution",
) -> PinDeclaration:
    """Pick the invisible-core pin out of a list of requirement strings.

    THE single implementation. Feed it importlib.metadata.requires(), a doctor's
    own DistFacts.requires, or a pyproject's [project].dependencies: the
    acceptance set is then the same everywhere by construction rather than by
    review.
    """
    found = []
    saw_core_without_pin = False
    saw_core_under_extra = False
    for raw in requires:
        parsed = parse_requirement(raw)
        if parsed is None or parsed.name != CORE_NAME:
            continue
        if _EXTRA_MARKER_RE.search(parsed.marker):
            saw_core_under_extra = True
            continue
        if parsed.pinned is None:
            saw_core_without_pin = True
            continue
        found.append(parsed.pinned)

    if not found:
        if saw_core_without_pin:
            detail = (f"{dist_name} requires {CORE_NAME} with no == version "
                      f"(a direct reference declares no version at all)")
        elif saw_core_under_extra:
            detail = (f"{dist_name} declares {CORE_NAME} only behind an extra, "
                      f"so a default install resolves nothing to compare")
        else:
            detail = f"{dist_name} declares no {CORE_NAME} requirement"
        return PinDeclaration(None, "no_pin", detail)

    distinct = sorted(set(found))
    if len(distinct) > 1:
        return PinDeclaration(
            None, "ambiguous",
            f"{dist_name} declares {len(distinct)} disagreeing {CORE_NAME} pins: "
            + ", ".join(distinct))
    return PinDeclaration(distinct[0], "pinned", None)


def pin_declaration(dist_name: str) -> PinDeclaration:
    """Parse the `invisible-core==` requirement out of dist_name's metadata."""
    try:
        reqs = _requires(dist_name) or []
    except PackageNotFoundError:
        return PinDeclaration(
            None, "no_record",
            f"{dist_name} has no install record, so it declares nothing "
            f"(running from a source checkout)")
    except Exception as e:  # unreadable metadata is not a pass either
        return PinDeclaration(None, "no_record", f"{dist_name} metadata unreadable: {e}")
    return pin_from_requirements(reqs, dist_name=dist_name)


def declared_core_pin(dist_name: str) -> Optional[str]:
    """The `invisible-core==X` version dist_name declares, or None.

    None means "nothing to enforce", NOT "healthy". Callers that need to tell
    those apart use pin_declaration() or pin_report()["verdict"].
    """
    return pin_declaration(dist_name).version


# ─────────────────────────────────────────────────────────────────────────────
#  The facts
# ─────────────────────────────────────────────────────────────────────────────

def installed_core_version() -> Optional[str]:
    """The version of the core code that will actually run, derived live.

    Read from the sibling _version module, which recomputes the version from the
    packaged seal.json (seal tag + CORE_REVISION) on every import.

    There is deliberately NO fallback to invisible_core.__version__: that
    attribute is importlib.metadata.version("invisible-core"), i.e. the
    installer's record, and falling back to it would hand back the record in the
    exact case this function exists to catch - a site-packages whose files were
    clobbered while the .dist-info stayed put. The old fallback made that
    environment report as healthy. None means "could not be determined", and it
    is rendered as such in the mismatch message rather than assumed healthy.

    The import is INSIDE the function on purpose, and it is what makes the
    in-process repair work: after repair_core() drops invisible_core.* from
    sys.modules and re-imports the package, this line resolves to the freshly
    installed _version module, so the same function object reports the new
    version without the process restarting.
    """
    try:
        from ._version import __version__ as derived
    except Exception:
        return None
    text = str(derived).strip()
    return text or None


def recorded_core_version() -> Optional[str]:
    """What the installer wrote down. Diagnostics only."""
    try:
        return _pkg_version(CORE_NAME)
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def editable_core_path() -> Optional[str]:
    """The source tree an editable core points at, or None.

    PEP 610: pip records direct_url.json for anything not installed from an
    index, and marks dir_info.editable for `pip install -e`. An install with no
    direct_url.json is by definition not editable.

    Read BEFORE any repair runs: the install replaces the .dist-info, so
    afterwards there is nothing left to read and the reattach command could not
    be printed.
    """
    try:
        raw = _distribution(CORE_NAME).read_text("direct_url.json")
    except Exception:
        return None
    if not raw:
        return None
    try:
        import json

        info = json.loads(raw)
    except Exception:
        return None
    if not info.get("dir_info", {}).get("editable"):
        return None
    url = str(info.get("url", ""))
    if url.startswith("file:///"):
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        return url2pathname(unquote(urlparse(url).path))
    return url or None


def core_modules_loaded() -> tuple:
    """Every invisible_core module currently in sys.modules, sorted.

    The repair's soundness turns on this being empty before the consumer's
    import ran: nobody can be holding an object from the version we are about to
    replace if nothing imported it yet.
    """
    return tuple(sorted(
        m for m in list(sys.modules)
        if m == "invisible_core" or m.startswith("invisible_core.")))


# The file the derived version actually comes from. Named in the messages
# because the previous wording blamed seal.json, which is only half of it: the
# version is <seal tag number>.<CORE_REVISION>.0 and CORE_REVISION is a literal
# in _version.py, so a wrong version can come from either file and the user has
# to be told which one to open.
_DERIVED_FROM = "invisible_core/_version.py, from seal.json + CORE_REVISION"

#: How a version that could not be derived at all is rendered. It is not a
#: separate branch any more: a core that cannot derive its version cannot be
#: imported either (invisible_core.__init__ imports _version), so the state
#: belongs to the consumers' import floor, and what is left here is one honest
#: rendering inside the ordinary mismatch message.
_UNDETERMINED = "an undeterminable version"


def pin_problem(
    *,
    dist_name: str,
    want: Optional[str],
    have: Optional[str],
    recorded: Optional[str] = None,
    editable_path: Optional[str] = None,
    dist_version: Optional[str] = None,
) -> Optional[str]:
    """Pure verdict. Returns the message to raise, or None when there is
    nothing to raise about.

    None covers two different situations on purpose, and the caller is expected
    to keep them apart: the pin holds, or there was no pin to check. Split out
    from pin_report so every branch is testable without building a broken
    environment - but note that the fact-gathering functions above are tested
    against real constructed site-packages layouts, not through this seam, since
    a pure verdict function proves nothing about what is fed into it.
    """
    consumer = f"{dist_name} {dist_version}" if dist_version else dist_name
    want_cmd = (f'pip install "{CORE_NAME}=={want}"' if want
                else f"pip install --force-reinstall {dist_name}")

    if not want:
        # Nothing declared: not checkable. The caller reports it as such.
        return None

    if have == want:
        return None

    shown = have or _UNDETERMINED

    if editable_path:
        return (
            f"{CORE_NAME} version mismatch, and {CORE_NAME} is an editable install.\n"
            f"  {consumer} requires  {CORE_NAME}=={want}\n"
            f"  editable source     {editable_path}\n"
            f"  that tree builds    {CORE_NAME} {shown}   ({_DERIVED_FROM})\n"
            f"Repairing this replaces the editable install with a copy from the index: "
            f"the tree above stays on disk, but imports stop coming from it and edits "
            f"in it stop having any effect. To reattach it afterwards:\n"
            f"  {format_command(['pip', 'install', '-e', str(editable_path)])}\n"
            f"To keep this pair instead, move the pin in {dist_name}'s pyproject.toml to "
            f"{shown}, or roll the core's seal back to the release {dist_name} was "
            f"built against.\n"
            f"To run this pair anyway: set {SKIP_ENV}=allow"
        )

    if recorded is not None and recorded == want:
        return (
            f"{CORE_NAME} version mismatch, and its install record disagrees with its "
            f"own files.\n"
            f"  {consumer} requires  {CORE_NAME}=={want}\n"
            f"  the files on disk say  {CORE_NAME} {shown}   ({_DERIVED_FROM})\n"
            f"  the install record says {CORE_NAME} {recorded}\n"
            f"pip and pip check read the record, so both report this environment as "
            f"healthy. The files are what actually runs, and a plain install is a "
            f"no-op here because pip believes {want} is already present.\n"
            f'Run: pip install --force-reinstall "{CORE_NAME}=={want}"'
        )

    return (
        f"{CORE_NAME} version mismatch.\n"
        f"  {consumer} requires  {CORE_NAME}=={want}\n"
        f"  installed           {CORE_NAME} {shown}   ({_DERIVED_FROM})\n"
        f"This pair is not tested together. The prefs, the spoofed User-Agent and the "
        f"protocol expectations are generated per engine release, so a core from "
        f"another release describes a browser you are not running.\n"
        f"An install that skipped dependency resolution is the usual cause: "
        f"pip install --no-deps, or a lockfile that pins the two packages separately.\n"
        f"Run: {want_cmd}"
    )


def pin_report(dist_name: str) -> dict:
    """Non-raising form. What a doctor command prints.

    verdict is the field to read, and it has three values, not two:
      "holds"         - a pin was declared and the code that will run matches it;
      "violated"      - problem carries the message;
      "not-checkable" - nothing was declared, or the declaration cannot be
                        compared. reason says which. This is NOT a pass, and
                        collapsing it into one was how a wrapper with an
                        unparsed pin reported itself healthy.
    """
    declaration = pin_declaration(dist_name)
    want = declaration.version
    have = installed_core_version()
    recorded = recorded_core_version()
    editable = editable_core_path()
    try:
        dist_version = _pkg_version(dist_name)
    except Exception:
        dist_version = None

    problem = pin_problem(
        dist_name=dist_name, want=want, have=have, recorded=recorded,
        editable_path=editable, dist_version=dist_version,
    )
    if problem is not None:
        verdict, reason = "violated", None
    elif want and have:
        verdict, reason = "holds", None
    else:
        verdict = "not-checkable"
        reason = declaration.detail or f"{CORE_NAME} derives no version"

    return {
        "dist": dist_name,
        "want": want,
        "declared_status": declaration.status,
        "have": have,
        "recorded": recorded,
        "editable": editable,
        "verdict": verdict,
        "reason": reason,
        # Kept for the CLI and for callers that only ask "should I stop?".
        # `ok` is True for "holds" AND for "not-checkable" on purpose; anything
        # reporting health to a human must read `verdict`.
        "ok": problem is None,
        "problem": problem,
    }


def assert_core_pin(dist_name: str) -> None:
    """Raise ImportError when the core that will run is not the one dist_name
    declares. A pin that cannot be checked is a silent pass here and a named
    "not checkable" in pin_report().

    This is the REPORTING form and it installs nothing. It is what the doctor
    and the tests use. The import-time form that repairs is enforce_core_pin().
    """
    report = pin_report(dist_name)
    if report["problem"] is None:
        return
    msg = report["problem"]
    if os.environ.get(SKIP_ENV) == "allow":
        warnings.warn(msg, RuntimeWarning, stacklevel=2)
        return
    raise ImportError(msg)


# ─────────────────────────────────────────────────────────────────────────────
#  The repair
# ─────────────────────────────────────────────────────────────────────────────

# Characters that make a shell do something other than pass the token through.
_NEEDS_QUOTING = set("=<>[]!;&|$`\"'*? \t")


def format_command(cmd: Sequence[str]) -> str:
    """A command list rendered so a human can paste it back."""
    return " ".join(
        f'"{token}"' if set(token) & _NEEDS_QUOTING else token for token in cmd)


class InstallOutcome(NamedTuple):
    ok: bool
    command: str
    detail: str


def _run_pip(cmd: Sequence[str], *, execute: bool) -> InstallOutcome:
    """The single choke point every pip command in this module goes through.

    The seam is deliberately used for FORMATTING as well as for running:
    execute=False renders the command and runs nothing. If it only ever fired on
    execution it would be dead in normal operation, and dead code is exactly what
    lets an installer be wired into a module whose tests are still green.

    Nothing raises out of here. A locked-down environment, a read-only
    site-packages, no network and no pip at all are all the same answer: ok is
    False and detail says why, and the caller degrades to the message.
    """
    shown = format_command(cmd)
    if not execute:
        return InstallOutcome(False, shown, "not executed")
    import subprocess

    # A timeout is not optional here. This runs from the first line of a
    # consumer's package __init__, so a pip that stalls - a hung index, a
    # contended lock, a proxy that never answers - stalls the IMPORT, and with
    # capture_output there is not a single character on screen to explain it.
    # A user's script hanging forever with no output is a worse failure than the
    # mismatch this is repairing. On expiry we degrade like any other failure:
    # the original error is raised, carrying the command to run by hand.
    try:
        completed = subprocess.run(list(cmd), capture_output=True, text=True,
                                   timeout=_install_timeout())
    except subprocess.TimeoutExpired:
        return InstallOutcome(
            False, shown,
            f"pip did not finish within {_install_timeout()}s and was stopped. "
            f"Run the command by hand, or raise {INSTALL_TIMEOUT_ENV}.")
    except Exception as e:
        return InstallOutcome(False, shown, f"{type(e).__name__}: {e}")
    if completed.returncode == 0:
        return InstallOutcome(True, shown, "pip exited 0")
    tail = " ".join(((completed.stderr or "") + " " + (completed.stdout or "")).split())
    return InstallOutcome(False, shown, f"pip exited {completed.returncode}: {tail[-400:]}")


#: The seam. Tests replace this with a recorder, which is what makes "which
#: command was formed" and "was execution requested" two separate assertions.
INSTALL_RUNNER = _run_pip


class RepairResult(NamedTuple):
    """attempted says whether the installer was reached at all, repaired says
    whether the environment is now correct IN THIS PROCESS. They are separate
    because "we ran pip and it worked" is not the claim that matters; the claim
    that matters is that the import about to happen gets the new files."""
    attempted: bool
    repaired: bool
    reason: str
    command: str
    editable_path: Optional[str]
    recovery: Optional[str]


def autofix_enabled() -> bool:
    """False only when the kill switch is set. Repair is the default."""
    return os.environ.get(AUTOFIX_ENV, "").strip().lower() not in (
        "off", "0", "no", "false")


# One attempt per process. The env var below covers a re-exec; this covers a
# second consumer imported in the same interpreter after the first one failed.
_REPAIR_ATTEMPTED = False


def _say(stream, text: str) -> None:
    try:
        print(text, file=stream)
        stream.flush()
    except Exception:
        pass


def repair_core(
    *,
    dist_name: str,
    want: Optional[str],
    core_preimported: bool,
    stream=None,
) -> RepairResult:
    """Install `want` and pick it up in this process. Never raises.

    core_preimported is not computed here on purpose: by the time any line of
    this module runs, invisible_core IS imported, so a check made here would
    always say yes and the guard would be theatre. The consumer captures it at
    the top of its own __init__, before it imports us, and passes it in.
    """
    global _REPAIR_ATTEMPTED
    out = stream if stream is not None else sys.stderr

    if not want:
        return RepairResult(False, False, "nothing is declared to install", "", None, None)

    cmd = [sys.executable, "-m", "pip", "install", "--force-reinstall",
           f"{CORE_NAME}=={want}"]
    shown = format_command(cmd)

    if not autofix_enabled():
        return RepairResult(
            False, False,
            f"the automatic repair is switched off ({AUTOFIX_ENV})", shown, None, None)

    if core_preimported:
        return RepairResult(
            False, False,
            f"{CORE_NAME} was already imported before this check ran "
            f"({len(core_modules_loaded())} of its modules are live), so replacing it "
            f"now would leave objects from two different versions alive in one process. "
            f"Import {dist_name} first, or run the command above yourself",
            shown, None, None)

    if _REPAIR_ATTEMPTED or os.environ.get(AUTOFIX_ATTEMPTED_ENV):
        return RepairResult(
            False, False,
            "an automatic repair was already attempted in this environment and is "
            "not retried",
            shown, None, None)

    # NEVER overwrite an editable install, and never guess that it is safe.
    #
    # This module runs `pip install --force-reinstall` from the first line of a
    # consumer's `__init__`, so on a developer's machine it can replace their
    # working tree with the published wheel - and it did, three times in one
    # session on 2026-07-27. Twice it corrupted a measurement rather than merely
    # being annoying: tests went red naming fixes that were present on disk, and
    # a mutation run reported SURVIVED against a gate that was sound. Nothing
    # said the package under test had been swapped.
    #
    # The check that could have stopped it already existed - a three-valued
    # detector over four independent signals - but it lived in `__main__`, which
    # is forbidden from installing anything. What guarded THIS command was one
    # file read with two possible answers. The careful check protected the
    # command that never runs.
    #
    # `safe_to_reinstall` is `state == NOT_EDITABLE`, not `state != EDITABLE`:
    # every way the old check was defeated produced an ABSENCE of evidence, and
    # that must not read as permission.
    # A probe that RAISES used to set `verdict = None`, which then failed the
    # `verdict is not None` guard below - so the reinstall PROCEEDED. The comment
    # on that line said "a broken probe is not a licence" and the code did the
    # opposite of it. Found 2026-08-01 by reading the two together.
    #
    # An absence of evidence is not permission, which is the rule `_env` was
    # written for and states in its own docstring. A probe that cannot answer is
    # the same answer as UNKNOWN: refuse.
    try:
        from ._env import _dist_facts, _editable_of

        verdict = _editable_of(_dist_facts(CORE_NAME))
    except Exception as exc:
        return RepairResult(
            False, False,
            f"could not determine whether {CORE_NAME} is an editable install "
            f"({type(exc).__name__}: {exc}), and this repair overwrites whatever "
            f"is there. Refusing rather than guessing. Run the command above "
            f"yourself, or set {AUTOFIX_ENV}=off for the session",
            shown, None, None)
    if verdict is not None and not verdict.safe_to_reinstall:
        where = f" at {verdict.path}" if verdict.path else ""
        # Hand back the exact command, with the real path, when the detector
        # found one. That was the point of the older warn-then-repair code and
        # it survives the change to refusing: the undo is handed over, not left
        # to be worked out. Through format_command because a checkout path
        # routinely contains spaces on Windows, and a recovery line that breaks
        # on those machines helps only the people who did not need it.
        remedy = (format_command(["pip", "install", "-e", verdict.path])
                  if verdict.path else f"pip install -e <path-to-{CORE_NAME}>")
        return RepairResult(
            False, False,
            f"{CORE_NAME} is an EDITABLE install{where} ({verdict.why}), so the "
            f"automatic repair would overwrite a working tree with a published "
            f"wheel. Refusing. Fix the environment yourself - usually "
            f"`{remedy}` - or set {AUTOFIX_ENV}=off for the session",
            shown, None, None)

    # Set BEFORE the attempt, both flags, so a crash, a hang killed from outside
    # or a failure that re-execs cannot come back through here.
    _REPAIR_ATTEMPTED = True
    os.environ[AUTOFIX_ATTEMPTED_ENV] = "1"

    # Read while the old .dist-info is still there. After the install it is gone.
    editable = editable_core_path()
    # Through format_command, not interpolated: an editable install points at a
    # checkout, and on Windows those paths routinely contain spaces. A bare
    # f-string produced a command that breaks on exactly the machines that need
    # it - the recovery line worked only for people who did not need recovering.
    recovery = format_command(["pip", "install", "-e", str(editable)]) if editable else None
    before = installed_core_version() or _UNDETERMINED

    _say(out, f"[{dist_name}] {CORE_NAME} {before} is installed, but {dist_name} "
              f"requires {CORE_NAME}=={want}.")
    if editable:
        _say(out, f"[{dist_name}] NOTE: {CORE_NAME} is an EDITABLE install pointing at "
                  f"{editable}. Repairing it detaches this environment from that tree: "
                  f"the sources stay, imports stop coming from them.")
    _say(out, f"[{dist_name}] repairing it now: {shown}")

    try:
        outcome = INSTALL_RUNNER(list(cmd), execute=True)
    except Exception as e:  # a seam that raises is still a failed repair
        outcome = InstallOutcome(False, shown, f"{type(e).__name__}: {e}")

    if not outcome.ok:
        _say(out, f"[{dist_name}] the automatic repair did not succeed: {outcome.detail}")
        return RepairResult(True, False, outcome.detail, shown, editable, recovery)

    # Pick it up without restarting. Everything under invisible_core goes, the
    # finders' caches go, and the package is imported again from the files pip
    # just wrote. installed_core_version() imports ._version inside the function,
    # so it reads the new module even though this frame belongs to the old one.
    for name in core_modules_loaded():
        sys.modules.pop(name, None)
    import importlib

    importlib.invalidate_caches()
    try:
        importlib.import_module("invisible_core")
    except Exception as e:
        _say(out, f"[{dist_name}] the repair installed {CORE_NAME}=={want} but the result "
                  f"does not import: {type(e).__name__}: {e}")
        return RepairResult(
            True, False, f"the freshly installed core does not import: {e}",
            shown, editable, recovery)

    now = installed_core_version()
    if now != want:
        _say(out, f"[{dist_name}] the repair ran but {CORE_NAME} still derives "
                  f"{now or _UNDETERMINED}, not {want}.")
        return RepairResult(
            True, False, f"after the install the core still derives {now or 'nothing'}",
            shown, editable, recovery)

    _say(out, f"[{dist_name}] repaired in place: {CORE_NAME} {now} is now the version "
              f"this process will run. No restart needed.")
    if recovery:
        _say(out, "")
        _say(out, f"[{dist_name}] {CORE_NAME} is NO LONGER an editable install. Your "
                  f"sources at {editable} are untouched, but nothing imports them any "
                  f"more. To reattach them:")
        _say(out, f"    {recovery}")
        _say(out, "")
    return RepairResult(True, True, "repaired", shown, editable, recovery)


def _with_repair_note(problem: str, result: RepairResult) -> str:
    """The message the user gets when the repair could not fix it. The original
    diagnosis, unchanged, plus what was tried."""
    if result.attempted:
        note = f"An automatic repair was attempted and did not succeed: {result.reason}."
    else:
        note = f"No automatic repair was attempted: {result.reason}."
    return f"{problem}\n{note}"


def enforce_core_pin(dist_name: str, *, core_preimported: bool, stream=None) -> None:
    """The import-time form: check, and REPAIR instead of only reporting.

    Raises ImportError only when the environment is still wrong afterwards.
    """
    report = pin_report(dist_name)
    problem = report["problem"]
    if problem is None:
        return

    if os.environ.get(SKIP_ENV) == "allow":
        warnings.warn(problem, RuntimeWarning, stacklevel=2)
        return

    result = repair_core(
        dist_name=dist_name, want=report["want"],
        core_preimported=core_preimported, stream=stream,
    )
    if result.repaired:
        after = pin_report(dist_name)
        if after["problem"] is None:
            return
        # Installed the declared version and it still does not satisfy the pin.
        # Report the NEW state, not the old one, or the user chases a version
        # that is no longer there.
        raise ImportError(_with_repair_note(after["problem"], result))

    raise ImportError(_with_repair_note(problem, result))
