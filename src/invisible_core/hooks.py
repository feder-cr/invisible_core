"""The pre-push policy, once, for every repository that pins this package.

Written for three repositories - `invisible_core`, `invisible_playwright` and
`invisible_firefox`. The last was deleted on 2026-08-18; the design and the
history below are unchanged by that, because the module was already indifferent
to how many repositories use it - see WHY HERE below.

WHY. The three `.githooks/pre-push` files were 743 lines of shell between them,
and 207 of 211 comparable lines were byte-identical between two of them.
Measured 2026-07-27: eleven behavioural differences separated the three, and
nine were accidental - the signature of copy-paste, where each fix lands in
whichever file happened to be open:

  * `invisible_core` ran no pytest at all, so the one repository the other two
    depend on was the only one where "never push red" was not enforced. It is
    also the repository that pushed red (56b2af2, fourteen tests red on main for
    two hours). CLAUDE.md hard rule 3 names this hook as the thing that stops
    exactly that;
  * `NAME_SKIPPED` was assigned in three places in the core's hook and read in
    none, because the closing summary that reads it only exists in the other two;
  * the core resolved an interpreter into `$python_bin` and then the pasted
    name-scan block called bare `python` anyway;
  * three different ways of locating the same two workbench scripts;
  * the core asked PyPI over curl whether a version existed, which answers a
    weaker question than the gate's own `--verify-index` (present on the index
    versus present AND byte-identical) and answers it with a second source of
    truth.

Two differences were real and are preserved as configuration - whether pytest
runs is NOT one of them, see below - and the rest are now one implementation.

WHY HERE. Same reason `invisible_core.release` lives here: every consumer pins
this package exactly, so a module in it is reachable from all of them, whereas a
script in one repository is reachable from one. That asymmetry is what left the
consumers with no publish gate when 0.4.4 went out built from the wrong tree.

WHAT EACH REPOSITORY DECLARES, in its own pyproject under
`[tool.invisible.hooks]`:

    pytest       = true|false     run the suite before pushing
    pin          = true|false     compare the invisible-core pin (consumers)
    release_tags = ["v"]          tag prefixes that mean "this is a release"

The block is REQUIRED. A missing one is a refusal rather than a default,
because a default here is a policy nobody chose: the wrong one silently skips a
gate, and a skipped gate reads exactly like a passed gate.

THE ONE DELIBERATE BEHAVIOUR CHANGE. `pytest = true` in the core, which
previously ran none. Everything else below preserves what the three hooks
already did, including the two decisions that look inconsistent and are not:

  * a missing PIN checker REFUSES, while a missing NAME word list carries on
    with a warning. The pin checker is a maintainer tool that must be beside
    this checkout and its absence means the workbench moved; the word list
    deliberately lives outside every public repo, so demanding it would leave
    every clone red by default, which is how gates get switched off.

STDIN. git feeds the refs being pushed on stdin, one line per ref, and it can be
read exactly once. Two gates need it, and a `while read` in the first of them
starved the second silently for a day - the scan reported "no commit messages
scanned" on every push, correctly, and with no coverage. It is read once, here.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

__all__ = [
    "main",
    "hook_config",
    "release_tag_in",
    "push_range",
    "GATE_NOTHING_TO_DO",
    "HookConfigError",
]

#: Exit 5 from the publish gate is ALREADY PUBLISHED AND BYTE-IDENTICAL: a
#: no-op, not a refusal, and what re-pushing or back-filling a release tag
#: legitimately produces. The gate gives it its own code precisely so a caller
#: can tell it from "the content moved under an unmoved version", which stays
#: fatal. Treating every non-zero code alike made a correct tag push impossible.
GATE_NOTHING_TO_DO = 5

#: What the pin and name gates are called in the workbench, two levels up from a
#: repository checkout. Not importable and not meant to be: they are maintainer
#: tools, and one of them reads a word list that must never enter a public repo.
_PIN_CHECKER = "sync_core_pin.py"
_NAME_CHECKER = "check_forbidden_names.py"
#: A third, and it is a different question from the name scan rather than more
#: tokens for it. That one asks "does this name somebody else's product"; this
#: one asks "does this describe our own engine from the inside". Its pattern
#: list is itself internal material - it spells the private sampler package and
#: the seed-pool constants - so like the word list it can never live in a public
#: repo, which is why it is looked up in the workbench and skipped when absent.
_DISCLOSURE_CHECKER = "check_internal_disclosure.py"

_DEFAULTS: Dict[str, object] = {"pytest": True, "pin": True, "release_tags": ["v"]}


class HookConfigError(Exception):
    """The repository did not declare a hook policy, so none can be applied."""


def _say(msg: str, *, err: bool = False, out=None) -> None:
    stream = out if out is not None else (sys.stderr if err else sys.stdout)
    print(f"[pre-push] {msg}", file=stream, flush=True)


def hook_config(root: Path) -> Dict[str, object]:
    """`[tool.invisible.hooks]` from the repo's pyproject.

    Raises rather than defaulting when the block is missing: see the module
    docstring. Individual keys DO default, so adding a gate later does not
    require touching every repo's pyproject before the first push can happen.
    """
    pyproject = Path(root) / "pyproject.toml"
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise HookConfigError(f"no pyproject.toml at {pyproject}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise HookConfigError(f"{pyproject} does not parse: {exc}") from exc

    declared = data.get("tool", {}).get("invisible", {}).get("hooks")
    if declared is None:
        raise HookConfigError(
            f"{pyproject} has no [tool.invisible.hooks] block, so this hook "
            f"does not know which gates this repository wants. Declare it - "
            f"pytest / pin / release_tags - rather than letting a default "
            f"decide, because the wrong default silently skips a gate and a "
            f"skipped gate reads exactly like a passed one.")

    unknown = sorted(k for k in declared if k not in _DEFAULTS)
    if unknown:
        # A key nobody reads is a gate the author believes they configured. The
        # permissive direction of that mistake - `pytst = false` leaving pytest
        # silently on, `pins = false` leaving the pin gate silently running -
        # is survivable; the other direction is not, and neither is legible
        # from the outside. Cheaper to refuse and name the typo.
        raise HookConfigError(
            f"[tool.invisible.hooks] declares {', '.join(unknown)}, which "
            f"nothing reads. Known keys: {', '.join(sorted(_DEFAULTS))}. A "
            f"misspelt key is a gate whose author believes it is configured.")

    cfg = dict(_DEFAULTS)
    cfg.update(declared)
    if isinstance(cfg["release_tags"], str):        # a bare "v" is what a human writes
        cfg["release_tags"] = [cfg["release_tags"]]
    return cfg


def read_push_refs(stdin=None) -> str:
    """The refs git is pushing, or "" when the hook is run by hand.

    Guarded on a tty so invoking it manually does not hang on input that will
    never arrive.
    """
    stream = stdin if stdin is not None else sys.stdin
    try:
        if stream is None or stream.isatty():
            return ""
        return stream.read()
    except Exception:
        return ""


def _ref_lines(push_refs: str):
    """(local_sha, remote_ref, remote_sha) per pushed ref, deletions dropped.

    An all-zero LOCAL sha is a branch or tag deletion: it pushes no content, so
    there is nothing to scan and nothing to publish.
    """
    for line in push_refs.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        _local_ref, local_sha, remote_ref, remote_sha = parts[:4]
        if local_sha and set(local_sha) == {"0"}:
            continue
        yield local_sha, remote_ref, remote_sha


def release_tag_in(push_refs: str, prefixes: Sequence[str] = ("v",)) -> str:
    """The first release tag being pushed, or ""."""
    for _local_sha, remote_ref, _remote_sha in _ref_lines(push_refs):
        for prefix in prefixes:
            if remote_ref.startswith(f"refs/tags/{prefix}"):
                return remote_ref
    return ""


#: L'albero vuoto di git. Non e' una costante nostra: e' lo SHA-1 dell'oggetto
#: albero senza voci, identico in ogni repository esistente, ed e' la base che
#: git stesso usa quando "prima non c'era niente".
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _revision_exists(rev: str, repo: Optional[Path]) -> bool:
    if repo is None:
        return False
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", rev],
            capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return False
    return out.returncode == 0


def _commits_not_on_a_remote(local_sha: str, repo: Optional[Path]) -> list:
    """I commit raggiunti da `local_sha` che nessun remoto ha ancora."""
    if repo is None:
        return []
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-list", local_sha, "--not", "--remotes"],
            capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return []
    if out.returncode:
        return []
    return [r for r in out.stdout.split() if r]


def push_range(push_refs: str, repo: Optional[Path] = None) -> str:
    """UN intervallo di revisioni valido per `git diff` e `git rev-list`, o "".

    Il valore di ritorno e' sempre UN SOLO token, e questo e' il punto.

    Prima restituiva `f"{local_sha} --not --remotes"` quando il remoto non
    aveva mai visto il ref: tre token in una stringa sola. Chi la riceveva
    doveva indovinare se spezzarla, e i due consumatori indovinavano diverso -
    il name scanner sopravviveva, il gate della disclosure la passava a
    `git diff` come singola revisione e otteneva `fatal: bad revision`. Il hook
    allora RIFIUTAVA il push, correttamente ma per un errore proprio: misurato
    2026-08-11 spingendo il tag v18.14.0, con la conseguenza che una release
    non poteva partire.

    Il caso che lo scatenava e' quello di sempre per un tag: il ref e' nuovo sul
    remoto (sha remoto tutto zeri) ma i COMMIT sono gia' pubblicati. La risposta
    giusta li' non e' un intervallo strano, e' "non c'e' niente di nuovo da
    leggere", e si dice con "".

    `repo` serve a chiederlo a git invece di dedurlo. Senza, la funzione resta
    quella di prima per il caso normale e non inventa: un intervallo che non
    puo' verificare non lo restituisce.
    """
    for local_sha, _remote_ref, remote_sha in _ref_lines(push_refs):
        if remote_sha and set(remote_sha) == {"0"}:
            nuovi = _commits_not_on_a_remote(local_sha, repo)
            if not nuovi:
                return ""
            piu_vecchio = nuovi[-1]
            if _revision_exists(f"{piu_vecchio}^", repo):
                return f"{piu_vecchio}^..{local_sha}"
            # Il piu' vecchio e' la RADICE, quindi non ha un genitore. La base
            # giusta e' l'albero vuoto - l'hash canonico di git, uguale in ogni
            # repository - che rende "tutto e' nuovo" un intervallo normale
            # invece di un caso speciale. Trovato dal test: senza questo ramo
            # git risponde "ambiguous argument <sha>^".
            return f"{_EMPTY_TREE}..{local_sha}"
        return f"{remote_sha}..{local_sha}"
    return ""


def _subprocess_run(cmd: Sequence[str], cwd: Path) -> int:
    try:
        return subprocess.run(list(cmd), cwd=str(cwd)).returncode
    except FileNotFoundError:
        return 127


Runner = Callable[[Sequence[str], Path], int]


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    root: Optional[Path] = None,
    push_refs: Optional[str] = None,
    run: Optional[Runner] = None,
    env: Optional[Dict[str, str]] = None,
    python: Optional[str] = None,
) -> int:
    """Run the whole pre-push policy. 0 lets the push through, 1 refuses.

    Every collaborator is injectable so the policy can be exercised without
    spawning a test suite or touching a network - the gates that were only ever
    observed printing PASS are the ones that turned out not to be gates.
    """
    root = Path(root if root is not None else os.getcwd()).resolve()
    env = dict(os.environ if env is None else env)
    run = run or _subprocess_run
    py = python or env.get("INVISIBLE_GATE_PYTHON") or sys.executable
    refs = read_push_refs() if push_refs is None else push_refs

    try:
        cfg = hook_config(root)
    except HookConfigError as exc:
        _say(f"REFUSED - {exc}", err=True)
        return 1

    ran: List[str] = []
    skipped: List[str] = []
    workbench = root.parent.parent / "scripts"

    # --- the suite -----------------------------------------------------
    if cfg["pytest"]:
        _say("running the test suite before push...")
        # The default selection is `not slow and not e2e`, identical across
        # every repo since 2026-07-27 (three of them then - invisible_core,
        # invisible_playwright, invisible_firefox; two since the last was
        # deleted 2026-08-18). `integration` deliberately RUNS: it is
        # in-process and fast, and it is what covers the release wiring.
        #
        # This comment said the opposite when it was written, hours earlier -
        # copied from the manager's hook, where `integration` meant "launches
        # the real binary" while meaning "no browser" in the other two. Same
        # word, two contracts, and the copy carried the wrong one into a file
        # that now speaks for every repo that pins this package.
        if run([py, "-m", "pytest", "-q", "--tb=short"], root):
            _say("", err=True)
            _say("TESTS FAILED - push aborted.", err=True)
            _say("Fix the failure, or `git push --no-verify` if you really know "
                 "what you are doing (NEVER for a branch that feeds a release).",
                 err=True)
            return 1
        ran.append("tests")

    # --- the invisible-core pin ----------------------------------------
    # The `invisible-core==` specifier in a consumer's pyproject must name the
    # version the core checkout actually builds. The core's version moves on its
    # own when a new engine is rolled in; the pin does not. No test downstream
    # can see a wrong pin: it only shows up at install time, on someone else's
    # machine. The checker looks at every consumer (`scripts/sync_core_pin.py`'s
    # own CONSUMERS list), so this re-checks the others too - deliberate: when
    # there is more than one, every pin must name the same core, and the cheap
    # moment to notice they do not is before any is pushed.
    if cfg["pin"]:
        setting = env.get("INVISIBLE_PIN_CHECK")
        if setting == "skip":
            _say("WARNING: INVISIBLE_PIN_CHECK=skip. The invisible-core pin was "
                 "NOT compared against the core. If it is stale, every install "
                 "of this package resolves a core it was never tested against, "
                 "and nothing downstream can see it.")
            skipped.append("pin gate")
        else:
            checker = Path(setting) if setting else workbench / _PIN_CHECKER
            # A gate that cannot run is a refusal. It used to be wrapped in a
            # bare `if [ -f ... ]` that skipped with NO output and fell through
            # to a line reading "all tests green - push proceeding": moving the
            # workbench, or pushing from a worktree at another depth, disarmed
            # the gate and printed something indistinguishable from success.
            if not checker.is_file():
                _say("", err=True)
                _say("REFUSED - the invisible-core pin gate is not reachable.", err=True)
                _say(f"  looked for: {checker}", err=True)
                _say(f"  from:       {root}", err=True)
                _say("The workbench moved, or this is a clone at another depth. "
                     "Point INVISIBLE_PIN_CHECK at sync_core_pin.py, or push with "
                     "INVISIBLE_PIN_CHECK=skip to state on the record that the "
                     "pin is going out unchecked.", err=True)
                return 1
            rc = _run_gate_with_its_own_tests(
                checker, "pin", py, root, run,
                args=["--check"],
                require_tests=True,
                fail_msg="the invisible-core pin does not match the core. Fix it "
                         f"with: python {checker}")
            if rc:
                return rc
            ran.append("pin")

    # --- names we do not publish ---------------------------------------
    # Naming a protection vendor or a target site in a public repo is a legal
    # and credibility problem, and no test inside these packages can cover it:
    # the scanner needs a word list that deliberately lives outside all of them.
    #
    # Three cases, kept apart on purpose. An earlier cut had two and refused
    # whenever the scanner was not at the default path, which blocked every
    # clone of the PUBLIC repo on every push with no way to comply except
    # switching the gate off.
    setting = env.get("INVISIBLE_NAME_CHECK")
    if setting == "skip":
        _say("WARNING: INVISIBLE_NAME_CHECK=skip. Nothing checked this push for "
             "vendor or target-site names. If one is in the diff it is public "
             "the moment this lands, and a force-push does not remove it - "
             "GitHub keeps the object reachable by SHA.")
        skipped.append("name scan")
    else:
        scanner = Path(setting) if setting else workbench / _NAME_CHECKER
        if setting and not scanner.is_file():
            # Configured but unreachable: somebody meant this to run.
            _say("", err=True)
            _say("REFUSED - INVISIBLE_NAME_CHECK is set but points at nothing.", err=True)
            _say(f"  looked for: {scanner}", err=True)
            _say("Fix the path, or set INVISIBLE_NAME_CHECK=skip to state on the "
                 "record that this push is going out unscanned.", err=True)
            return 1
        if not scanner.is_file():
            _say("no forbidden-name word list here, so nothing scanned this push.")
            _say("(that scan is a maintainer gate; it lives outside this repo.)")
            skipped.append("name scan")
        else:
            rng = push_range(refs, root)
            if rng:
                _say(f"scanning files + the commit messages in {rng} ...")
            else:
                _say("scanning files for names we do not publish.")
                _say("NOTE: no push range on stdin, so COMMIT MESSAGES were not "
                     "scanned. A message cannot be reworded once it is pushed.")
            rc = _run_gate_with_its_own_tests(
                scanner, "name scanner", py, root, run,
                args=[".", "--range", rng] if rng else ["."],
                require_tests=True,
                fail_msg="see above. The offending files are named; the tokens "
                         "are not, so this log is safe to paste.")
            if rc:
                return rc
            ran.append("names")

    # --- our own internals, which the word list cannot see ---------------
    # Same shape as the block above and a different question. 296 files and
    # 40,367 lines of public documentation went out on 2026-08-05 with the name
    # scanner as their only gate, and that scanner reads 23 vendor names: a page
    # can be clean on every one of them and still print the private sampler's
    # package name or a line number in the patched tree. Diff-scoped, so it asks
    # whether THIS push discloses something new rather than whether the whole
    # corpus is perfect - a gate that fails on a long-standing phrase is a gate
    # people learn to skip.
    setting = env.get("INVISIBLE_DISCLOSURE_CHECK")
    if setting == "skip":
        _say("WARNING: INVISIBLE_DISCLOSURE_CHECK=skip. Nothing checked this "
             "push for internal symbols, engine source lines or local paths.")
        skipped.append("disclosure scan")
    else:
        scanner = Path(setting) if setting else workbench / _DISCLOSURE_CHECKER
        if setting and not scanner.is_file():
            _say("", err=True)
            _say("REFUSED - INVISIBLE_DISCLOSURE_CHECK is set but points at "
                 "nothing.", err=True)
            _say(f"  looked for: {scanner}", err=True)
            _say("Fix the path, or set INVISIBLE_DISCLOSURE_CHECK=skip to state "
                 "on the record that this push is going out unscanned.", err=True)
            return 1
        if not scanner.is_file():
            _say("no internal-disclosure scanner here, so nothing scanned this "
                 "push for our own internals.")
            _say("(that scan is a maintainer gate; it lives outside this repo.)")
            skipped.append("disclosure scan")
        else:
            rng = push_range(refs, root)
            rc = _run_gate_with_its_own_tests(
                scanner, "internal-disclosure", py, root,
                run, args=[".", "--range", rng] if rng else ["."],
                require_tests=True,
                fail_msg="see above. The lines are named and so is what matched, "
                         "because both are already in the diff you are pushing.")
            if rc:
                return rc
            ran.append("internals")

    # --- the publish gate, on release tags only ------------------------
    # Only release tags: the gate builds the project twice, and a hook that
    # costs a minute on every push is a hook people delete.
    tag = release_tag_in(refs, cfg["release_tags"])
    if tag:
        _say(f"{tag} is a release tag, running the publish gate...")
        # --project-root is a TOP-LEVEL option, before the subcommand. Putting
        # it after made argparse exit 2, which the shell version read as a
        # refusal - so it printed REFUSED without the gate ever having run, and
        # the first "verification" of that wiring was itself wrong.
        rc = run([py, "-m", "invisible_core.release", "--project-root", str(root),
                  "check", "--verify-index"], root)
        if rc == GATE_NOTHING_TO_DO:
            _say("the index already has this version, byte-identical. Nothing "
                 "to publish; the publish workflow will no-op the same way.")
            ran.append("publish gate (no-op)")
        elif rc:
            _say("", err=True)
            _say(f"REFUSED (gate exit {rc}) - see above. A version whose content "
                 f"moved needs a NEW version number: a PyPI filename is never "
                 f"re-uploaded, so a wrong artifact stays wrong forever.", err=True)
            return 1
        else:
            ran.append("publish gate")

    # --- say exactly what ran, and no more -----------------------------
    _say("push proceeding - " + (", ".join(ran) if ran else "NOTHING was checked")
         + (f" (SKIPPED: {', '.join(skipped)})" if skipped else ""))
    return 0


def _run_gate_with_its_own_tests(
    checker: Path,
    label: str,
    py: str,
    root: Path,
    run: Runner,
    *,
    args: Sequence[str],
    require_tests: bool,
    fail_msg: str,
) -> int:
    """Run a workbench gate, but only after its own known-bad cases pass.

    A gate that has only ever printed PASS is not a gate. Both of these had a
    period of being exactly that: the name scanner was untested until
    2026-07-27 and was returning a clean verdict over three of the four surfaces
    a public repo publishes - commit messages among them, which is where ten of
    our own names went out. Nothing else in any repo that pins this package runs
    these cases, so without this they are a runbook note wearing a gate's
    clothes.
    """
    own_tests = checker.with_name(f"test_{checker.name}")
    if not own_tests.is_file():
        if require_tests:
            _say("", err=True)
            _say(f"REFUSED - {own_tests} is missing, so the {label} gate is "
                 f"unverified. A gate that has only ever printed PASS is not a "
                 f"gate; do not trust this one until its own cases run.", err=True)
            return 1
    else:
        _say(f"running the {label} gate's own known-bad cases...")
        if run([py, str(own_tests)], root):
            _say("", err=True)
            _say(f"REFUSED - the {label} gate's own cases are RED, so its verdict "
                 f"on this push would mean nothing. Fix {own_tests} first.", err=True)
            return 1

    if run([py, str(checker), *args], root):
        _say("", err=True)
        _say(f"REFUSED - {fail_msg}", err=True)
        return 1
    return 0


# ======================================================================
# Arming a clone
# ======================================================================
# git does not version `.git/hooks`, so "we have a pre-push hook" is usually
# false on every machine except the one it was written on. `core.hooksPath` is
# the one setting that makes a checked-in hook directory real.
#
# This lived in `invisible_core/scripts/install_hooks.py` and therefore existed
# for ONE of the three repos - the other two shipped a hook that nothing armed
# and nothing checked was armed, so a fresh clone of either had no gates at all
# and no way to notice. Same asymmetry, same fix, as the publish gate.

HOOKS_DIR_NAME = ".githooks"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True)


def hooks_path(root: Path) -> str:
    """This clone's `core.hooksPath`, or "" if unset / not a checkout."""
    p = _git(root, "config", "--get", "core.hooksPath")
    return p.stdout.strip() if p.returncode == 0 else ""


