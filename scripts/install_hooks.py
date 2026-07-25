"""Point this clone's git hooks at .githooks/, so the publish gate is armed.

Git does not version .git/hooks, which is why "we have a pre-push hook" is
usually false on every machine except the one it was written on. `core.hooksPath`
is the one setting that makes a checked-in hook directory real, and it is a
single per-clone config write.

Nobody has to remember to run this: tests/test_release_wiring.py fails in a git
checkout whose hooksPath is not set, and prints this command. The test suite is
the reminder, and it is a reminder that runs on its own.

  python scripts/install_hooks.py            # set it
  python scripts/install_hooks.py --check    # exit 1 if it is not set

Nothing here touches the working tree, and it refuses to overwrite a hooksPath
someone else already chose - a repo with its own hook framework is not ours to
hijack, and silently replacing it would disarm THEIR gates to arm ours.
"""
from __future__ import annotations

import argparse
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".githooks"
WANTED = ".githooks"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                          capture_output=True, text=True)


def current_hooks_path() -> str:
    p = _git("config", "--get", "core.hooksPath")
    return p.stdout.strip() if p.returncode == 0 else ""


def is_installed() -> bool:
    cur = current_hooks_path().replace("\\", "/").rstrip("/")
    return cur in (WANTED, f"./{WANTED}", str(HOOKS_DIR).replace("\\", "/"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="install_hooks.py", description=__doc__.splitlines()[0])
    p.add_argument("--check", action="store_true",
                   help="report only; exit 1 if the hooks are not installed")
    a = p.parse_args(argv)

    if not (HOOKS_DIR / "pre-push").exists():
        print(f"error: {HOOKS_DIR / 'pre-push'} does not exist", file=sys.stderr)
        return 2
    if _git("rev-parse", "--git-dir").returncode != 0:
        print(f"error: {REPO_ROOT} is not a git checkout, so there are no hooks to "
              f"install here.", file=sys.stderr)
        return 2

    cur = current_hooks_path()
    if is_installed():
        print(f"hooks already installed: core.hooksPath = {cur}")
        return 0
    if a.check:
        print("HOOKS NOT INSTALLED: the pre-push publish gate would not run.",
              file=sys.stderr)
        print(f"  core.hooksPath = {cur or '(unset)'}, wanted {WANTED}", file=sys.stderr)
        print("  fix: python scripts/install_hooks.py", file=sys.stderr)
        return 1
    if cur:
        print(f"refusing to overwrite core.hooksPath = {cur}", file=sys.stderr)
        print(f"  Another hook framework owns this clone. Add {WANTED}/pre-push to it "
              f"by hand rather than letting this script disarm it.", file=sys.stderr)
        return 2

    r = _git("config", "core.hooksPath", WANTED)
    if r.returncode != 0:
        print(f"error: git config failed: {r.stderr.strip()}", file=sys.stderr)
        return 2

    # git only runs a hook it considers executable. On Windows the bit is
    # cosmetic, on POSIX it is the difference between a gate and a text file.
    hook = HOOKS_DIR / "pre-push"
    try:
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    print(f"core.hooksPath = {WANTED}")
    print("the pre-push publish gate is armed for this clone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
