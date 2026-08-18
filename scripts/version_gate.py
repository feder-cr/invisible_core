#!/usr/bin/env python3
"""Back-compat shim. The gate is `invisible_core.release` now.

Kept because the pre-push hooks, the publish workflow and the docs all name this
path, and a release-critical entry point is the last thing to break for tidiness.
It moved into the package so every CONSUMER can run the same gate (two when
this was written, one since invisible_firefox was deleted 2026-08-18): a
consumer pins the core exactly, so importing it costs nothing, while a script
in this repo was reachable only from this repo - which is why they had none
when 0.4.4 went out wrong.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from invisible_core.release import main  # noqa: E402

if __name__ == "__main__":
    # This repo's root, explicitly: the module defaults to the current directory,
    # and a hook may well invoke this from somewhere else.
    argv = sys.argv[1:]
    if not any(a == "--project-root" or a.startswith("--project-root=") for a in argv):
        argv = ["--project-root", str(Path(__file__).resolve().parents[1])] + argv
    raise SystemExit(main(argv))
