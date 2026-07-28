"""The publish gate, for all three packages.

Importable, so `python -m invisible_core.release` works wherever the core is
installed - which is everywhere, since both consumers pin it exactly. It used to
be a script in the core's repo, so the consumers had no gate at all: on
2026-07-27 invisible-playwright 0.4.4 reached the index built from a tree that
predated the fix it was supposed to carry, uploaded by a bare `twine upload` of
whatever happened to be in a directory. A PyPI filename is never re-uploaded, so
that artifact is wrong forever and the only remedy was 0.4.5.

`publish` builds and uploads WHAT IT JUST BUILT. That is the property which makes
that failure impossible, and it is the reason to use this rather than twine - not
the digests, not the ledger, but that the bytes uploaded are the bytes the gate
looked at.

Which project is gated comes from the pyproject.toml at --project-root, so this
file names no package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
#: The project to gate defaults to the CURRENT DIRECTORY, not to anything derived
#: from where this file sits. It used to be the script's parent, which was the
#: core's repo root while this lived in that repo's scripts/ - and became `src/`
#: the moment it moved into the package, so the shim started reading a
#: pyproject.toml that does not exist. A gate is run FROM a project; that is the
#: project it should look at, and it is the only answer that is right for an
#: installed copy too, where no repo exists to derive anything from.
DEFAULT_ROOT = Path.cwd()
LEDGER_NAME = "PUBLISHED.json"
LEDGER_SCHEMA = 1
# The identity of the project being gated. DEFAULTS: `main()` resolves them from
# the pyproject.toml at --project-root before anything reads them, so one gate
# serves all three packages.
#
# It is one gate on purpose. On 2026-07-27 invisible-playwright 0.4.4 reached the
# index built from a tree that predated the fix it was meant to carry, uploaded
# by a bare `twine upload` of whatever was in a directory - because the consumers
# had no gate at all. A PyPI filename is never re-uploaded, so that artifact is
# wrong forever and the only remedy was 0.4.5.
DIST_NAME = "invisible-core"
PKG_NAME = "invisible_core"


def resolve_project_identity(root: "Path") -> "tuple[str, str]":
    """(distribution name, wheel package name) read from the project itself.

    From pyproject rather than from an argument: a name passed on the command
    line is a second place for it to be wrong, and this gate exists to stop that
    class of mistake.
    """
    cfg_path = Path(root) / "pyproject.toml"
    try:
        import tomllib
        with cfg_path.open("rb") as fh:
            cfg = tomllib.load(fh)
    except Exception as exc:
        raise GateBroken(f"cannot read {cfg_path}: {exc}") from exc
    dist = str(cfg.get("project", {}).get("name") or "").strip()
    if not dist:
        raise GateBroken(f"{cfg_path} declares no [project] name")
    packages = (cfg.get("tool", {}).get("hatch", {}).get("build", {})
                   .get("targets", {}).get("wheel", {}).get("packages") or [])
    pkg = Path(packages[0]).name if packages else dist.replace("-", "_")
    return dist, pkg

EXIT_OK, EXIT_REFUSED, EXIT_BROKEN, EXIT_USAGE, EXIT_NO_LEDGER = 0, 1, 2, 3, 4

#: Already published, and byte-identical to what was published. NOT a refusal,
#: and giving it its own code is what lets a caller tell the two apart. They
#: used to share EXIT_REFUSED, which meant a release tag pointing at a version
#: already on the index - backfilling tags for releases published by hand, or
#: simply re-pushing a tag - produced a red run indistinguishable from "the
#: content changed under an unmoved version", which must stay a hard failure.
#: Here there is nothing to do and nothing wrong.
EXIT_ALREADY_PUBLISHED = 5

# The JSON API of the index this project publishes to. Overridable so that the
# cross-check can follow a `--repository testpypi` upload to the index that
# upload actually lands on, and so the tests can state what the index holds
# instead of depending on the network. Pointing it somewhere that agrees with
# you is a deliberate act, the same class of act as hand-editing the ledger, and
# neither is a threat this gate can or tries to defend against - the failure it
# defends against is the accidental one.
DEFAULT_INDEX_JSON_URL = f"https://pypi.org/pypi/{DIST_NAME}/json"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# Root entries a build backend injects into the sdist whatever the include list
# says. Measured on hatchling: .gitignore ships even though sdist `include`
# names only src/invisible_core, tests, README.md, LICENSE and pyproject.toml.
# They describe the checkout, not the package, and treating them as content made
# the gate refuse a release over a newly added .gitignore. Kept deliberately
# short and root-only: a file with one of these names further down the tree is
# still content, and everything not named here is still content too.
SDIST_NONCONTENT = frozenset({".gitignore", ".gitattributes", ".hgignore", ".hgtags"})


class GateBroken(Exception):
    """The gate could not form an opinion. Never a refusal."""


class LedgerUnavailable(Exception):
    """The gate's memory is missing or unreadable. Never a pass, never a refusal."""


# --------------------------------------------------------------- manifests

