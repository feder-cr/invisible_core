"""Can somebody install THIS package on its own and have it work?

Written when there were two consumers, both growing an install test the same
day. This one had none, and it is the package both of them pinned:
`invisible-playwright` and `invisible-firefox` declared an exact
`invisible-core==X.Y.Z`, so a core that is broken on the index would break both
of them at once - and neither consumer's test would say the cause is upstream.
It would look like two unrelated failures. `invisible_firefox` was deleted on
2026-08-18; `invisible-playwright` still pins this way, and is still the
consumer whose failure this file exists to distinguish from the core's own.

It is also the only one of the surviving packages whose version is DERIVED
rather than written: `_version.py` computes it from the packaged seal. That
makes "what the index actually serves" a question worth asking against a real
install rather than against a checkout, because the derivation runs at build
time and a seal that did not get packaged produces a version nobody chose.

Marked `e2e`: builds a venv and talks to the index, so it is excluded from the
default suite and runs in .github/workflows/user-install.yml and before a
release.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


pytestmark = pytest.mark.e2e

DIST = "invisible-core"
REPO = "invisible_core"


# LOCAL AND STAYING LOCAL. This is the one file in this repository that must
# NOT import invisible_core.
#
# The whole point of the job that runs it is a clean environment where ONLY the
# venv it builds has the package - the runner installs pytest and nothing else.
# `from invisible_core.testing import ...` therefore fails at COLLECTION with
# `ModuleNotFoundError: No module named 'invisible_core'`, which is what
# happened on both legs on 2026-07-28 after the venv helpers were shared. It
# passes on any development machine, where the editable install is on the path.
#
# `test_the_install_e2e_never_imports_the_package_under_test` keeps it that way.
# The wrapper's test_release_e2e.py carries the same rule in prose and has since
# the day it hit the same wall.
#
# ⛔ AND NOTHING THAT POINTS AT A CHECKOUT CROSSES INTO THE VENV. conftest.py
# puts this tree's `src` on PYTHONPATH for every child a test spawns, which is
# right for every other file and exactly wrong here: the venv's python then
# imports THIS CHECKOUT, not the copy the index served. Measured 2026-10-17 on
# the first run of the uv fixture: the installed distribution said 49.44.0
# (the index) and the module said 49.45.0 (this tree), inside "one" install.
# From 2026-09-16, when conftest.py started exporting it, to that day, every
# check here that imports the package read the checkout: the Playwright check
# read the checkout's imports, and "the packaged seal is in the wheel" found the
# checkout's seal.json, so a wheel without it would have passed. The wrapper's
# test_release_e2e.py strips the same variables, for the same measured reason.
# A copy of `invisible_core.install_job.LEAKS`, which this file cannot import;
# the gate checks that it covers it ([B349]).
_LEAKS = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "INVISIBLE_SEAL_FILE",
          "INVISIBLE_CORE_AUTOFIX", "INVISIBLE_CORE_AUTOFIX_ATTEMPTED",
          "INVISIBLE_CORE_PIN", "UV", "INVISIBLE_PLAYWRIGHT_ENGINE_DIR",
          "INVISIBLE_PLAYWRIGHT_CACHE_DIR")


def _run(cmd, *, timeout: int = 600, check: bool = True, env=None, cwd=None):
    env = dict(os.environ if env is None else env)
    for k in _LEAKS:
        env.pop(k, None)
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       timeout=timeout, env=env,
                       cwd=str(cwd) if cwd is not None else None)
    if check and r.returncode != 0:
        raise AssertionError(
            "{} exited {}\n--- stdout ---\n{}\n--- stderr ---\n{}".format(
                " ".join(str(c) for c in cmd), r.returncode,
                r.stdout[-3000:], r.stderr[-3000:]))
    return r


def _venv_python(venv):
    bindir = "Scripts" if os.name == "nt" else "bin"
    return Path(venv) / bindir / ("python.exe" if os.name == "nt" else "python")


def _uv() -> str:
    """The uv the README's way in needs. Missing is a FAILURE, not a skip: the
    workflow installs it with uv's own installer, so a runner without it is a
    broken job, and a skip would read as a pass."""
    found = shutil.which("uv")
    if not found:
        pytest.fail("uv is not on PATH, and the README installs this package with "
                    "uv: the job has to put it there (user-install.yml does, with "
                    "uv's own installer)")
    return found


def readme_install_lines() -> list:
    """The README's install fence: the code block that adds this distribution
    with `uv add`. Read from the README so the test runs what a person copies,
    not a copy of it that can drift."""
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    for body in re.findall(r"```[^\n]*\n(.*?)```", text, re.S):
        lines = [l.strip() for l in body.splitlines() if l.strip()]
        if any(l.split()[:2] == ["uv", "add"] and DIST in l.split() for l in lines):
            return lines
    raise AssertionError(
        f"README.md has no code block that installs {DIST} with `uv add`, so "
        f"there is no way in for this test to follow")


@pytest.fixture(scope="module")
def clean_venv():
    """A project `uv init` made, with the README's install lines run inside it,
    from the index. Returns the project's interpreter.

    The project is the README's precondition (`uv add` adds to one); the lines
    after it are the README's own. A venv uv makes has no pip in it, and that is
    the environment a person who followed the README is in.

    `UV_NO_CACHE` is environment, not a change to the lines: a cached index
    answer would hide a forgotten publish, which is what `--no-cache-dir` did
    for the pip version of this fixture.

    Module-scoped: the install pulls the whole dependency tree."""
    uv = _uv()
    root = Path(tempfile.mkdtemp(prefix="invcore-e2e-"))
    env = dict(os.environ, UV_NO_CACHE="1")
    try:
        project = root / "project"
        _run([uv, "init", "--python", ">=3.11", "--no-workspace", "--vcs", "none",
              str(project)], timeout=300, env=env)
        for line in readme_install_lines():
            _run(shlex.split(line), cwd=project, timeout=900, env=env)
        py = _venv_python(project / ".venv")
        assert py.exists(), "the README's lines made no venv at {}".format(py)
        yield py
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_it_installs_from_the_index_with_nothing_else_present(clean_venv: Path):
    """No Playwright, no wrapper, no manager. The core has to stand alone -
    that is the whole reason it was split out of the wrapper."""
    _run([_uv(), "pip", "check", "--python", str(clean_venv)], timeout=180)
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
    """EVERY version on the index needs a tag and a release carrying it.

    Added 2026-07-26, when the three packages had been on PyPI for a day with
    ZERO tags and ZERO releases between them. Not cosmetic: the release page is
    where a reader looks for what changed, `git describe` has nothing to say,
    and there is no commit anybody can point at as "this is the source of the
    version you have".

    IT CHECKED ONE VERSION UNTIL 2026-08-02. It read `info.version` - the latest
    - so the moment a release was published without its page, the next release
    made the omission invisible: the check moved on to the new version and the
    old gap stayed behind it. Measured that day: ELEVEN published versions across
    the three packages had no release, and eight had no tag at all, three of them
    published AFTER the backfill this test was written to protect. The docstring
    said "every version" from the first line while the code checked one, which is
    the same defect this project found three times in two days - a claim written
    beside the code rather than from it.

    It now walks the whole index. That is one API call per version, ~36 across
    the three packages, and it runs in the install-e2e job rather than the unit
    suite for exactly that reason.

    NO VENV, deliberately. The first version of this took `clean_venv` and read
    the version out of the installed package, which made it depend on ANOTHER
    test in the same file having installed it first - it passed alone in the one
    repository whose fixture installs, and failed in the two whose fixture does
    not. A test whose result depends on what ran before it is not measuring what
    it claims. Both facts here are public: the index says which versions exist,
    and the releases API says whether each has one.

    One-directional on purpose. A release for a version not yet on the index is
    a normal intermediate state during a publish; an index version with no
    release is the thing that gets forgotten, because nothing downstream breaks.

    Yanked versions are skipped: a yanked release is one the project has
    withdrawn, and demanding a release page for something nobody should install
    would be asking for the opposite of what the yank said.
    """
    import json
    import os
    import urllib.error
    import urllib.request

    with urllib.request.urlopen(
            f"https://pypi.org/pypi/{DIST}/json", timeout=30) as resp:
        index = json.load(resp)["releases"]

    live = sorted((version for version, files in index.items()
                   if files and not all(f.get("yanked") for f in files)),
                  key=lambda v: tuple(int(p) for p in v.split(".")))
    assert live, f"the index serves no usable version of {DIST}"

    # Authenticated when a token happens to be around, which on a GitHub runner
    # it always is. Unauthenticated the API allows 60 calls an hour PER IP, and
    # this walk wants one per version across three repositories: measured
    # 2026-08-02, the walk hit 403 on its eleventh call. The token is optional,
    # never required, and never printed.
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    missing, drafts, empty = [], [], []
    checked = 0
    cut_short = None
    for version in live:
        url = f"https://api.github.com/repos/feder-cr/{REPO}/releases/tags/v{version}"
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=30) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                missing.append(version)
                checked += 1
                continue
            if exc.code in (403, 429):
                # NOT an unconditional skip. The first version of this walk
                # skipped here, which threw away every violation already in
                # hand: the mutation that deleted an OLD release passed, because
                # a rate-limit two versions later abandoned the whole test. A
                # gate that discards its own findings on an unrelated error is
                # worse than no gate, because it reports PASS.
                cut_short = (version, exc.code)
                break
            raise
        checked += 1
        if payload.get("draft") is not False:
            drafts.append(version)
        if not (payload.get("body") or "").strip():
            empty.append(version)

    problems = []
    # The LEDGER is the fourth record, and it costs no API call: it is a file in
    # this repository. `version_gate check` compares it to the index too, but
    # only on a release - so a version published outside the gate stays invisible
    # until the NEXT release is refused by it, which is how the gap from
    # 2026-08-02 was found. Here it surfaces on every run of this job.
    ledger_path = Path(__file__).resolve().parents[1] / "PUBLISHED.json"
    recorded = {}
    if ledger_path.is_file():
        recorded = {e["version"]: e for e in
                    json.loads(ledger_path.read_text(encoding="utf-8"))["released"]}

    unrecorded = [v for v in live if recorded and v not in recorded]
    thin = {v: [k for k in ("published_at", "requires_dist", "wheel_filename",
                            "sdist_filename", "wheel", "sdist")
                if k not in recorded[v]]
            for v in live if v in recorded}
    thin = {v: missing for v, missing in thin.items() if missing}

    if unrecorded:
        problems.append(
            f"on the index and NOT in PUBLISHED.json: {unrecorded}. Those were "
            f"published outside the gate, so nothing records what shipped under "
            f"them; back-fill from the artifacts the index serves, never from the "
            f"tree, which has moved on")
    if thin:
        problems.append(f"ledger entries missing fields: {thin}")

    if missing:
        problems.append(
            f"on the index with NO release: {missing}. Create each at the commit "
            f"that built that version - not at HEAD, which has moved on. PyPI's "
            f"upload_time and the commit carrying the version are how to find it")
    if drafts:
        problems.append(f"still a DRAFT, so nobody can see it: {drafts}")
    if empty:
        problems.append(f"empty body, which is a tag with extra steps: {empty}")

    assert not problems, (
        f"{DIST} has {len(live)} usable versions on the index, {checked} were "
        f"checked, and " + "; ".join(problems))

    if cut_short:
        version, code = cut_short
        if checked == 0:
            # "Green because it never ran" is the shape this project met three
            # times in two days. A skip that reads "all clean so far" after
            # checking NOTHING is that shape wearing a pass.
            pytest.skip(f"GitHub API answered {code} on the FIRST call, so NONE "
                        f"of {len(live)} versions were checked and this proves "
                        f"nothing. Set GITHUB_TOKEN to raise the rate limit")
        pytest.skip(f"GitHub API answered {code} at v{version} after {checked} of "
                    f"{len(live)} versions, clean up to there. Set GITHUB_TOKEN to "
                    f"raise the rate limit")

def test_the_install_e2e_never_imports_the_package_under_test():
    """This file must be COLLECTABLE without invisible_core installed.

    The job that runs it installs pytest on the runner and nothing else, on
    purpose: the package under test goes into a venv the fixture builds, from
    the index, and a second copy on the runner's path would mean every
    assertion read the wrong one.

    So a module-level `import invisible_core...` here fails at COLLECTION with
    `ModuleNotFoundError`, and the whole job goes red having tested nothing.
    That happened on both legs on 2026-07-28, after the venv helpers were moved
    into `invisible_core.testing` and this file started importing them from
    there. It passes on any development machine, where the editable install is
    always on the path - which is why an assertion is cheaper than remembering.

    Parsed, not imported: importing this module to check it would be the very
    thing under test.
    """
    import ast

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        # Module level only. Inside a function is fine and is how the checks
        # below reach the VENV's copy through a subprocess.
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if node.col_offset != 0:
            continue
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""])
        for name in names:
            if name.split(".")[0] == REPO:
                offenders.append(f"line {node.lineno}: {name}")

    assert not offenders, (
        "this file imports the package it is testing the INSTALL of, at module "
        "level:\n  " + "\n  ".join(offenders) +
        "\nThe runner has no invisible_core, so this is a collection error and "
        "the job reports failure having run nothing. Keep the helpers local.")
