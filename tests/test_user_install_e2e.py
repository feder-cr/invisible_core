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

import sys
from pathlib import Path

import pytest

from invisible_core.testing import run_checked, throwaway_venv

pytestmark = pytest.mark.e2e

DIST = "invisible-core"
REPO = "invisible_core"


# The venv mechanics live in invisible_core.testing: `_run` and `_venv_python`
# were byte-identical here and in the other consumer, and the two fixtures had
# already drifted on whether the install happens in the fixture or in the test -
# same name, two meanings.
_run = run_checked


@pytest.fixture(scope="module")
def clean_venv():
    """An empty venv with nothing but pip, plus this distribution.

    Module-scoped: the install pulls the whole dependency tree."""
    with throwaway_venv("invcore-e2e-", install=DIST) as py:
        yield py


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

    here = Path(__file__).resolve().parents[1]
    # The checkout's version is derived too, so it is read by executing the
    # same derivation rather than parsed out of pyproject (which declares it
    # dynamic and carries no literal).
    #
    # Loaded BY FILE PATH, not as `invisible_core._version`. Importing it as a
    # submodule runs the package `__init__` first, which imports `download`,
    # which imports `platformdirs` - and this runs on the RUNNER's interpreter,
    # which deliberately has no dependencies installed: the whole job is a clean
    # environment where only the venv has the package.
    #
    # Measured on CI 2026-07-27: `ModuleNotFoundError: No module named
    # 'platformdirs'` on both legs, so THIS test - the forgotten-publish check -
    # was red on main and inert. It passed locally forever, because a dev
    # machine has the dependencies. `_version.py` imports nothing but json and
    # pathlib and reads `seal.json` beside itself, so the derivation under test
    # needs none of that machinery.
    local = _run([sys.executable, "-c",
                  "import importlib.util as u; "
                  "s = u.spec_from_file_location('_v', r'%s'); "
                  "m = u.module_from_spec(s); s.loader.exec_module(m); "
                  "print(m.__version__)"
                  % str(here / "src" / REPO / "_version.py")],
                 timeout=60).stdout.strip()
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


# ── PyPI and GitHub Releases must not drift apart ──────

def test_the_published_version_has_a_github_release():
    """Every version on the index needs a tag and a release carrying it.

    Added 2026-07-26, when the three packages had been on PyPI for a day with
    ZERO tags and ZERO releases between them. Not cosmetic: the release page is
    where a reader looks for what changed, `git describe` has nothing to say,
    and there is no commit anybody can point at as "this is the source of the
    version you have".

    NO VENV, deliberately. The first version of this took `clean_venv` and read
    the version out of the installed package, which made it depend on ANOTHER
    test in the same file having installed it first - it passed alone in the one
    repository whose fixture installs, and failed in the two whose fixture does
    not. A test whose result depends on what ran before it is not measuring what
    it claims. Both facts here are public: the index says what the latest
    version is, and the releases API says whether it has one.

    One-directional on purpose. A release for a version not yet on the index is
    a normal intermediate state during a publish; an index version with no
    release is the thing that gets forgotten, because nothing downstream breaks.
    """
    import json
    import urllib.error
    import urllib.request

    with urllib.request.urlopen(
            f"https://pypi.org/pypi/{DIST}/json", timeout=30) as resp:
        version = json.load(resp)["info"]["version"]

    url = f"https://api.github.com/repos/feder-cr/{REPO}/releases/tags/v{version}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            pytest.fail(
                f"the index serves {DIST} {version} and there is no GitHub "
                f"release tagged v{version}. Create it at the commit that built "
                f"that version - not at HEAD, which has moved on")
        if exc.code in (403, 429):
            pytest.skip(f"GitHub API rate-limited this check ({exc.code})")
        raise
    assert payload.get("draft") is False, (
        f"the release for v{version} is still a DRAFT, so nobody can see it")
    assert (payload.get("body") or "").strip(), (
        f"the release for v{version} has an empty body - a release page with no "
        f"notes is a tag with extra steps")