def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def normalise(blob: bytes) -> bytes:
    """Fold CRLF and lone CR to LF for anything that is text.

    A checkout on Windows with autocrlf active and the same checkout on Linux
    hold the same content in different bytes. The ledger is checked in and
    travels between the two, so digesting raw bytes would make the first CI run
    on Linux refuse a release nobody made a change to. Binary payloads are left
    exactly alone: a NUL byte or a failed UTF-8 decode is the test, and both are
    conservative - misjudging a text file as binary costs a false alarm, the
    error this normalisation exists to remove, so the test errs towards text
    only when the bytes really do decode.

    THE EXACT BOUNDARY, both sides of it deliberate
    -----------------------------------------------
    This is the only place the gate is allowed to be blind, so the width is
    chosen rather than inherited, and both edges are pinned by tests
    (test_release_gate.py, D4 and D6):

      lower edge - it must fold at least CRLF, or the gate cries wolf on every
        cross-platform release and gets switched off.
      upper edge - it must fold nothing ELSE. Every other whitespace difference
        survives: an extra space, a tab, a blank line, a trailing newline and,
        above all, a change of indentation. Indentation is semantic in Python,
        so a normalisation that stripped whitespace generally would let a
        re-indent - which can move a statement out of an `if` - ship under an
        unchanged version.

    THE COST, accepted knowingly
    ----------------------------
    A lone CR is folded wherever it appears, including inside a string literal
    where it is DATA and not a line ending. A build whose only difference is
    `"a\\rb"` becoming `"a\\nb"` therefore reads as byte-identical here and the
    gate will not refuse it. The alternative is to tell a content CR apart from
    a line-ending CR without parsing the language the file is written in, which
    cannot be done; digesting raw bytes instead brings back the false alarm on
    every CRLF checkout, which is the worse failure because it ends with the
    gate turned off. Source that genuinely needs a bare CR should write the
    escape `\\r` rather than embed the byte, and then it is ordinary text and is
    digested like any other.
    """
    if b"\x00" in blob:
        return blob
    try:
        blob.decode("utf-8")
    except UnicodeDecodeError:
        return blob
    return blob.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def wheel_manifest(whl: Path) -> dict[str, str]:
    """path -> sha256 of each member's normalised bytes.

    Hashed here rather than copied out of RECORD: RECORD carries the raw byte
    digest, which is exactly the number a CRLF/LF flip moves. RECORD itself is
    skipped - its bytes are a function of every other member, so including it
    would just re-report the same differences a second time, un-normalised.
    """
    out: dict[str, str] = {}
    with zipfile.ZipFile(whl) as z:
        record_names = [n for n in z.namelist() if n.endswith(".dist-info/RECORD")]
        if len(record_names) != 1:
            raise GateBroken(f"{whl.name}: expected exactly one RECORD, found {record_names}")
        dist_info = record_names[0].rsplit("/", 1)[0] + "/"
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name == record_names[0]:
                continue
            # The dist-info directory carries the version in its NAME. Normalise
            # it so the manifest describes content and nothing else.
            if name.startswith(dist_info):
                name = "<dist-info>/" + name[len(dist_info):]
            out[name] = _sha256_bytes(normalise(z.read(info)))
    if not out:
        raise GateBroken(f"{whl.name}: the wheel holds no files")
    return out


def sdist_manifest(sdist: Path) -> dict[str, str]:
    """path -> sha256 for every regular file, top-level version dir stripped."""
    out: dict[str, str] = {}
    with tarfile.open(sdist, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = member.name.split("/", 1)[1] if "/" in member.name else member.name
            if name in SDIST_NONCONTENT:
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            out[name] = _sha256_bytes(normalise(fh.read()))
    if not out:
        raise GateBroken(f"{sdist.name}: no files")
    return out


def manifest_digest(manifest: dict[str, str]) -> str:
    blob = "\n".join(f"{p},{manifest[p]}" for p in sorted(manifest))
    return _sha256_bytes(blob.encode("utf-8"))


def diff_manifests(old: dict[str, str], new: dict[str, str]) -> dict[str, list[str]]:
    return {
        "changed": sorted(p for p in old.keys() & new.keys() if old[p] != new[p]),
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
    }


# ------------------------------------------------------------------ build

def _build_once(root: Path, outdir: Path, isolated: bool) -> tuple[Path, Path]:
    cmd = [sys.executable, "-m", "build", "--wheel", "--sdist",
           "--outdir", str(outdir), str(root)]
    if not isolated:
        # Default: no network. Isolation would pip-install the backend.
        cmd.insert(4, "--no-isolation")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-15:]
        raise GateBroken(
            "the build failed, so there is nothing to compare:\n  "
            + "\n  ".join(tail)
            + "\n\nIf the backend is missing: pip install build hatchling"
        )
    whls = list(outdir.glob("*.whl"))
    tars = list(outdir.glob("*.tar.gz"))
    if len(whls) != 1 or len(tars) != 1:
        raise GateBroken(f"expected one wheel and one sdist in {outdir}, got {whls} {tars}")
    return whls[0], tars[0]


def _version_from_wheel(whl: Path) -> str:
    with zipfile.ZipFile(whl) as z:
        name = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")][0]
        meta = z.read(name).decode("utf-8", "replace")
    for line in meta.splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
        if not line.strip():
            break
    raise GateBroken(f"{whl.name}: METADATA declares no Version")


def _requires_dist(whl: Path) -> list[str]:
    with zipfile.ZipFile(whl) as z:
        name = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")][0]
        meta = z.read(name).decode("utf-8", "replace")
    out = []
    for line in meta.splitlines():
        if not line.strip():
            break
        if line.startswith("Requires-Dist:"):
            out.append(line.split(":", 1)[1].strip())
    return out


def _seal_tag_from_wheel(whl: Path) -> str:
    """Archaeology for the ledger entry: which engine this core was sealed to."""
    try:
        with zipfile.ZipFile(whl) as z:
            data = json.loads(z.read(f"{PKG_NAME}/seal.json").decode("utf-8"))
        return str(data.get("tag", ""))
    except Exception:
        return ""


