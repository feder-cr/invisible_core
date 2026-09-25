"""Download and launch the sealed engine the way an MSIX host does.

Run by the `msix-host` job of ci.yml INSIDE a registered MSIX package, with
the BASE interpreter as the package's first process (see below for why). Not a
pytest file: it needs a package context no test runner has.

WHY THIS EXISTS. Claude Desktop on Windows is an MSIX package, and so is the
Microsoft Store Python. A packaged app's processes see their writes under
AppData at the AppData path while the files really land in the package's own
LocalCache folder. Our engine was downloaded there, and a launch from the
AppData path failed with ERROR_SXS_CANT_GEN_ACTCTX (14001), because Windows
resolves the `mozglue` assembly firefox.exe declares against the directory as
it really is. Nobody could start the MCP server from Claude Desktop on Windows,
from the first release until core 34.30.0, and no test ever ran inside the
host the product is used from. Reported as invisible_playwright discussion
#256 (2026-09-25); issue #22 in May was the same failure.

THREE VERDICTS, written to the JSON file named by argv[1]:

  PASS            the path the process sees fails with 14001 (the bench still
                  reproduces the defect) AND the path ensure_binary returns
                  starts the engine.
  FAIL            ensure_binary's path does not start the engine.
  BENCH_NOT_LIVE  this process has no package identity, or the cache was not
                  redirected, or the old path did NOT fail: the run proves
                  nothing either way and is refused, not passed.

Three traps, measured on 2026-09-25, that each produced a false green before
they were understood:
  * only the FIRST process of Invoke-CommandInDesktopPackage has the package
    identity; children of cmd.exe leave it, so this file must be run by the
    python.exe given as -Command, and it checks its own identity;
  * a venv's python.exe starts the base interpreter as a child, which leaves the
    package the same way: the job runs the base interpreter;
  * a write under a folder that already exists at the real AppData path is not
    redirected, so the cache is a folder that does not exist yet.
"""
from __future__ import annotations

import ctypes
import json
import os
import platform
import subprocess
import sys
import traceback
from pathlib import Path

OUT = Path(sys.argv[1])
APPMODEL_ERROR_NO_PACKAGE = 15700
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_SXS_CANT_GEN_ACTCTX = 14001

report: dict = {"verdict": None, "notes": []}


def finish(verdict: str, **fields) -> None:
    report["verdict"] = verdict
    report.update(fields)
    OUT.write_bytes(json.dumps(report, indent=2, sort_keys=True).encode("utf-8"))
    sys.exit(0)


def has_package_identity() -> bool:
    length = ctypes.c_uint32(0)
    rc = ctypes.WinDLL("kernel32").GetCurrentPackageFullName(ctypes.byref(length), None)
    report["notes"].append(f"GetCurrentPackageFullName returned {rc}")
    return rc == ERROR_INSUFFICIENT_BUFFER


def launch(exe: Path) -> dict:
    """firefox.exe --version: enough to cross CreateProcessW and the activation
    context, which is exactly where the defect lives, without a window."""
    try:
        done = subprocess.run([str(exe), "--version"], capture_output=True,
                              text=True, timeout=180)
    except OSError as exc:
        return {"started": False, "winerror": getattr(exc, "winerror", None),
                "error": str(exc)}
    return {"started": done.returncode == 0 and "Firefox" in done.stdout,
            "returncode": done.returncode, "stdout": done.stdout.strip()[:200],
            "stderr": done.stderr.strip()[:400]}


def main() -> None:
    if not has_package_identity():
        finish("BENCH_NOT_LIVE", reason="this process has no package identity, so "
               "nothing it writes is redirected")

    cache = Path(os.environ["LOCALAPPDATA"]) / f"invisible-msix-gate-{os.getpid()}"
    if cache.exists():
        finish("BENCH_NOT_LIVE", reason=f"{cache} already exists, and a folder that "
               "exists at the real path is not redirected")
    os.environ["INVISIBLE_PLAYWRIGHT_CACHE_DIR"] = str(cache)

    from invisible_core import __version__, ensure_binary
    from invisible_core.download import cache_dir_for_seal
    from invisible_core.seal import active_seal

    seal = active_seal()
    report["core"] = __version__
    report["seal"] = seal.describe()
    entry_rel = seal.asset_for(sys.platform, platform.machine()).entry_rel
    returned = ensure_binary(seal=seal)
    seen = cache_dir_for_seal(seal) / entry_rel
    report["returned_path"] = str(returned)
    report["seen_path"] = str(seen)

    real_of_seen = os.path.normcase(os.path.realpath(seen))
    if real_of_seen == os.path.normcase(str(seen)):
        finish("BENCH_NOT_LIVE", reason="the cache was not redirected: the path this "
               "process sees is where the files really are, so the MSIX case did "
               "not happen")

    report["old_path_launch"] = old = launch(seen)
    report["returned_path_launch"] = new = launch(returned)
    if not new["started"]:
        finish("FAIL", reason="the engine does not start from the path ensure_binary "
               "returns, under an MSIX host")
    if old.get("winerror") != ERROR_SXS_CANT_GEN_ACTCTX:
        finish("BENCH_NOT_LIVE", reason="the path this process sees did not fail with "
               "14001, so this runner no longer reproduces the defect and a PASS "
               "here would prove nothing")
    finish("PASS")


try:
    main()
except SystemExit:
    raise
except BaseException:
    finish("FAIL", reason="the probe raised", traceback=traceback.format_exc()[-3000:])
