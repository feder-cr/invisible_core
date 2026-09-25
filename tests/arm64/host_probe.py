"""An ARM64 Python on Windows gets the engine, and the engine starts.

Run by the `arm64-host` job of ci.yml on a `windows-11-arm` runner. Not a
pytest file: it needs that host. It measures the claim `EMULATED_LEGS` in
seal.py makes - that Windows 11 on ARM runs the x86_64 engine - and refuses
to pass on a host where the claim was not put to the test.

Exit codes: 0 PASS, 1 FAIL, 2 BENCH_NOT_LIVE (this Python is not ARM64, so a
pass here would say nothing about the host the declaration is for).
"""
from __future__ import annotations

import platform
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    machine = platform.machine()
    print(f"python {sys.version.split()[0]} machine={machine}")
    if machine.upper() != "ARM64":
        print("BENCH_NOT_LIVE: this interpreter is not ARM64")
        return 2

    from invisible_core import ensure_binary
    from invisible_core.seal import active_seal
    asset = active_seal().asset_for(sys.platform, machine)
    print(f"asset for this host: {asset.name} ({asset.arch})")
    exe = ensure_binary()
    print(f"ensure_binary -> {exe}")

    done = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=180)
    print(f"--version: rc={done.returncode} {done.stdout.strip()!r} {done.stderr.strip()[:300]!r}")
    if done.returncode != 0 or "Firefox" not in done.stdout:
        print("FAIL: the engine did not start under emulation")
        return 1

    with tempfile.TemporaryDirectory() as td:
        shot = Path(td) / "shot.png"
        profile = Path(td) / "profile"
        profile.mkdir()
        run = subprocess.run([str(exe), "-headless", "-no-remote", "-profile", str(profile),
                              "-screenshot", str(shot), "data:text/html,<h1>arm64</h1>"],
                             capture_output=True, text=True, timeout=300)
        size = shot.stat().st_size if shot.exists() else 0
        print(f"headless screenshot: rc={run.returncode} png={size} bytes {run.stderr.strip()[-300:]!r}")
        if size == 0:
            print("FAIL: the engine started but did not render a page")
            return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