def inspect(root: Path, *, isolated: bool, self_check: bool) -> dict:
    """Build and describe the artifact. Raises GateBroken, never refuses."""
    with tempfile.TemporaryDirectory(prefix="invisible-core-gate-") as td:
        tmp = Path(td)
        whl, sdist = _build_once(root, tmp / "a", isolated)
        info = {
            "version": _version_from_wheel(whl),
            "seal_tag": _seal_tag_from_wheel(whl),
            "requires_dist": _requires_dist(whl),
            "wheel": {"files": wheel_manifest(whl)},
            "sdist": {"files": sdist_manifest(sdist)},
            "wheel_filename": whl.name,
            "sdist_filename": sdist.name,
        }
        info["wheel"]["digest"] = manifest_digest(info["wheel"]["files"])
        info["sdist"]["digest"] = manifest_digest(info["sdist"]["files"])

        if self_check:
            # A build that is not reproducible makes every comparison below
            # meaningless. That is a broken gate (exit 2), never a refusal.
            whl2, sdist2 = _build_once(root, tmp / "b", isolated)
            w2, s2 = wheel_manifest(whl2), sdist_manifest(sdist2)
            if manifest_digest(w2) != info["wheel"]["digest"]:
                d = diff_manifests(info["wheel"]["files"], w2)
                raise GateBroken(
                    "two builds of the SAME tree produced different wheels, so a "
                    "digest comparison proves nothing here.\n"
                    f"  unstable entries: {d['changed'] + d['added'] + d['removed']}\n"
                    "  pin the build backend, or re-run with --single-build if you "
                    "accept the weaker check.")
            if manifest_digest(s2) != info["sdist"]["digest"]:
                d = diff_manifests(info["sdist"]["files"], s2)
                raise GateBroken(
                    "two builds of the SAME tree produced different sdists.\n"
                    f"  unstable entries: {d['changed'] + d['added'] + d['removed']}")
    return info


# ----------------------------------------------------------------- ledger

def _version_in_filename(leg: str, filename: str) -> str | None:
    """The version an artifact filename declares, or None if it declares none.

    A wheel is `<name>-<version>-<python>-<abi>-<platform>.whl` and an sdist is
    `<name>-<version>.tar.gz`, with the distribution name escaped so that it can
    never itself contain a hyphen (PEP 427). So the version is recoverable from
    the name without guessing, and it is a SECOND, independent witness of what
    the entry claims to be - which is the whole point of reading it.
    """
    if leg == "wheel":
        if not filename.endswith(".whl"):
            return None
        parts = filename[:-len(".whl")].split("-")
        return parts[1] if len(parts) >= 3 else None
    if not filename.endswith(".tar.gz"):
        return None
    stem = filename[:-len(".tar.gz")]
    return stem.rsplit("-", 1)[1] if "-" in stem else None


def _validate_entry(path: Path, i: int, entry: object) -> None:
    """A present-but-malformed entry is GATE BROKEN, never a violation.

    Without this, an entry missing its 'wheel' key blew up inside the diff and
    the caller reported something violation-shaped. The operator then reads
    "the content changed" and starts hunting a change that does not exist, which
    is the exact failure mode that gets gates disabled. Say the record is
    unusable, and say which record.

    Two of the checks below are not shape checks, they are CONSISTENCY checks,
    and both close a measured hole:

      * the version string is the key everything else is looked up by
        (_entry_for matches on it and nothing else), so on its own it is a claim
        with no witness. Editing released[0].version from 18.0.0 to 17.9.0 made
        a drifted 18.0.0 build look like it had never been published and exited
        0 PUBLISH ALLOWED, while every other single-field corruption in this
        file yields exit 2. The recorded filenames carry the version too, so the
        entry can be made to corroborate itself.
      * a digest that does not match the file table it sits next to means the
        entry disagrees with itself, and there is no way to tell which half is
        right. Overwriting both digests with garbage while leaving the tables
        intact used to read as "nothing to release", because the verdict was
        computed from the tables and only the digests were printed.
    """
    where = f"{path}: released[{i}]"
    if not isinstance(entry, dict):
        raise GateBroken(f"{where} is {type(entry).__name__}, not an object")
    version = entry.get("version")
    if not isinstance(version, str) or not version:
        raise GateBroken(f"{where} has no 'version' string")
    for leg in ("wheel", "sdist"):
        blob = entry.get(leg)
        if not isinstance(blob, dict):
            raise GateBroken(
                f"{where} ({version}) has no '{leg}' record, so there is nothing to "
                f"compare this build against. The ledger is written only by "
                f"`version_gate.py record`; a hand edit or a bad merge is the usual "
                f"cause. Restore it from git history rather than re-recording, or the "
                f"record of what was actually published is lost.")
        if not isinstance(blob.get("files"), dict) or not blob["files"]:
            raise GateBroken(f"{where} ({version}): '{leg}.files' is missing or empty")
        if not isinstance(blob.get("digest"), str) or not blob["digest"]:
            raise GateBroken(f"{where} ({version}): '{leg}.digest' is missing")
        bad = [k for k, v in blob["files"].items() if not isinstance(v, str)]
        if bad:
            raise GateBroken(f"{where} ({version}): '{leg}.files' has non-string hashes "
                             f"for {bad[:3]}")

        key = f"{leg}_filename"
        filename = entry.get(key)
        if not isinstance(filename, str) or not filename:
            raise GateBroken(
                f"{where} ({version}) has no '{key}'. `record` always writes it, and "
                f"it is the only thing in the entry that corroborates the version "
                f"string - without it one edited field turns a published version into "
                f"an unpublished one. Restore the entry from git history.")
        declared = _version_in_filename(leg, filename)
        if declared is None:
            raise GateBroken(
                f"{where} ({version}): '{key}' is {filename!r}, which is not a "
                f"{leg} filename this gate can read a version out of.")
        if declared != version:
            raise GateBroken(
                f"{where}: the entry says version {version!r} but its '{key}' is "
                f"{filename!r}, which was built as {declared!r}. The two cannot both "
                f"be right, so this record cannot say what was published. An edited "
                f"version string is the usual cause, and on its own it would make a "
                f"drifted build look like it had never been released. Restore the "
                f"entry from git history rather than re-recording it.")

        recomputed = manifest_digest(blob["files"])
        if recomputed != blob["digest"]:
            raise GateBroken(
                f"{where} ({version}): '{leg}.digest' is {blob['digest'][:16]} but the "
                f"'{leg}.files' table beside it hashes to {recomputed[:16]}. The entry "
                f"contradicts itself and there is no way to tell which half is the "
                f"record of what shipped, so this gate will not compare against "
                f"either. Restore the entry from git history.")


