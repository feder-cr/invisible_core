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

__all__ = ["tracked_file_mode", "assert_hook_is_executable"]


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
