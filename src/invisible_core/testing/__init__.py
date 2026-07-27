"""Test support shared by the packages that pin this one.

WHY IT SHIPS. `invisible_playwright` and `invisible_firefox` are released
independently and both pin `invisible-core==` exactly, so this is the only place
they can share anything. Before it existed the same helpers were written four
times: the throwaway-venv harness twice byte-identically (`invisible_core` and
`invisible_firefox` `test_user_install_e2e.py`), and the stub-core subprocess
harness four times, twice inside one repo. Four copies means four acceptance
sets, which is the defect the pin parser had already been through.

MAINTAINER-FACING. Nothing here is part of the product's public API and nothing
in `invisible_core/` imports it. It carries no third-party dependency, so it
costs a wheel almost nothing; that is the price of the consumers being able to
import it at all.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

__all__ = ["tracked_file_mode", "assert_hook_is_executable",
           "assert_pre_push_policy_is_wired", "assert_hooks_are_armed"]

#: Tokens whose presence in a `.githooks/pre-push` means the stub has started
#: deciding things again. The three hooks were 743 lines of shell that drifted
#: nine accidental ways; the stub is allowed to find an interpreter and hand
#: over stdin, and nothing else.
_POLICY_TOKENS = ("pytest", "sync_core_pin", "check_forbidden_names",
                  "version_gate", "verify-index", "refs/tags")


def tracked_file_mode(repo_root: Path, rel_path: str) -> str | None:
    """The mode git has recorded in the INDEX, or None outside a checkout.

    The index, not the filesystem. `os.access(X_OK)` is meaningless on Windows
    and would have returned True throughout the bug below.
    """
    out = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-s", rel_path],
        capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.split()[0]


def assert_hook_is_executable(repo_root: Path,
                              rel_path: str = ".githooks/pre-push") -> None:
    """Fail if git will silently ignore the hook.

    Measured 2026-07-27: tracked `100644` in all three repos. git refuses to run
    a non-executable hook - it prints "the '.githooks/pre-push' hook was ignored
    because it's not set as executable" and exits 0, so the push succeeds and
    every gate behind the hook is inert. Reproduced under WSL: mode 644 gives
    `push rc=0` with the hook never running; 755 gives a refusal.

    It survived because `core.fileMode` is false on Windows, where this is
    developed. The mode belongs to the index, so chmod-ing the working tree -
    which `install_hooks.py` did, inside a silent `except OSError: pass` - never
    fixed it and never could. A fresh clone inherits the tracked mode, so every
    clone repeated it.

    Raises nothing outside a git checkout: an installed copy has no hook to
    check, and failing there would make `pytest` red for someone who merely
    installed the package.
    """
    mode = tracked_file_mode(repo_root, rel_path)
    if mode is None:
        return
    assert mode == "100755", (
        f"{rel_path} is tracked as {mode}, so git silently ignores it on every "
        f"clone where core.fileMode is honoured - which is every POSIX clone, "
        f"including CI and WSL. Fix with:\n"
        f"  git update-index --chmod=+x {rel_path}\n"
        f"and commit. Chmod-ing the working tree does not change the tracked mode.")


def assert_pre_push_policy_is_wired(repo_root: Path,
                                    rel_path: str = ".githooks/pre-push") -> None:
    """Fail if a release tag could be pushed here without reaching the gate.

    The property, not the prose. The version of this that lived in each
    consumer read the hook's TEXT for `invisible_core.release` and for a tag
    pattern, which made it a copy of the shell it was checking: it went red the
    day the hook started matching tags with awk instead of a glob, for a hook
    that was correct, and it went red again the day the policy moved out of the
    shell entirely. Both times the property was untouched.

    So the policy is driven instead, in-process, with a recording runner: push
    a release tag, and the gate must be invoked FOR THIS PROJECT; push a branch,
    and it must not - it builds the project twice, and a hook that costs a
    minute on every push is a hook people delete.

    The pin and name gates are switched off here on purpose. They are maintainer
    tools living outside every published repo, this is a claim about the publish
    gate, and a helper that needed the workbench present would skip itself in
    exactly the clone where it matters.
    """
    from .. import hooks

    hook = Path(repo_root) / rel_path
    if not hook.is_file():
        return          # an installed copy has no hook; nothing to check

    body = "\n".join(l for l in hook.read_text(encoding="utf-8").splitlines()
                     if not l.lstrip().startswith("#"))
    assert "invisible_core.hooks" in body, (
        f"{rel_path} does not hand over to the shared policy, so this repo has "
        f"its own copy of the pre-push rules again")
    for token in _POLICY_TOKENS:
        assert token not in body, (
            f"{rel_path} decides something about {token!r}. That decision now "
            f"exists in three places and will be fixed in one of them.")

    # git must never hand a shell script to a POSIX shell with CRLF endings:
    # `/bin/sh^M: bad interpreter` is how a gate stops running on a machine
    # where nothing else looks wrong. These repos are developed on Windows with
    # core.autocrlf=true, so the default was right only because the index
    # happened to hold LF. Asked of `git check-attr`, which is what git will
    # actually apply, rather than of a .gitattributes line, which is one
    # spelling of it.
    eol = subprocess.run(
        ["git", "-C", str(repo_root), "check-attr", "eol", "--", rel_path],
        capture_output=True, text=True)
    if eol.returncode == 0 and eol.stdout.strip():
        assert eol.stdout.strip().endswith("eol: lf"), (
            f"{rel_path} is not pinned to LF ({eol.stdout.strip()}), so a clone "
            f"with core.autocrlf=true gets a shell script with CRLF endings and "
            f"the hook dies on its own shebang.\n"
            f"    add `.githooks/** text eol=lf` to .gitattributes")

    cfg = hooks.hook_config(Path(repo_root))
    calls: list[list[str]] = []

    def record(cmd, cwd):
        calls.append([str(c) for c in cmd])
        return 0

    off = {"INVISIBLE_PIN_CHECK": "skip", "INVISIBLE_NAME_CHECK": "skip"}
    tag = f"refs/tags/{cfg['release_tags'][0]}9.9.9"

    hooks.main(root=Path(repo_root), run=record, env=dict(off), python="PY",
               push_refs=f"x NEW {tag} 0000\n")
    gate = [c for c in calls if "invisible_core.release" in c]
    assert gate, (
        f"pushing {tag} did not run the publish gate, so a release tag can be "
        f"pushed - and published - with no check at all")
    assert str(Path(repo_root).resolve()) in gate[0], (
        f"the gate ran against {gate[0]!r} rather than this project. One gate, "
        f"three projects: pointed at the wrong one it passes every time.")

    calls.clear()
    hooks.main(root=Path(repo_root), run=record, env=dict(off), python="PY",
               push_refs="refs/heads/main NEW refs/heads/main OLD\n")
    assert not [c for c in calls if "invisible_core.release" in c], (
        "an ordinary branch push runs the publish gate, which builds the "
        "project twice; that is the cost that gets a hook deleted")


def assert_hooks_are_armed(repo_root: Path) -> None:
    """Fail if this clone would push with every gate inert.

    git does not version `.git/hooks`, so a checked-in hook directory does
    nothing until `core.hooksPath` points at it, and git will not do that for
    you. Two of the three repos had no way to notice: the installer and the
    reminder both lived in the core, so a fresh clone of either consumer was
    ungated and silent about it.

    Running the suite is the one thing a developer does constantly, so the suite
    is where the reminder belongs. Skipped outside a git checkout: an sdist or a
    CI job that unpacked a tarball has no hooks to arm and no push to make.
    """
    from .. import hooks

    root = Path(repo_root)
    if subprocess.run(["git", "-C", str(root), "rev-parse", "--git-dir"],
                      capture_output=True).returncode != 0:
        return
    assert hooks.is_armed(root), (
        f"core.hooksPath = {hooks.hooks_path(root) or '(unset)'} in {root}, so "
        f"the pre-push gates are not armed in this clone: tests, the core pin, "
        f"the forbidden-name scan and the publish gate would all be skipped, "
        f"and the push would look exactly like a checked one.\n"
        f"    python scripts/install_hooks.py")