def load_ledger(path: Path) -> dict:
    """Read the ledger, or raise. It NEVER invents one.

    An absent file used to return an empty ledger, and `check` then announced
    "this is release 1" and exited 0. That is the gate's single piece of offline
    memory failing open: a --ledger typo, a deleted file, a shallow checkout or
    a bad merge each disarmed it while a real content change sat in the tree.
    Missing and unreadable are now the same hard stop, and it is a stop the
    operator cannot mistake for a pass.
    """
    if not path.exists():
        raise LedgerUnavailable(
            f"{path}: NO LEDGER.\n"
            "  This file is the gate's only offline memory of what was already\n"
            "  published. Without it the gate cannot tell a first release from a\n"
            "  release whose content moved under a version that did not, so it\n"
            "  refuses to guess.\n"
            "  Usual causes, in order: a typo in --ledger; a checkout that did not\n"
            "  bring the repo root; the file deleted or lost in a merge.\n"
            "  If this really is the first release ever, create it with\n"
            f'    printf \'{{"schema": {LEDGER_SCHEMA}, "released": []}}\' > "{path.name}"\n'
            "  and run `check --first-release` - which says so out loud, once.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise LedgerUnavailable(
            f"{path}: UNREADABLE LEDGER ({e}).\n"
            "  Restore it from git history. Do not delete it to move on: an absent\n"
            "  ledger is this same failure, and re-recording would overwrite the\n"
            "  record of what was actually published.")
    if not isinstance(data, dict):
        raise LedgerUnavailable(f"{path}: UNREADABLE LEDGER (top level is not an object)")
    if data.get("schema") != LEDGER_SCHEMA:
        raise LedgerUnavailable(
            f"{path}: ledger schema is {data.get('schema')!r}, this gate understands "
            f"{LEDGER_SCHEMA}")
    if not isinstance(data.get("released"), list):
        raise LedgerUnavailable(f"{path}: ledger has no 'released' list")
    for i, entry in enumerate(data["released"]):
        _validate_entry(path, i, entry)
    return data