def is_armed(root: Path) -> bool:
    cur = hooks_path(root).replace("\\", "/").rstrip("/")
    wanted = str(Path(root) / HOOKS_DIR_NAME).replace("\\", "/")
    return cur in (HOOKS_DIR_NAME, f"./{HOOKS_DIR_NAME}", wanted)


def install_main(argv: Optional[Sequence[str]] = None,
                 *, root: Optional[Path] = None) -> int:
    """Arm this clone. 0 armed or already armed, 1 --check and not armed, 2 broken.

    Refuses to overwrite a hooksPath somebody else chose: a repo with its own
    hook framework is not ours to hijack, and replacing it would disarm THEIR
    gates to arm ours.
    """
    import argparse

    root = Path(root if root is not None else os.getcwd()).resolve()
    p = argparse.ArgumentParser(prog="install_hooks.py",
                                description="arm this clone's pre-push gate")
    p.add_argument("--check", action="store_true",
                   help="report only; exit 1 if the hooks are not installed")
    args = p.parse_args(argv)

    hook = root / HOOKS_DIR_NAME / "pre-push"
    if not hook.exists():
        print(f"error: {hook} does not exist", file=sys.stderr)
        return 2
    if _git(root, "rev-parse", "--git-dir").returncode != 0:
        print(f"error: {root} is not a git checkout, so there are no hooks to "
              f"install here.", file=sys.stderr)
        return 2

    cur = hooks_path(root)
    if is_armed(root):
        print(f"hooks already installed: core.hooksPath = {cur}")
        return 0
    if args.check:
        print("HOOKS NOT INSTALLED: the pre-push gates would not run.", file=sys.stderr)
        print(f"  core.hooksPath = {cur or '(unset)'}, wanted {HOOKS_DIR_NAME}",
              file=sys.stderr)
        print("  fix: python scripts/install_hooks.py", file=sys.stderr)
        return 1
    if cur:
        print(f"refusing to overwrite core.hooksPath = {cur}", file=sys.stderr)
        print(f"  Another hook framework owns this clone. Add "
              f"{HOOKS_DIR_NAME}/pre-push to it by hand rather than letting this "
              f"script disarm it.", file=sys.stderr)
        return 2

    r = _git(root, "config", "core.hooksPath", HOOKS_DIR_NAME)
    if r.returncode != 0:
        print(f"error: git config failed: {r.stderr.strip()}", file=sys.stderr)
        return 2

    # The executable bit that matters is the TRACKED one, not this. git refuses
    # to run a hook it does not consider executable - it says so and exits 0, so
    # the push succeeds with every gate inert - and the mode it reads belongs to
    # the index. Chmod-ing the working tree never changed it and never could,
    # which is why all three hooks sat at 100644 through this same call. Kept
    # because a fresh POSIX checkout still needs the bit on disk; the index side
    # is asserted by invisible_core.testing.assert_hook_is_executable.
    import stat
    try:
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    print(f"core.hooksPath = {HOOKS_DIR_NAME}")
    print("the pre-push gates are armed for this clone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
