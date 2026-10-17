"""The pin repair, end to end, through the real installer of each environment.

THE FAILURE, MEASURED 2026-10-17. In a project `uv init` + `uv add
invisible-playwright` made (uv 0.12.10, invisible-playwright 0.37.0), with the
core one release off its pin, the import ran
`python -m pip install --force-reinstall "invisible-core==49.44.0"`, which died
with `No module named pip`, and the ImportError told the user to
`Run: pip install "invisible-core==49.44.0"`. A venv uv makes has no pip. Every
user who followed a README that says uv was in that environment.

WHAT THIS PROVES, in both directions, with real installers and the real index:

  * a venv uv built, with no pip in it (asserted: that is what made the old
    repair fail, so a bench without it would prove nothing), is repaired by
    `uv pip install --python <it> --force-reinstall`, found on PATH;
  * a venv pip built is still repaired by its own `-m pip`, as it always was,
    both with the pip the venv is seeded with and with pip 23.2.1: on Windows
    that version refused the old `--force-reinstall` with WinError 5 over a
    psutil the process had loaded (23.3.2 and later do not), so it is the one
    that makes this half fail if the repair goes back to reinstalling the
    dependencies. On a runner's current pip that mistake would pass.

The core under test is THIS checkout, built into a wheel and installed into
each venv. The consumer is a one-module stub, installed by the same tool with
`--no-deps`, so the INSTALLER record is the one that tool really writes and the
pin is really off: the stub pins the last version the ledger says was
published, and this checkout is the next one. The repair then has to fetch that
version from the index and pick it up in the same process.

Marked `e2e`: it builds a wheel and two venvs and reaches the index. It runs in
ci.yml's `repair-through-the-installer` job, on Linux and Windows, with uv
installed by its own installer.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[1]
STUB = "stub-consumer"

#: What must not reach the children: the suite's own PYTHONPATH points at this
#: checkout's src (conftest.py), and a child that imports from there is testing
#: the checkout instead of the venv; the autofix switches would turn the repair
#: off or mark it as already tried.
_HOSTILE = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "INVISIBLE_CORE_AUTOFIX",
            "INVISIBLE_CORE_AUTOFIX_ATTEMPTED", "INVISIBLE_CORE_PIN", "UV")


def _env() -> dict:
    env = dict(os.environ)
    for k in _HOSTILE:
        env.pop(k, None)
    return env


def _run(cmd, *, timeout=900, cwd=None):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       timeout=timeout, env=_env(), cwd=cwd)
    assert r.returncode == 0, (
        f"{' '.join(map(str, cmd))} exited {r.returncode}\n"
        f"--- stdout ---\n{r.stdout[-3000:]}\n--- stderr ---\n{r.stderr[-3000:]}")
    return r


def _uv() -> str:
    found = shutil.which("uv")
    if not found:
        pytest.fail("uv is not on PATH: this test builds the environment the README "
                    "makes, and the CI job installs uv with its own installer")
    return found


def _python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _published() -> str:
    """The last version the ledger records, which is the one the index serves
    and the one this checkout is not."""
    released = json.loads((ROOT / "PUBLISHED.json").read_text(encoding="utf-8"))["released"]
    return released[-1]["version"]


def _stub_wheel(where: Path, pin: str) -> Path:
    """A one-module consumer that pins invisible-core and runs the core's
    import-time check the way every client's `__init__` does."""
    name = "stub_consumer-1.0.0"
    init = (
        "import sys\n"
        "_PRE = any(n == 'invisible_core' or n.startswith('invisible_core.')\n"
        "           for n in sys.modules)\n"
        "from invisible_core.pin import enforce_core_pin\n"
        f"enforce_core_pin({STUB!r}, core_preimported=_PRE)\n")
    metadata = (f"Metadata-Version: 2.1\nName: {STUB}\nVersion: 1.0.0\n"
                f"Requires-Dist: invisible-core=={pin}\n")
    wheel = "Wheel-Version: 1.0\nGenerator: hand\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    files = {
        "stub_consumer/__init__.py": init,
        f"{name}.dist-info/METADATA": metadata,
        f"{name}.dist-info/WHEEL": wheel,
    }
    record = "".join(f"{p},,\n" for p in files) + f"{name}.dist-info/RECORD,,\n"
    files[f"{name}.dist-info/RECORD"] = record
    path = where / f"{name}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        for p, text in files.items():
            z.writestr(p, text)
    return path


@pytest.fixture(scope="module")
def wheels(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("wheels")
    _run([_uv(), "build", "--wheel", "--out-dir", out, ROOT], timeout=900)
    core = sorted(out.glob("invisible_core-*.whl"))
    assert len(core) == 1, core
    return {"core": core[0], "stub": _stub_wheel(out, _published())}


def _repair(py: Path) -> subprocess.CompletedProcess:
    """Import the stub in the venv: the repair runs, then report what runs."""
    return subprocess.run(
        [str(py), "-c",
         "import stub_consumer, invisible_core; print('RUNS', invisible_core.__version__)"],
        capture_output=True, text=True, timeout=900, env=_env())


def test_a_venv_uv_built_is_repaired_through_uv(tmp_path, wheels):
    uv = _uv()
    venv = tmp_path / "uv-venv"
    _run([uv, "venv", "--python", sys.executable, venv], timeout=300)
    py = _python(venv)
    _run([uv, "pip", "install", "--python", py, wheels["core"]])
    _run([uv, "pip", "install", "--python", py, "--no-deps", wheels["stub"]])

    # The bench is live only if this venv is what made the old repair fail.
    probe = _run([py, "-c", "import importlib.util as u, importlib.metadata as m; "
                  "print(u.find_spec('pip') is None, "
                  "m.distribution('invisible-core').read_text('INSTALLER').strip())"])
    assert probe.stdout.split() == ["True", "uv"], probe.stdout

    r = _repair(py)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"RUNS {_published()}" in r.stdout, r.stdout + r.stderr
    assert "repairing it now: uv pip install --python" in r.stderr, r.stderr
    assert "-m pip install" not in r.stderr, r.stderr


@pytest.mark.parametrize("pip", ["seeded", "23.2.1"])
def test_a_venv_pip_built_is_still_repaired_through_pip(tmp_path, wheels, pip):
    venv = tmp_path / "pip-venv"
    _run([sys.executable, "-m", "venv", venv], timeout=300)
    py = _python(venv)
    if pip != "seeded":
        _run([py, "-m", "pip", "install", "--quiet", f"pip=={pip}"], timeout=300)
    _run([py, "-m", "pip", "install", "--quiet", wheels["core"]])
    _run([py, "-m", "pip", "install", "--quiet", "--no-deps", wheels["stub"]])

    probe = _run([py, "-c", "import importlib.metadata as m; "
                  "print(m.distribution('invisible-core').read_text('INSTALLER').strip())"])
    assert probe.stdout.strip() == "pip", probe.stdout

    r = _repair(py)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"RUNS {_published()}" in r.stdout, r.stdout + r.stderr
    # A plain install of the pin: `--force-reinstall` reinstalled psutil too,
    # which this process has loaded, and on Windows that died on WinError 5.
    assert "-m pip install " in r.stderr, r.stderr
    assert "--force-reinstall" not in r.stderr, r.stderr
    assert "uv pip" not in r.stderr, r.stderr