def write_ledger(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _entry_for(ledger: dict, version: str) -> dict | None:
    for e in ledger["released"]:
        if e.get("version") == version:
            return e
    return None


def _parse_version(v: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match(v)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


# ------------------------------------------------------------------ report

def _report_diff(label: str, d: dict[str, list[str]]) -> list[str]:
    lines = []
    for kind in ("changed", "added", "removed"):
        for p in d[kind]:
            lines.append(f"    {kind:<8} {label}: {p}")
    return lines


REMEDY = """
  Two remedies are legal, and exactly one of them fits:

    1. A core-only fix against the SAME engine
       bump CORE_REVISION in src/invisible_core/_version.py (it is the only
       hand-edited number in the package). {cur} -> {next_rev}
       Never reset it: pip compares with != , not <.

    2. The engine moved
       roll a new seal (a new firefox-N tag in src/invisible_core/seal.json).
       The version follows the tag on its own, no hand edit.

  Then re-run: python scripts/version_gate.py check
"""


#: For a package whose version is a plain literal in pyproject.toml. The core's
#: REMEDY names CORE_REVISION and rolling a seal, neither of which the consumers
#: have - and a remedy naming a knob the reader does not own turns a refusal into
#: a support question.
CONSUMER_REMEDY = """
  Publishing this as-is is a no-op for every installed copy: pip's
  satisfaction check is name plus version, never content, so nobody
  who already has {cur} would ever receive these bytes.

  Bump `version` in pyproject.toml - {cur} -> {next_rev} - and note the
  change in CHANGELOG.md. Never reuse a version: a PyPI filename can
  never be re-uploaded, so a wrong artifact stays wrong forever.

  Then re-run: python -m invisible_core.release check
"""


def _remedy(version: str) -> str:
    p = _parse_version(version)
    if PKG_NAME == "invisible_core":
        nxt = f"{p[0]}.{p[1] + 1}.0" if p else "the next revision"
        return REMEDY.format(cur=version, next_rev=nxt)
    nxt = f"{p[0]}.{p[1]}.{p[2] + 1}" if p else "the next version"
    return CONSUMER_REMEDY.format(cur=version, next_rev=nxt)


# ---------------------------------------------------------------- commands

def _ledger_path_for(args) -> Path:
    root = Path(args.project_root).resolve()
    return Path(args.ledger) if args.ledger else root / LEDGER_NAME


def _index_url_for(args) -> str:
    """The index for THIS project, unless the caller named another.

    Reads the module global, which `main()` rebinds from the resolved
    distribution name. The flag's own default is None on purpose - see the
    comment where it is declared.
    """
    return getattr(args, "index_json_url", None) or DEFAULT_INDEX_JSON_URL


def cmd_check(args) -> int:
    root = Path(args.project_root).resolve()
    ledger_path = _ledger_path_for(args)
    ledger = load_ledger(ledger_path)

    try:
        info = inspect(root, isolated=args.isolated, self_check=not args.single_build)
    except GateBroken as e:
        print(f"GATE BROKEN: {e}", file=sys.stderr)
        return EXIT_BROKEN

    version = info["version"]
    seal = info["seal_tag"]
    print(f"{DIST_NAME} {version}" + (f"  (seal {seal})" if seal else ""))
    print(f"  wheel {info['wheel']['digest'][:16]}  {len(info['wheel']['files'])} entries")
    print(f"  sdist {info['sdist']['digest'][:16]}  {len(info['sdist']['files'])} entries")
    print(f"  ledger {ledger_path}  ({len(ledger['released'])} published)")

    # A public index rejects a Requires-Dist carrying a URL, and a direct
    # reference is also the construct that makes `pip check` blind. Refuse it
    # here rather than at the upload, which happens after the build.
    urls = [r for r in info["requires_dist"] if "@" in r.split(";")[0]]
    if urls:
        print("\nRELEASE REFUSED: a dependency is declared as a direct URL reference.")
        for r in urls:
            print(f"    {r}")
        print("\n  A public index rejects this metadata outright (HTTP 400), and a\n"
              "  direct reference carries no version specifier, so `pip check` has\n"
              "  nothing to compare and reports a broken environment as healthy.\n"
              "  Declare a real specifier instead, for example name==X.Y.Z.")
        return EXIT_REFUSED

    if not ledger["released"]:
        # An empty ledger is the one state where this gate has nothing to
        # compare against, which makes it the one state worth claiming falsely.
        # It is not accepted on the file's say-so: release 1 happens once in the
        # life of a project, so the operator states it, out loud, on the command
        # line. And the operator is not taken on trust either - the index
        # cross-check is FORCED here, with no flag to turn it on and none to turn
        # it off, because "nothing published yet" is the single claim the index
        # can settle outright and this is the single branch on which nothing else
        # is checked at all. An earlier version of this comment claimed the same
        # thing while the code below asked for --verify-index first; a content
        # change then walked through on exit 0. Do not put a condition back on
        # that call without deleting this paragraph.
        if not getattr(args, "first_release", False):
            print("\nRELEASE REFUSED: the ledger records no published version.")
            print(f"  {ledger_path} parses and is empty. That is either the first")
            print("  release ever, or a ledger that lost its entries - and this gate")
            print("  cannot tell those apart, so it will not guess in the direction")
            print("  that lets everything through.")
            print("\n  If nothing has ever been published, say so:")
            print("    python scripts/version_gate.py check --first-release")
            print("  If something HAS been published, restore the ledger from git")
            print("  history. Do not re-record: `record` would write today's digests")
            print("  as if they were what shipped, and the real record is then gone.")
            return EXIT_REFUSED
        print("\nFIRST RELEASE DECLARED - the operator states nothing is published yet.")
        print("  The ledger is empty, so there is no earlier content to compare")
        print("  against. This gate starts guarding the moment `record` writes the")
        print("  first entry, and --first-release must never be passed again.")
        print("  Nothing local can check this claim, so the index is asked, always:")
        rc = _verify_first_release(_index_url_for(args))
        if rc != EXIT_OK:
            return rc
        print("\nPUBLISH ALLOWED")
        return EXIT_OK

    if getattr(args, "first_release", False):
        print(f"\nRELEASE REFUSED: --first-release was passed, but {ledger_path}")
        print(f"  already records {len(ledger['released'])} published version(s):")
        for e in ledger["released"]:
            print(f"    {e['version']}  ({e.get('published_at', 'at an unrecorded time')})")
        print("  The flag exists to be used exactly once. Drop it.")
        return EXIT_REFUSED

    # Monotonicity. The version scheme is derived, but CORE_REVISION is hand
    # edited, and a reset produces a version collision explained badly.
    cur = _parse_version(version)
    if cur is not None:
        newest = max(
            (p for p in (_parse_version(e.get("version", "")) for e in ledger["released"])
             if p is not None), default=None)
        if newest is not None and cur < newest:
            hi = ".".join(str(x) for x in newest)
            print(f"\nRELEASE REFUSED: version went BACKWARDS. {version} < {hi}, already published.")
            print("  CORE_REVISION was reset, or the seal tag moved down. pip compares")
            print("  with != , not <, so a version that goes backwards is not an")
            print("  upgrade for anybody and the index will not accept it twice.")
            return EXIT_REFUSED

    prior = _entry_for(ledger, version)
    if prior is None:
        print(f"\nVERSION {version} HAS NEVER BEEN PUBLISHED - it moved since the last release.")
        if getattr(args, "verify_index", False):
            rc = _verify_index(version, _index_url_for(args), _ledger_path_for(args))
            if rc != EXIT_OK:
                return rc
        print("\nPUBLISH ALLOWED")
        return EXIT_OK

    # The verdict is the DIGEST comparison, because the digest is what the
    # refusal below prints and what the ledger stores - printing one number as
    # evidence while deciding on another is how a gate ends up unfalsifiable.
    # The file tables are the explanation of the verdict, never the verdict, and
    # _validate_entry has already refused to load an entry whose digest and file
    # table disagree, so the two can never point different ways here.
    wdiff = diff_manifests(prior["wheel"]["files"], info["wheel"]["files"])
    sdiff = diff_manifests(prior["sdist"]["files"], info["sdist"]["files"])
    changed = (prior["wheel"]["digest"] != info["wheel"]["digest"]
               or prior["sdist"]["digest"] != info["sdist"]["digest"])

    if not changed:
        print(f"\nRELEASE REFUSED: {version} is already published and its content is")
        print("  byte-identical to what was published. There is nothing to release,")
        print("  and the index refuses a filename it has already served.")
        print(f"  published {prior.get('published_at', 'at an unrecorded time')}")
        print("  (exit 5, not 1: a no-op, not a refusal. A caller that treats")
        print("   every non-zero alike reports a red release for a tag that is")
        print("   simply pointing at something already shipped.)")
        return EXIT_ALREADY_PUBLISHED

    print("\nRELEASE REFUSED: the content changed but the version did not.")
    print(f"  version    {version}  (published {prior.get('published_at', '?')})")
    print(f"  wheel      {prior['wheel']['digest'][:16]} -> {info['wheel']['digest'][:16]}")
    print(f"  sdist      {prior['sdist']['digest'][:16]} -> {info['sdist']['digest'][:16]}")
    print("\n  Files that differ from the published artifact:")
    for line in _report_diff("wheel", wdiff) + _report_diff("sdist", sdiff):
        print(line)
    print("\n  Publishing this as-is is a no-op for every installed copy: pip's")
    print("  satisfaction check is name plus version, never content, so nobody")
    print("  who already has {v} would ever receive these bytes.".format(v=version))
    print(_remedy(version))
    return EXIT_REFUSED


def cmd_record(args) -> int:
    root = Path(args.project_root).resolve()
    ledger_path = _ledger_path_for(args)
    ledger = load_ledger(ledger_path)

    try:
        info = inspect(root, isolated=args.isolated, self_check=not args.single_build)
    except GateBroken as e:
        print(f"GATE BROKEN: {e}", file=sys.stderr)
        return EXIT_BROKEN

    version = info["version"]
    prior = _entry_for(ledger, version)
    if prior is not None:
        if prior["wheel"]["digest"] == info["wheel"]["digest"] and \
                prior["sdist"]["digest"] == info["sdist"]["digest"]:
            print(f"{version} already recorded with these exact digests, nothing to do.")
            return EXIT_OK
        print(f"REFUSED: {version} is already in the ledger with DIFFERENT digests.",
              file=sys.stderr)
        print(f"  wheel      {prior['wheel']['digest'][:16]} -> "
              f"{info['wheel']['digest'][:16]}", file=sys.stderr)
        print(f"  sdist      {prior['sdist']['digest'][:16]} -> "
              f"{info['sdist']['digest'][:16]}", file=sys.stderr)
        for line in (_report_diff("wheel", diff_manifests(prior["wheel"]["files"],
                                                          info["wheel"]["files"]))
                     + _report_diff("sdist", diff_manifests(prior["sdist"]["files"],
                                                            info["sdist"]["files"]))):
            print(line, file=sys.stderr)
        print("  Recording over it would erase the record of what was actually",
              file=sys.stderr)
        print("  published - and it is exactly the drift `check` exists to refuse,",
              file=sys.stderr)
        print("  made invisible in one command. Bump the version instead.",
              file=sys.stderr)
        return EXIT_REFUSED

    ledger["released"].append({
        "version": version,
        "seal_tag": info["seal_tag"],
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "wheel_filename": info["wheel_filename"],
        "sdist_filename": info["sdist_filename"],
        "wheel": info["wheel"],
        "sdist": info["sdist"],
    })
    ledger["released"].sort(key=lambda e: _parse_version(e.get("version", "")) or (0, 0, 0))
    write_ledger(ledger_path, ledger)
    print(f"recorded {version} in {ledger_path}")
    print(f"  wheel {info['wheel']['digest']}")
    print(f"  sdist {info['sdist']['digest']}")
    return EXIT_OK


def cmd_show(args) -> int:
    root = Path(args.project_root).resolve()
    try:
        info = inspect(root, isolated=args.isolated, self_check=not args.single_build)
    except GateBroken as e:
        print(f"GATE BROKEN: {e}", file=sys.stderr)
        return EXIT_BROKEN
    print(json.dumps({
        "version": info["version"],
        "seal_tag": info["seal_tag"],
        "wheel_digest": info["wheel"]["digest"],
        "sdist_digest": info["sdist"]["digest"],
        "wheel_entries": len(info["wheel"]["files"]),
        "sdist_entries": len(info["sdist"]["files"]),
        "requires_dist": info["requires_dist"],
    }, indent=2))
    return EXIT_OK


def cmd_publish(args) -> int:
    """check -> upload -> record, in one command, so the gate cannot be skipped.

    This exists because the first version of this gate was wired to nothing:
    `grep -rn version_gate` over the whole workbench found the script, its test
    and a comment, and an operator running `twine upload dist/*` by hand would
    never have touched it. A gate that depends on somebody remembering it is a
    runbook note wearing a gate's clothes.

    So the upload lives INSIDE the gate rather than beside it. `check` runs
    first, in-process; on anything other than exit 0 this returns that exit code
    unchanged and twine is never reached. The artifacts uploaded are the ones
    the gate just built and digested, from a temporary directory, so there is no
    window in which a stale dist/ gets shipped instead of the thing that was
    checked. `record` runs only after twine returns 0, because the ledger is a
    record of what reached the index and not of what we intended to send.
    """
    try:
        rc = cmd_check(args)
    except (LedgerUnavailable, GateBroken):
        # main() turns these into exit 4 / exit 2. Say the upload did not happen
        # before they get there, or the operator reads a stack of ledger advice
        # and has to guess whether anything was uploaded.
        print("\nupload NOT attempted: the gate could not run.", file=sys.stderr)
        raise
    if rc != EXIT_OK:
        print("\nupload NOT attempted: the gate did not pass.", file=sys.stderr)
        return rc

    root = Path(args.project_root).resolve()
    with tempfile.TemporaryDirectory(prefix="invisible-core-publish-") as td:
        outdir = Path(td)
        try:
            whl, sdist = _build_once(root, outdir, args.isolated)
        except GateBroken as e:
            print(f"GATE BROKEN: {e}", file=sys.stderr)
            return EXIT_BROKEN
        cmd = [sys.executable, "-m", "twine", "upload", str(whl), str(sdist)]
        if args.repository:
            cmd[4:4] = ["--repository", args.repository]
        if args.dry_run:
            print("\n--dry-run: the gate passed, and this is the upload it authorises:")
            print("    " + " ".join(cmd))
            print("  Nothing was uploaded and nothing was recorded.")
            return EXIT_OK
        print("\ngate passed, uploading:\n    " + " ".join(cmd))
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            print(f"\nupload FAILED (twine exit {proc.returncode}). Nothing recorded: the",
                  file=sys.stderr)
            print("  ledger records what reached the index, never what we tried to send.",
                  file=sys.stderr)
            return EXIT_BROKEN
    return cmd_record(args)


def _index_versions(url: str) -> list[str]:
    """Every version the index serves for this distribution. Raises GateBroken.

    A 404 is an answer, not a failure: the project has never been published.
    Anything else that goes wrong is NOT an answer, and the caller must never be
    able to read it as an empty index - that is the fail-open shape this whole
    file exists to avoid.
    """
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise GateBroken(f"index cross-check failed: {e} ({url})")
    except Exception as e:
        raise GateBroken(f"index cross-check failed: {e} ({url})")
    if not isinstance(data, dict) or not isinstance(data.get("releases", {}), dict):
        raise GateBroken(f"index cross-check failed: {url} did not answer with a "
                         f"releases object")
    return sorted(data.get("releases", {}))


def _verify_index(version: str, url: str, ledger: Path | None = None) -> int:
    """Optional ONLINE cross-check. The ledger is a claim; the index is the fact."""
    try:
        published = _index_versions(url)
    except GateBroken as e:
        print(f"GATE BROKEN: {e}", file=sys.stderr)
        return EXIT_BROKEN
    if version in published:
        print(f"\nRELEASE REFUSED: the index already serves {DIST_NAME} {version}.")
        print("  A version number can never be reused, not even after a yank.")
        return EXIT_REFUSED
    print(f"  index cross-check: {DIST_NAME} {version} is not on the index "
          f"({len(published)} versions published)")

    # And the other direction, which nothing asked until 2026-07-26: does the
    # ledger cover everything the index serves? 18.1.0 was uploaded on
    # 2026-07-25 and has no entry - `git log -S18.1.0 -- PUBLISHED.json` finds
    # nothing, ever. The ledger's own header claims one entry per version that
    # reached the index, so the claim was already false and the gate could not
    # see it: every check compares the CURRENT version against the entries that
    # happen to exist, and a missing entry simply is not consulted.
    #
    # It matters because the entry is what a later release is compared against.
    # A version with no entry can be re-released with different bytes and
    # nothing here would object.
    try:
        recorded = ({e.get("version") for e in load_ledger(ledger).get("released", [])}
                    if ledger is not None else None)
    except Exception:
        recorded = None
    if recorded is not None:
        gaps = sorted(v for v in published if v not in recorded)
        if gaps:
            print()
            print(f"RELEASE REFUSED: the index serves {len(gaps)} version(s) of "
                  f"{DIST_NAME} that the ledger does not record:")
            print("    " + ", ".join(gaps))
            print("  Those were published outside this gate, so there is no record of")
            print("  what shipped under them and a re-release with different bytes")
            print("  would go unnoticed. Back-fill them from the artifacts the index")
            print("  actually serves - not from today's tree, which has moved on.")
            return EXIT_REFUSED
    return EXIT_OK


def _verify_first_release(url: str) -> int:
    """The FORCED cross-check behind --first-release. Never optional.

    "Nothing has ever been published" is the one claim in this gate that an
    index can settle outright, and it is also the one claim that turns the gate
    off completely: with an empty ledger there is no earlier content, so a real
    content change sails through. Measured - `check --first-release` on an empty
    ledger and a modified tree exited 0, and `publish --first-release --dry-run`
    went on to print the twine command it authorised. The recipe that gets an
    operator into that state is the printf load_ledger itself prints when the
    ledger is missing, so it is the documented path rather than an exotic one.

    Hence: on that branch the index is asked, always, and it must serve NO
    version at all. An index that cannot be reached is exit 2 (no opinion), not
    exit 0 - which costs nothing real, because release 1 is being run on a
    machine that is about to upload to that same index.
    """
    try:
        published = _index_versions(url)
    except GateBroken as e:
        print(f"GATE BROKEN: {e}", file=sys.stderr)
        print("  --first-release forces this cross-check: the claim is that NOTHING\n"
              "  has been published, and the index is the only thing that can settle\n"
              "  it. With an empty ledger there is no content to compare against\n"
              "  either, so passing here would mean the gate checked nothing at all.\n"
              "  Run it where the index is reachable - the upload needs that anyway.",
              file=sys.stderr)
        return EXIT_BROKEN
    if published:
        print("\nRELEASE REFUSED: --first-release says nothing has ever been published,")
        print(f"  but the index already serves {len(published)} version(s) of {DIST_NAME}:")
        print("    " + ", ".join(published[:8]) + (" ..." if len(published) > 8 else ""))
        print("  So the ledger is empty because it was LOST, not because this is")
        print("  release 1. Restore it from git history. Do not re-record: `record`")
        print("  would write today's digests as if they were what shipped, and the")
        print("  record of what actually shipped is then gone.")
        return EXIT_REFUSED
    print(f"  index cross-check: {DIST_NAME} has no published version, so the")
    print("  first-release claim holds.")
    return EXIT_OK


class _Parser(argparse.ArgumentParser):
    """Argparse exits 2 on a usage error. In this gate 2 means EXIT_BROKEN.

    So `version_gate.py chekc` and "the gate could not reach a verdict" were the
    same number, and a caller reading the exit code could not tell a typo from a
    broken gate. `EXIT_USAGE = 3` had been declared for exactly this and used
    nowhere - open item 5 in `18-gate-inventory.md`, alongside the note that a
    documented code which cannot happen is a false entry in the contract.

    Wired rather than deleted, because the ambiguity is real: `doctor` already
    uses 2 for "a check could not be made", and A3 in that same document warns
    that anything treating 2 as an argparse error will misread it.
    """

    def error(self, message):                      # pragma: no cover - exits
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        raise SystemExit(EXIT_USAGE)


def main(argv=None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    p = _Parser(
        prog="version_gate.py",
        description="Refuse to publish a core whose content changed but whose version did not.")
    p.add_argument("--project-root", default=str(DEFAULT_ROOT),
                   help="the invisible_core repo root (default: this script's parent)")
    p.add_argument("--ledger", default=None,
                   help=f"path to the ledger (default: <project-root>/{LEDGER_NAME})")
    p.add_argument("--isolated", action="store_true",
                   help="build in an isolated env (needs index access; default is offline)")
    p.add_argument("--single-build", action="store_true",
                   help="skip the build-twice reproducibility self-check")
    sub = p.add_subparsers(dest="cmd")

    def _gate_flags(sp):
        sp.add_argument("--verify-index", action="store_true",
                        help="also ask the index whether this version already exists (online)")
        sp.add_argument("--first-release", action="store_true",
                        help="state that nothing has ever been published. Only legal "
                             "against an empty ledger, only ever once, and always "
                             "cross-checked against the index.")
        # default=None, NOT the module constant. The parser is built before
        # main() resolves which project this is, so a default captured here is
        # invisible-core's URL forever - and `_index_url_for` prefers a non-None
        # args value over the resolved one. Measured 2026-07-28 on the manager:
        # `check --verify-index` fetched the CORE's index and reported "the
        # index serves 8 version(s) of invisible_firefox that the ledger does
        # not record: 18.0.0 ... 18.7.0". Core versions, named as the manager's.
        # Both consumers' index cross-check had never asked about them at all.
        #
        # Same shape as the BINARY_VERSION default-argument bug this file's
        # meta-rule is about: a constant bound once, at definition time.
        sp.add_argument("--index-json-url", default=None,
                        help="the index JSON API to cross-check against "
                             "(default: this project's own, derived from its "
                             "pyproject name). Point it at the index a "
                             "--repository upload actually lands on.")

    c = sub.add_parser("check", help="the gate (default)")
    _gate_flags(c)
    c.set_defaults(fn=cmd_check)
    u = sub.add_parser("publish", help="check, then upload, then record - the only "
                                       "supported way to release")
    _gate_flags(u)
    u.add_argument("--repository", default=None, help="a twine repository alias, e.g. testpypi")
    u.add_argument("--dry-run", action="store_true",
                   help="run the gate and print the upload it authorises, upload nothing")
    u.set_defaults(fn=cmd_publish)
    r = sub.add_parser("record", help="record this version as published (after a successful upload)")
    r.set_defaults(fn=cmd_record)
    s = sub.add_parser("show", help="print the digests, no verdict")
    s.set_defaults(fn=cmd_show)
    args = p.parse_args(argv)
    if args.cmd is None:
        # `check` is the default, and the options in front of it must survive.
        args = p.parse_args(argv + ["check"])

    # WHICH project is being gated, resolved from its own pyproject, before any
    # command runs. Done at the single entry point rather than threaded through
    # every function, so there is no window in which half this module is talking
    # about one package and half about another.
    global DIST_NAME, PKG_NAME, DEFAULT_INDEX_JSON_URL
    try:
        DIST_NAME, PKG_NAME = resolve_project_identity(Path(args.project_root))
    except GateBroken as exc:
        print(f"GATE BROKEN: {exc}", file=sys.stderr)
        return EXIT_BROKEN
    # PEP 503: the index normalises "_" to "-". Building the URL from the raw
    # pyproject name works because PyPI redirects, but every message printed
    # from DIST_NAME then spells the package a way no user typed.
    DIST_NAME = DIST_NAME.replace("_", "-")
    DEFAULT_INDEX_JSON_URL = f"https://pypi.org/pypi/{DIST_NAME}/json"

    try:
        return args.fn(args)
    except LedgerUnavailable as e:
        # The gate has no memory, so it has no opinion. Its own exit code: a
        # caller must never be able to read this as the "nothing published yet"
        # pass it used to be.
        print(f"NO USABLE LEDGER: {e}", file=sys.stderr)
        return EXIT_NO_LEDGER
    except GateBroken as e:
        print(f"GATE BROKEN: {e}", file=sys.stderr)
        return EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
