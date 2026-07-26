"""Can somebody install THIS package on its own and have it work?

The two consumers each grew an install test today. This one had none, and it is
the package both of them pin: `invisible-playwright` and `invisible-firefox`
declare an exact `invisible-core==X.Y.Z`, so a core that is broken on the index
breaks both of them at once - and neither consumer's test would say the cause is
upstream. It would look like two unrelated failures.

It is also the only one of the three whose version is DERIVED rather than
written: `_version.py` computes it from the packaged seal. That makes "what the
index actually serves" a question worth asking against a real install rather
than against a checkout, because the derivation runs at build time and a seal
that did not get packaged produces a version nobody chose.

Marked `e2e`: builds a venv and talks to the index, so it is excluded from the
default suite and runs in .github/workflows/user-install.yml and before a
release.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

DIST = "invisible-core"


def _run(cmd: list[str], *, timeout: int = 600,
         check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} exited {r.returncode}\n"
            f"--- stdout ---\n{r.stdout[-3000:]}\n--- stderr ---\n{r.stderr[-3000:]}")
    return r


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")


@pytest.fixture(scope="module")
def clean_venv():
    root = Path(tempfile.mkdtemp(prefix="invcore-e2e-"))
    try:
        _run([sys.executable, "-m", "venv", str(root / "venv")], timeout=300)
        py = _venv_python(root / "venv")
        assert py.exists(), f"no venv python at {py}"
        _run([str(py), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
             timeout=300)
        _run([str(py), "-m", "pip", "install", "--no-cache-dir", DIST],
             timeout=900)
        yield py
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_it_installs_from_the_index_with_nothing_else_present(clean_venv: Path):
    """No Playwright, no wrapper, no manager. The core has to stand alone -
    that is the whole reason it was split out of the wrapper."""
    _run([str(clean_venv), "-m", "pip", "check"], timeout=180)
    out = _run([str(clean_venv), "-c",
                "import invisible_core; print('OK', invisible_core.__name__)"],
               timeout=120).stdout
    assert "OK invisible_core" in out, out


def test_importing_it_does_not_pull_playwright_in(clean_venv: Path):
    """The split's load-bearing property. Importing the core must not start
    Playwright, or the profile-manager - which has no Playwright at all - could
    not use it, and every consumer would pay for a dependency it may not want.

    Checked on a REAL install rather than by reading the imports: a transitive
    dependency added by mistake is exactly the kind of thing a source checkout
    hides, because the developer has Playwright installed anyway.
    """
    out = _run([str(clean_venv), "-c",
                "import sys, invisible_core; "
                "print(sorted(m for m in sys.modules if 'playwright' in m.lower()))"],
               timeout=120).stdout.strip()
    assert out.splitlines()[-1] == "[]", (
        f"importing the core dragged Playwright in: {out}")


def test_the_version_the_index_serves_matches_this_checkout(clean_venv: Path):
    """The forgotten-publish check, and here it is sharper than elsewhere.

    This version is DERIVED from the packaged seal, so a mismatch means one of
    two things and both matter: the publish was skipped, or the build produced
    a version nobody chose because the seal it derives from was not what this
    checkout holds.
    """
    served = _run([str(clean_venv), "-c",
                   f"from importlib.metadata import version; print(version({DIST!r}))"],
                  timeout=60).stdout.strip()
    derived = _run([str(clean_venv), "-c",
                    "import invisible_core; print(invisible_core.__version__)"],
                   timeout=120).stdout.strip()
    assert served == derived, (
        f"the installed distribution reports {served} while the module derives "
        f"{derived} from its own packaged seal - the two disagree inside one "
        f"install, which means the seal that shipped is not the one the version "
        f"was computed from")

    import tomllib
    here = Path(__file__).resolve().parents[1]
    # The checkout's version is derived too, so it is read by executing the
    # same derivation rather than parsed out of pyproject (which declares it
    # dynamic and carries no literal).
    local = _run([sys.executable, "-c",
                  "import sys; sys.path.insert(0, r'%s'); "
                  "from invisible_core._version import __version__; print(__version__)"
                  % str(here / "src")], timeout=60).stdout.strip()
    assert served == local, (
        f"the index serves {DIST} {served} while this checkout derives {local}. "
        f"Either the publish was forgotten or the checkout is ahead - and both "
        f"consumers pin an exact version, so they resolve what the INDEX has")


def test_the_engine_it_would_download_is_named_by_the_seal(clean_venv: Path):
    """A real install has to be able to say which engine it is for, without a
    network call. Everything downstream - the cache key, the refusal messages,
    the consumers' compatibility checks - reads these two values."""
    out = _run([str(clean_venv), "-c",
                "from invisible_core.constants import BINARY_VERSION, "
                "FIREFOX_UPSTREAM_VERSION; print(BINARY_VERSION, FIREFOX_UPSTREAM_VERSION)"],
               timeout=120).stdout.strip()
    tag, upstream = out.split()
    assert tag.startswith("firefox-"), f"engine tag looks wrong: {tag!r}"
    assert upstream[0].isdigit(), f"upstream version looks wrong: {upstream!r}"


def test_the_packaged_seal_is_actually_in_the_wheel(clean_venv: Path):
    """The failure the version derivation cannot survive.

    `_version.py` reads `seal.json` from beside itself. If packaging drops that
    file the import fails at install time for every user, and no test that runs
    from a checkout can see it - the file is always there in the source tree.
    """
    out = _run([str(clean_venv), "-c",
                "import invisible_core, pathlib; "
                "p = pathlib.Path(invisible_core.__file__).with_name('seal.json'); "
                "print(p.is_file(), p.stat().st_size if p.is_file() else 0)"],
               timeout=120).stdout.strip()
    present, size = out.split()
    assert present == "True", "seal.json is not inside the installed package"
    assert int(size) > 0, "seal.json shipped empty"
