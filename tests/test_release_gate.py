"""The publish gate, proved against known-bad input.

A gate that has only ever printed PASS is not a gate, so every case below drives
scripts/version_gate.py to a REFUSAL and asserts the exit code, the file it
names and the remedy it prints. The passing cases are here too, because a gate
that refuses everything is just as useless.

Shape of every test: copy the repo into tmp_path, `record` the pristine build as
if it had been published, mutate one thing, then `check`. The baseline build and
its ledger are made once per session and copied, so each case costs one build.

Cases:
  KB1  seal.json changed WITHOUT its tag moving (playwright.max widened, a
       designed-in workflow) -> refused. This is the hard one: seal.json is both
       shipped content and the version input.
  KB2  a .py docstring changed                  -> refused
  KB3  a dependency added to pyproject.toml     -> refused (it reaches METADATA)
  KB4  README.md edited                         -> refused (readme = long_description)
  KB5  a tests/ file edited                     -> refused on the SDIST leg only
  KB6  a direct URL dependency declared         -> refused (the index rejects it)
  KB7  the version goes backwards               -> refused
  OK1  the seal TAG moved, nothing else         -> allowed, version moved with it
  OK2  CORE_REVISION bumped beside a code change-> allowed
  OK3  nothing published yet, and the operator SAYS so -> allowed, loudly
  NB   an unchanged tree at an already published version is refused as well, for
       a different reason: there is nothing to release and the index will not
       serve the same filename twice.

Then the three failures an adversarial pass found in the first version of the
gate, each with the same known-bad input that got through:

  FO1-FO3  FAILS OPEN. A --ledger typo, a deleted ledger and an emptied ledger
       each waved a real content change through with "this is release 1", exit
       0. The gate's memory going missing is now its own exit code (4), and an
       empty ledger needs --first-release said out loud.
  CW1  CRIES WOLF. A plain .gitignore at the repo root was reported as a content
       change, because hatchling ships it whatever the include list says.
  CW2  CRIES WOLF. A CRLF/LF flip read as content drift, so the ledger written
       on this Windows checkout would refuse the first release from Linux CI.
  CW3  A ledger entry missing its 'wheel' key produced something
       violation-shaped instead of GATE BROKEN.

Then the six a later adversarial pass found, each of them measured:

  D1   FAILS OPEN. `check --first-release` against an empty ledger waved a real
       content change through, exit 0, and `publish --first-release --dry-run`
       printed the twine upload it authorised. The comment on that branch already
       claimed the index cross-check was forced there; it was not. It is now, and
       the claim it settles is the stronger one - the index must serve NO version
       at all.
  D2   FAILS OPEN. `_entry_for` matches on the entry's 'version' string alone, so
       editing released[0].version made a drifted build look unpublished. The
       recorded wheel/sdist filenames carry the true version and are cross-checked.
  D3   VACUOUS. `record`'s refusal to overwrite an entry whose digests differ had
       no test at all: deleting the refusal left the suite green.
  D4   VACUOUS. normalise() had a tested LOWER bound (a CRLF flip must not fire)
       and no UPPER bound, so broadening it to "strip all whitespace" left the
       suite green - and under that mutant a pure re-indent of a .py file, which
       is semantic in Python, was not refused.
  D5   The verdict came from the file tables while the refusal printed the stored
       digests as though they were the comparison, so garbage digests beside an
       intact file table still read as "nothing to release". The digests are the
       verdict now, and an entry that contradicts itself is GATE BROKEN.
  D6   normalise() folds a lone CR even when it is CONTENT rather than a line
       ending. Deliberate, and pinned here so a future change has to be too.
"""
from __future__ import annotations

import importlib.util
import re
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("build", reason="the gate builds the artifact it digests")
pytest.importorskip("hatchling", reason="offline build needs the backend installed")

import invisible_core  # noqa: E402

# The repo, taken from THIS FILE rather than from where the module happens to
# be installed. `Path(invisible_core.__file__).parents[2]` is the repo under an
# editable install and `.../Lib` under a regular one, so the same expression
# silently means two different things depending on how the developer installed
# the package - and under the regular one the fixtures below fail copying a
# pyproject.toml that is simply not there.
REPO_ROOT = Path(__file__).resolve().parents[1]

# Every expectation in this file is written against a <tag>.0.0 baseline.
BASELINE_REVISION = 0

#: "already published and byte-identical" - a NO-OP, not a refusal.
#: It shared exit 1 with "the content changed under an unmoved version" until
#: 2026-07-26, and the two must not look alike to a caller: the second has to
#: stay a hard failure, while the first is what a re-pushed or backfilled
#: release tag legitimately produces. See scripts/version_gate.py.
NOTHING_TO_RELEASE = 5
GATE = REPO_ROOT / "scripts" / "version_gate.py"

# The whole file drives scripts/version_gate.py, and `scripts/` is NOT in the
# sdist include list - deliberately, since the ledger and the gate are not
# something users receive. But `tests/` IS, so unpacking the sdist and running
# pytest gave 43 hard errors on a missing file, for a gate that is none of the
# user's business. The `integration` marker does not save them either: this
# project's addopts filter `slow` and `e2e`, not `integration`.
#
# The sibling file test_release_wiring.py already had exactly this guard. This
# one is the same shape as the defect invisible_playwright records having fixed
# once already: a maintainer-only test shipped to users who cannot make it pass.
_in_checkout = (REPO_ROOT / "scripts" / "version_gate.py").exists()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _in_checkout,
        reason=("not a source checkout - the publish gate lives in scripts/, "
                "which the sdist does not ship"),
    ),
]

_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".git", ".pytest_cache", ".venv", "venv",
    "dist", "build", "*.egg-info", "PUBLISHED.json",
)


EMPTY_LEDGER = '{"schema": 1, "released": []}'


def set_core_revision(root: Path, value: int) -> None:
    p = root / "src" / "invisible_core" / "_version.py"
    src = p.read_text(encoding="utf-8")
    out = "\n".join(f"CORE_REVISION = {value}" if line.startswith("CORE_REVISION")
                    else line for line in src.splitlines()) + "\n"
    assert f"CORE_REVISION = {value}" in out
    p.write_text(out, encoding="utf-8")


def _copy_repo(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(REPO_ROOT / name, dest / name)
    shutil.copytree(REPO_ROOT / "src", dest / "src", ignore=_IGNORE)
    shutil.copytree(REPO_ROOT / "tests", dest / "tests", ignore=_IGNORE)
    # The ledger is never copied from the real repo (it would make these tests
    # depend on the project's actual release history) and it is never left
    # absent either: an absent ledger is now its own hard failure, which is the
    # point of FO2 below.
    (dest / "PUBLISHED.json").write_text(EMPTY_LEDGER, encoding="utf-8")
    # Neither is the revision. Every expectation below is written against a
    # baseline of <tag>.0.0, so leaving the copy at whatever the repo happens
    # to hold today makes those literals wrong the first time the core ships a
    # normal release: bumping CORE_REVISION 1 -> 2 for 18.2.0 turned nine of
    # these red at once, each one asserting a version string that had simply
    # moved on. A test that has to be re-edited whenever an expected thing
    # happens is a test that eventually gets re-edited without being read.
    # Pinning it here is the arrange step these cases were relying on ambient
    # state for.
    set_core_revision(dest, BASELINE_REVISION)
    return dest


def run_gate(root: Path, *args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE), "--project-root", str(root),
           "--ledger", str(root / "PUBLISHED.json"), "--single-build", *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def touch_content(root: Path) -> None:
    """A real, shipped content change under an unmoved version.

    Every fail-open case below carries one of these, because "the gate said
    nothing" is only damning if there was something to say."""
    p = root / "src" / "invisible_core" / "prefs.py"
    p.write_text("# a change nobody would ever receive\n" + p.read_text(encoding="utf-8"),
                 encoding="utf-8")


def out_of(p: subprocess.CompletedProcess) -> str:
    return (p.stdout or "") + (p.stderr or "")


def gate_module():
    """The gate imported as a module, for the pieces worth testing directly.

    normalise() is one of them: its upper bound is a property of the function and
    proving it only end-to-end costs a build per assertion.

    Imported as a MODULE, not loaded from `scripts/version_gate.py` by path. The
    implementation moved into the package on 2026-07-27 so the two consumers -
    which pin this package exactly - can run the same gate; they had none, which
    is how invisible-playwright 0.4.4 was uploaded from a stale tree. The script
    is a back-compat shim now, and loading it by path gave
    `AttributeError: module has no attribute 'normalise'` - a useful failure,
    because it says the test was addressing the code by its old address.
    """
    from invisible_core import release

    return release


def fake_index(tmp_path: Path, versions: list[str]) -> str:
    """A file:// URL the gate can read instead of the real index.

    The first-release cross-check is FORCED (D1), so the tests need an index they
    can state the contents of. A real one would make them network-dependent and
    would start failing the day invisible-core is actually published."""
    p = tmp_path / "index.json"
    p.write_text(json.dumps({"releases": {v: [] for v in versions}}), encoding="utf-8")
    return p.as_uri()


@pytest.fixture(scope="session")
def published_baseline(tmp_path_factory) -> Path:
    """A copy of the repo whose CURRENT build is recorded as published."""
    base = _copy_repo(tmp_path_factory.mktemp("gate-baseline") / "core")
    p = run_gate(base, "record")
    assert p.returncode == 0, out_of(p)
    ledger = json.loads((base / "PUBLISHED.json").read_text(encoding="utf-8"))
    assert len(ledger["released"]) == 1, ledger
    assert len(ledger["released"][0]["wheel"]["files"]) > 20
    return base


@pytest.fixture()
def core(published_baseline, tmp_path) -> Path:
    work = tmp_path / "core"
    shutil.copytree(published_baseline, work)
    return work


# ------------------------------------------------------------------ helpers

def seal_path(root: Path) -> Path:
    return root / "src" / "invisible_core" / "seal.json"


def edit_seal(root: Path, **fields) -> None:
    p = seal_path(root)
    data = json.loads(p.read_text(encoding="utf-8"))
    data.update(fields)
    p.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n",
                 encoding="utf-8")




# --------------------------------------------------------------- known bad

def test_kb1_seal_changed_without_the_tag_moving_is_refused(core):
    """The hard case. Widening the Playwright range travels in the seal, on
    purpose, so that consumers need no release - which means it changes the
    bytes users receive while the tag, and therefore the version, stands still.
    Measured: same wheel FILENAME, different content digest."""
    data = json.loads(seal_path(core).read_text(encoding="utf-8"))
    assert data["playwright"]["max"] != "1.62.0"
    pw = dict(data["playwright"], max="1.62.0")
    edit_seal(core, playwright=pw)

    p = run_gate(core)
    text = out_of(p)
    assert p.returncode == 1, text
    assert "RELEASE REFUSED" in text
    assert "the content changed but the version did not" in text
    assert "invisible_core/seal.json" in text
    assert "CORE_REVISION" in text and "_version.py" in text
    assert "18.0.0 -> 18.1.0" in text, "the remedy must name the next version"
    # The seal tag did not move, so the other legal remedy must be offered too.
    assert "roll a new seal" in text


def test_kb2_a_python_docstring_change_is_refused(core):
    p = core / "src" / "invisible_core" / "prefs.py"
    p.write_text("# a change nobody would ever receive\n" + p.read_text(encoding="utf-8"),
                 encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 1, text
    assert "changed  wheel: invisible_core/prefs.py" in text
    assert "bump CORE_REVISION" in text


def test_kb3_a_new_dependency_is_refused_because_metadata_is_content(core):
    """pyproject.toml is only partly content, but the part that reaches
    Requires-Dist is exactly the part that changes what pip installs."""
    p = core / "pyproject.toml"
    p.write_text(p.read_text(encoding="utf-8").replace(
        '    "tzdata>=2024.1",', '    "tzdata>=2024.1",\n    "certifi>=2024",'),
        encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 1, text
    assert "<dist-info>/METADATA" in text


def test_kb4_a_readme_edit_is_refused_because_it_is_the_long_description(core):
    p = core / "README.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nOne more line.\n", encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 1, text
    assert "<dist-info>/METADATA" in text, "the README body ships inside METADATA"


def test_kb5_a_tests_only_change_is_refused_on_the_sdist_leg(core):
    """tests/ ships in the sdist and not in the wheel. The sdist filename is
    burned on upload just like the wheel's, so the sdist needs its own leg."""
    p = core / "tests" / "test_seal_version.py"
    p.write_text(p.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 1, text
    assert "changed  sdist: tests/test_seal_version.py" in text
    assert "changed  wheel:" not in text, "the wheel leg must be untouched here"


def test_kb6_a_direct_url_dependency_is_refused(core):
    """A public index rejects a Requires-Dist carrying a URL (HTTP 400), and a
    direct reference is also the construct that makes `pip check` blind. Catch
    it before the upload, not during."""
    p = core / "pyproject.toml"
    p.write_text(p.read_text(encoding="utf-8").replace(
        '    "tzdata>=2024.1",',
        '    "tzdata>=2024.1",\n    "somepkg @ git+https://example.invalid/somepkg.git",',
    ) + '\n[tool.hatch.metadata]\nallow-direct-references = true\n', encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 1, text
    assert "direct URL reference" in text
    assert "somepkg" in text


def test_kb7_a_version_that_goes_backwards_is_refused(core):
    """CORE_REVISION must never be reset and a tag must never go down: pip
    compares with != , not <, and the index refuses a reused number outright."""
    edit_seal(core, tag="firefox-17")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 1, text
    assert "went BACKWARDS" in text
    assert "17.0.0" in text and "18.0.0" in text


def test_an_unchanged_tree_at_a_published_version_is_refused(core):
    """Not the content-drift case, and it must not be reported as one: there is
    simply nothing to release."""
    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == NOTHING_TO_RELEASE, text
    assert "nothing to release" in text.lower()
    assert "the content changed but the version did not" not in text


# ------------------------------------------------------------- known good

def test_ok1_moving_only_the_seal_tag_is_allowed(core):
    """The version is a projection of seal['tag'], so a seal edit that moves the
    tag moves the version for free. This is the ONE seal change that is not an
    un-versioned content change, and the gate must not confuse it with KB1."""
    edit_seal(core, tag="firefox-19")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 0, text
    assert re.search(r"invisible[-_]core 19\.0\.0", text), text[:200]
    assert "HAS NEVER BEEN PUBLISHED" in text
    assert "PUBLISH ALLOWED" in text


def test_ok2_a_code_change_with_a_core_revision_bump_is_allowed(core):
    p = core / "src" / "invisible_core" / "prefs.py"
    p.write_text("# a core-only fix\n" + p.read_text(encoding="utf-8"), encoding="utf-8")
    set_core_revision(core, 1)

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 0, text
    # PEP 503 makes the hyphen and underscore forms the SAME name, and the
    # header now takes it from pyproject (one gate, three projects) instead of
    # a hardcoded constant. The claim is that the project and its version are
    # both stated - not which spelling.
    assert re.search(r"invisible[-_]core 18\.1\.0", text), text[:200]
    assert "PUBLISH ALLOWED" in text


def test_ok3_an_empty_ledger_plus_an_explicit_declaration_is_release_one(core, tmp_path):
    """Bootstrap. Nothing has been published, so there is nothing to compare -
    and the operator has to SAY so, because an empty ledger is also what a lost
    ledger looks like. The index has to agree: the claim is checkable and it is
    checked (D1), so the empty index below is part of the fixture, not decoration."""
    (core / "PUBLISHED.json").write_text(EMPTY_LEDGER, encoding="utf-8")

    r = run_gate(core, "check", "--first-release",
                 "--index-json-url", fake_index(tmp_path, []))
    text = out_of(r)
    assert r.returncode == 0, text
    assert "FIRST RELEASE DECLARED" in text
    assert "has no published version" in text
    assert "PUBLISH ALLOWED" in text


def test_the_ledger_is_readable_and_never_ships(core):
    """Two invariants of the real repo. The ledger records a digest of what
    ships, so it must never be part of what ships, or every release would
    invalidate the digest it certifies. Asserted from the built artifacts, not
    from the include list."""
    real = json.loads((REPO_ROOT / "PUBLISHED.json").read_text(encoding="utf-8"))
    assert real["schema"] == 1
    assert isinstance(real["released"], list)

    r = run_gate(core, "show")
    assert r.returncode == 0, out_of(r)
    info = json.loads(r.stdout)
    assert info["wheel_entries"] > 20 and info["sdist_entries"] > 20
    assert not any(r_ for r_ in info["requires_dist"] if "@" in r_.split(";")[0]), \
        "no dependency may be a direct URL"

    import tarfile
    import zipfile
    # Prove it from the artifacts, not from the include list.
    out = core / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--wheel", "--sdist",
         "--outdir", str(out), str(core)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    whl = next(out.glob("*.whl"))
    sd = next(out.glob("*.tar.gz"))
    assert not [n for n in zipfile.ZipFile(whl).namelist() if "PUBLISHED" in n]
    with tarfile.open(sd) as tf:
        names = tf.getnames()
    assert not [n for n in names if "PUBLISHED" in n or "/scripts/" in n]


# ------------------------------------------------- FO: it used to fail OPEN
#
# Each of these three had a real content change sitting in the tree and was
# waved through with "NO PRIOR RELEASE RECORDED - this is release 1", exit 0.
# The ledger is the gate's only offline memory, and losing it silently disarmed
# everything.

def test_fo1_a_typo_in_the_ledger_path_is_a_hard_failure_not_a_first_release(core):
    """--ledger PUBLISHED.jsonn. One keystroke used to turn the gate off."""
    touch_content(core)
    cmd = [sys.executable, str(GATE), "--project-root", str(core),
           "--ledger", str(core / "PUBLISHED.jsonn"), "--single-build", "check"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    text = out_of(r)

    assert r.returncode == 4, text
    assert "NO LEDGER" in text
    assert "release 1" not in text.replace("--first-release", "")
    assert "PUBLISH ALLOWED" not in text


def test_fo2_a_deleted_ledger_is_a_hard_failure(core):
    touch_content(core)
    (core / "PUBLISHED.json").unlink()

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 4, text
    assert "NO LEDGER" in text
    assert "PUBLISH ALLOWED" not in text


def test_fo3_an_emptied_ledger_is_refused_unless_first_release_is_declared(core):
    """The file is there and parses, so this is not exit 4 - but "empty" is
    equally what a ledger that lost its entries looks like, and the gate must
    not resolve that ambiguity in the direction that lets everything through."""
    touch_content(core)
    (core / "PUBLISHED.json").write_text(EMPTY_LEDGER, encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 1, text
    assert "the ledger records no published version" in text
    assert "--first-release" in text
    assert "restore the ledger from git" in text.lower()
    assert "PUBLISH ALLOWED" not in text


def test_fo4_first_release_cannot_be_claimed_twice(core):
    """The flag is the whole strength of FO3, so it must be worthless the moment
    anything has actually been published."""
    r = run_gate(core, "check", "--first-release")
    text = out_of(r)
    assert r.returncode == 1, text
    assert "already records 1 published version" in text
    assert "18.0.0" in text


def test_fo5_a_missing_ledger_is_not_rescued_by_first_release(core):
    """--first-release states that nothing was published. It does not stand in
    for the file that has to carry that claim into the next run."""
    touch_content(core)
    (core / "PUBLISHED.json").unlink()

    r = run_gate(core, "check", "--first-release")
    text = out_of(r)
    assert r.returncode == 4, text
    assert "NO LEDGER" in text


def test_the_exit_codes_are_all_distinct(core):
    """Each has to mean a different thing to a CI step, and the fail-open bug
    was exactly a 4-shaped situation reported as 0.

    Five now, not four: "already published and byte-identical" was split out of
    the refusal code on 2026-07-26. A release tag pointing at something already
    on the index is a no-op, and a caller that cannot tell it from a content
    violation reports a red release for it.
    """
    assert run_gate(core, "show").returncode == 0
    assert run_gate(core).returncode == NOTHING_TO_RELEASE      # nothing to do
    touch_content(core)
    assert run_gate(core).returncode == 1                       # content drift
    (core / "PUBLISHED.json").unlink()
    assert run_gate(core).returncode == 4                       # no memory


def test_a_mistyped_command_is_not_reported_as_a_broken_gate(core):
    """A typo and "the gate could not reach a verdict" were the same number.

    Argparse exits 2 on a usage error, and 2 is EXIT_BROKEN here, so a CI step
    reading the code could not tell `chekc` from a gate that fell over. EXIT_USAGE
    was declared for this and used nowhere - open item 5 in
    `18-gate-inventory.md`, whose own text about it was wrong too: it claimed the
    module docstring documented the code, and the docstring never mentioned it.

    Both shapes: an unknown subcommand and an unknown option, since argparse
    reports them through different parsers.
    """
    for argv in (["chekc"], ["check", "--no-such-flag"]):
        r = run_gate(core, *argv)
        assert r.returncode == 3, (
            f"{argv} exited {r.returncode}; 2 would be indistinguishable from "
            f"EXIT_BROKEN and 0 would be worse\n" + out_of(r))
        assert "PUBLISH ALLOWED" not in out_of(r)


def test_an_unreadable_ledger_is_a_hard_failure_not_a_refusal(core):
    (core / "PUBLISHED.json").write_text("{not json", encoding="utf-8")
    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 4, text
    assert "UNREADABLE LEDGER" in text
    assert "Restore it from git history" in text
    assert "PUBLISH ALLOWED" not in text


# ------------------------------------------------ CW: it used to cry wolf
#
# A gate that fires on a change nobody made gets switched off, and then it
# guards nothing at all.

def test_cw1_a_gitignore_at_the_repo_root_is_not_a_content_change(core):
    """Measured: hatchling puts .gitignore into the sdist even though the sdist
    `include` list never mentions it. Adding one is not a change to what users
    receive, and the first version of this gate refused a release over it."""
    (core / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert "the content changed but the version did not" not in text, text
    assert ".gitignore" not in text
    # The tree is otherwise identical to what was published, so the only verdict
    # left is "there is nothing to release".
    assert r.returncode == NOTHING_TO_RELEASE, text
    assert "nothing to release" in text.lower()


def test_cw2_a_line_ending_flip_is_not_a_content_change(core):
    """This repo is a Windows checkout with autocrlf active - 19 of the 52 files
    in today's sdist carry CRLF and the rest carry LF. The ledger is checked in
    and travels to Linux CI, so digesting raw bytes would refuse the first CI
    release over a change nobody made."""
    flipped = 0
    for p in sorted(core.rglob("*")):
        if not p.is_file() or p.name == "PUBLISHED.json":
            continue
        blob = p.read_bytes()
        if b"\x00" in blob:
            continue
        try:
            blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        lf = blob.replace(b"\r\n", b"\n")
        crlf = lf.replace(b"\n", b"\r\n")
        if crlf != blob:
            p.write_bytes(crlf)
            flipped += 1
    assert flipped > 5, "nothing was flipped, so this test proves nothing"

    r = run_gate(core)
    text = out_of(r)
    assert "the content changed but the version did not" not in text, text
    assert r.returncode == NOTHING_TO_RELEASE, text
    assert "nothing to release" in text.lower()


def test_cw2b_a_real_change_still_survives_the_line_ending_normalisation(core):
    """The other half of CW2: folding line endings must not fold away content.
    A one-character edit inside a file whose endings also flipped is still a
    content change."""
    p = core / "src" / "invisible_core" / "prefs.py"
    blob = p.read_bytes().replace(b"\r\n", b"\n")
    p.write_bytes(b"# a real change\r\n" + blob.replace(b"\n", b"\r\n"))

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 1, text
    assert "the content changed but the version did not" in text
    assert "changed  wheel: invisible_core/prefs.py" in text


def test_cw3_a_structurally_broken_ledger_entry_is_gate_broken(core):
    """A schema-valid entry with no 'wheel' key produced something
    violation-shaped: the operator reads "the content changed" and goes hunting
    a change that does not exist. It is the gate that is broken, and the two
    must not print the same way."""
    led = json.loads((core / "PUBLISHED.json").read_text(encoding="utf-8"))
    del led["released"][0]["wheel"]
    (core / "PUBLISHED.json").write_text(json.dumps(led), encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 2, text
    assert "GATE BROKEN" in text
    assert "released[0]" in text
    assert "RELEASE REFUSED" not in text
    assert "the content changed but the version did not" not in text


@pytest.mark.parametrize("mangle,marker", [
    (lambda e: e.pop("sdist"), "'sdist' record"),
    (lambda e: e.pop("version"), "no 'version' string"),
    (lambda e: e["wheel"].pop("files"), "'wheel.files' is missing or empty"),
    (lambda e: e["wheel"].pop("digest"), "'wheel.digest' is missing"),
    (lambda e: e["wheel"]["files"].update({"invisible_core/prefs.py": 7}),
     "non-string hashes"),
])
def test_cw3b_every_shape_of_corrupt_entry_is_gate_broken(core, mangle, marker):
    led = json.loads((core / "PUBLISHED.json").read_text(encoding="utf-8"))
    mangle(led["released"][0])
    (core / "PUBLISHED.json").write_text(json.dumps(led), encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 2, text
    assert "GATE BROKEN" in text
    assert marker in text
    assert "RELEASE REFUSED" not in text


# -------------------------------------------------- the gate is WIRED to the
# only supported upload path, so it cannot be left uninvoked

def test_publish_does_not_reach_twine_when_the_gate_refuses(core):
    """`publish` runs `check` in-process and returns its exit code unchanged.
    The upload is inside the gate rather than beside it, so skipping the gate
    means not publishing at all."""
    touch_content(core)   # content moved, version did not

    r = run_gate(core, "publish", "--dry-run")
    text = out_of(r)
    assert r.returncode == 1, text
    assert "the content changed but the version did not" in text
    assert "upload NOT attempted" in text
    assert "twine" not in text, "an upload command was formed after a refusal"


def test_publish_does_not_reach_twine_when_the_ledger_is_missing(core):
    (core / "PUBLISHED.json").unlink()

    r = run_gate(core, "publish", "--dry-run")
    text = out_of(r)
    assert r.returncode == 4, text
    assert "upload NOT attempted" in text
    assert "twine" not in text


def test_publish_authorises_the_upload_only_after_a_green_gate(core):
    set_core_revision(core, 1)
    p = core / "src" / "invisible_core" / "prefs.py"
    p.write_text("# a core-only fix\n" + p.read_text(encoding="utf-8"), encoding="utf-8")

    r = run_gate(core, "publish", "--dry-run")
    text = out_of(r)
    assert r.returncode == 0, text
    assert "PUBLISH ALLOWED" in text
    assert "-m twine upload" in text
    assert "invisible_core-18.1.0-py3-none-any.whl" in text
    assert "invisible_core-18.1.0.tar.gz" in text
    assert "Nothing was uploaded" in text


# ------------------------------------------------- D1: the first-release claim
#
# Measured: an empty ledger plus --first-release waved a real content change
# through with exit 0, and `publish --first-release --dry-run` printed the twine
# command it authorised. The recipe that gets an operator into that state is the
# literal printf load_ledger prints when the ledger is missing, so it is not a
# hypothetical path - it is the documented one. "Nothing has been published" is
# also the one claim in this whole gate that an index can settle outright, so the
# cross-check is no longer optional on that branch.

def test_d1_first_release_is_refused_when_the_index_already_serves_something(core, tmp_path):
    """The empty ledger was lost, not empty. The index says so, and it is asked."""
    touch_content(core)
    (core / "PUBLISHED.json").write_text(EMPTY_LEDGER, encoding="utf-8")

    r = run_gate(core, "check", "--first-release",
                 "--index-json-url", fake_index(tmp_path, ["17.0.0", "18.0.0"]))
    text = out_of(r)
    assert r.returncode == 1, text
    assert "RELEASE REFUSED" in text
    assert "already serves" in text
    assert "restore" in text.lower()
    assert "PUBLISH ALLOWED" not in text


def test_d1b_first_release_cannot_pass_with_an_unreachable_index(core, tmp_path):
    """Forced means forced: an index the gate cannot read leaves it with no
    opinion, which is exit 2, never the pass it used to be. Release 1 happens at
    a machine that is about to upload to that same index, so requiring it to be
    reachable costs nothing that is not already required."""
    touch_content(core)
    (core / "PUBLISHED.json").write_text(EMPTY_LEDGER, encoding="utf-8")

    r = run_gate(core, "check", "--first-release",
                 "--index-json-url", (tmp_path / "no-such-index.json").as_uri())
    text = out_of(r)
    assert r.returncode == 2, text
    assert "GATE BROKEN" in text
    assert "PUBLISH ALLOWED" not in text


def test_d1c_publish_first_release_authorises_no_upload_without_the_index(core, tmp_path):
    """The dry run printed the exact twine command it was authorising. It must
    not reach that line when the forced cross-check could not run."""
    touch_content(core)
    (core / "PUBLISHED.json").write_text(EMPTY_LEDGER, encoding="utf-8")

    r = run_gate(core, "publish", "--first-release", "--dry-run",
                 "--index-json-url", (tmp_path / "no-such-index.json").as_uri())
    text = out_of(r)
    assert r.returncode == 2, text
    assert "upload NOT attempted" in text
    assert "twine" not in text, "an upload command was formed without the cross-check"


def test_d1d_publish_first_release_is_refused_when_the_index_disagrees(core, tmp_path):
    touch_content(core)
    (core / "PUBLISHED.json").write_text(EMPTY_LEDGER, encoding="utf-8")

    r = run_gate(core, "publish", "--first-release", "--dry-run",
                 "--index-json-url", fake_index(tmp_path, ["18.0.0"]))
    text = out_of(r)
    assert r.returncode == 1, text
    assert "already serves" in text
    assert "upload NOT attempted" in text
    assert "twine" not in text


# ------------------------------------------- D2: the version string is a claim
#
# _entry_for matched on entry['version'] alone and _validate_entry only asked
# that it be a non-empty string, so one edited field - 18.0.0 -> 17.9.0 - made a
# drifted 18.0.0 build look unpublished and exit 0 PUBLISH ALLOWED. Every other
# single-field corruption in this file yields exit 2; this one passed.

def test_d2_a_version_that_contradicts_its_own_filenames_is_gate_broken(core):
    """The entry records the wheel it published. That filename carries the
    version, so the two can be made to agree or the record is unusable."""
    touch_content(core)   # a real drift sitting at 18.0.0
    led = json.loads((core / "PUBLISHED.json").read_text(encoding="utf-8"))
    assert led["released"][0]["wheel_filename"].startswith("invisible_core-18.0.0")
    led["released"][0]["version"] = "17.9.0"
    (core / "PUBLISHED.json").write_text(json.dumps(led), encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 2, text
    assert "GATE BROKEN" in text
    assert "released[0]" in text
    assert "wheel_filename" in text
    assert "PUBLISH ALLOWED" not in text
    assert "RELEASE REFUSED" not in text


@pytest.mark.parametrize("mangle,marker", [
    (lambda e: e.pop("wheel_filename"), "'wheel_filename'"),
    (lambda e: e.pop("sdist_filename"), "'sdist_filename'"),
    (lambda e: e.update({"sdist_filename": "invisible_core-17.9.0.tar.gz"}),
     "'sdist_filename'"),
])
def test_d2b_a_filename_record_that_is_missing_or_wrong_is_gate_broken(core, mangle, marker):
    led = json.loads((core / "PUBLISHED.json").read_text(encoding="utf-8"))
    mangle(led["released"][0])
    (core / "PUBLISHED.json").write_text(json.dumps(led), encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 2, text
    assert "GATE BROKEN" in text
    assert marker in text
    assert "RELEASE REFUSED" not in text


# ----------------------------------- D3: `record` must not rewrite the record
#
# The refusal existed and had no test: deleting it left all 32 cases green.
# Reproduced here as the scenario that reaches it - record, edit a shipped file,
# record again.

def test_d3_record_refuses_to_overwrite_an_entry_whose_digests_differ(core):
    """The ledger is the record of what reached the index. Overwriting an entry
    replaces that with what happens to be in the tree today, and the drift the
    gate exists to catch becomes invisible in one command."""
    before = (core / "PUBLISHED.json").read_text(encoding="utf-8")
    touch_content(core)

    r = run_gate(core, "record")
    text = out_of(r)
    assert r.returncode == 1, text
    assert "already in the ledger with DIFFERENT digests" in text
    assert "invisible_core/prefs.py" in text, "the refusal must name what differs"
    assert (core / "PUBLISHED.json").read_text(encoding="utf-8") == before, \
        "the ledger was rewritten by a run that refused"


def test_d3b_recording_the_same_content_twice_is_a_no_op(core):
    """The other half: `record` is idempotent for an unchanged tree, or the
    refusal above would just make a normal re-run look like a violation."""
    before = (core / "PUBLISHED.json").read_text(encoding="utf-8")

    r = run_gate(core, "record")
    text = out_of(r)
    assert r.returncode == 0, text
    assert "already recorded with these exact digests" in text
    assert (core / "PUBLISHED.json").read_text(encoding="utf-8") == before


# ------------------------------------------ D4: the UPPER bound on normalise()
#
# CW2 and CW2b pin the lower bound - a line-ending flip must not fire, and a real
# edit beside one still must. Nothing pinned the top, so widening normalise() to
# b"".join(blob.split()) left all 32 cases green while a pure re-indent of a .py
# file stopped being a content change. Indentation is semantic in Python.

def _reindent(src: str) -> str:
    """Add one level of indentation to every already-indented line."""
    return "\n".join(("    " + line) if line.startswith("    ") else line
                     for line in src.splitlines()) + "\n"


def test_d4_a_whitespace_only_change_that_is_semantic_is_still_refused(core):
    p = core / "src" / "invisible_core" / "prefs.py"
    src = p.read_text(encoding="utf-8")
    out = _reindent(src)
    assert out != src
    assert "".join(out.split()) == "".join(src.split()), \
        "this test only proves the upper bound if the change is whitespace ONLY"
    p.write_text(out, encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 1, text
    assert "the content changed but the version did not" in text
    assert "changed  wheel: invisible_core/prefs.py" in text


def test_d4b_normalise_folds_line_endings_and_nothing_else(core):
    """Stated on the function rather than through a build, so the boundary is
    readable: CRLF and lone CR fold, every other whitespace difference survives."""
    normalise = gate_module().normalise
    assert normalise(b"a\r\nb") == normalise(b"a\nb") == normalise(b"a\rb")
    for a, b in ((b"    x", b"        x"),      # a re-indent
                 (b"a b", b"a  b"),             # an inner space
                 (b"a\tb", b"a b"),             # a tab
                 (b"a\n", b"a\n\n"),            # a blank line
                 (b"a", b"a\n")):               # a trailing newline
        assert normalise(a) != normalise(b), (a, b)


# ------------------------------- D5: the printed evidence must be the evidence
#
# The verdict came from diff_manifests over the file tables while the refusal
# printed the stored digests as though they were the comparison, so overwriting
# both digests with garbage and leaving the tables intact still read as "nothing
# to release". An entry cannot be half trusted: if it disagrees with itself the
# gate has no opinion.

def test_d5_a_digest_that_contradicts_its_own_file_table_is_gate_broken(core):
    led = json.loads((core / "PUBLISHED.json").read_text(encoding="utf-8"))
    led["released"][0]["wheel"]["digest"] = "0" * 64
    led["released"][0]["sdist"]["digest"] = "0" * 64
    (core / "PUBLISHED.json").write_text(json.dumps(led), encoding="utf-8")

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 2, text
    assert "GATE BROKEN" in text
    assert "released[0]" in text
    assert "nothing to release" not in text
    assert "PUBLISH ALLOWED" not in text


def test_d5b_the_refusal_prints_the_digests_it_actually_compared(core):
    """The two numbers in the refusal are the verdict, not an illustration of
    it: the left one is the ledger's and the right one is this build's."""
    touch_content(core)
    led = json.loads((core / "PUBLISHED.json").read_text(encoding="utf-8"))
    old_wheel = led["released"][0]["wheel"]["digest"]

    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == 1, text
    assert f"wheel      {old_wheel[:16]} -> " in text
    new_wheel = text.split(f"wheel      {old_wheel[:16]} -> ")[1][:16]
    assert new_wheel != old_wheel[:16]

    show = json.loads(run_gate(core, "show").stdout)
    assert show["wheel_digest"].startswith(new_wheel)


# ---------------------------------------- D6: a lone CR that is CONTENT is lost
#
# Deliberate, and the cost is stated rather than discovered. Pinned so that a
# future widening or narrowing of normalise() has to be a decision.

def test_d6_a_lone_cr_inside_a_literal_is_folded_and_that_is_the_choice(core):
    normalise = gate_module().normalise
    assert normalise(b'_LINESEP = "a\rb"\n') == normalise(b'_LINESEP = "a\nb"\n')


def test_d6b_a_build_whose_only_change_is_a_content_cr_reads_as_identical(core):
    """End to end, so the tradeoff is visible where it actually bites: a shipped
    file carrying a lone CR as data can have it turned into a newline and this
    gate will not notice. The alternative - digesting raw bytes - refuses every
    release made on the other side of a CRLF checkout, which is the failure that
    gets gates switched off. A file that really needs a bare CR should spell it
    \\r in the source rather than embed the byte."""
    p = core / "src" / "invisible_core" / "prefs.py"
    p.write_bytes(p.read_bytes() + b'\n_LINESEP = "a\rb"\n')
    set_core_revision(core, 1)
    assert run_gate(core, "record").returncode == 0

    p.write_bytes(p.read_bytes().replace(b'"a\rb"', b'"a\nb"'))
    r = run_gate(core)
    text = out_of(r)
    assert r.returncode == NOTHING_TO_RELEASE, text
    assert "nothing to release" in text.lower()
    assert "the content changed but the version did not" not in text


# ------------------------------------- the eight silences, kept in one place
#
# Every one of these is a thing somebody adds to a repo on an ordinary afternoon.
# If the gate fires on any of them it gets switched off, and then it guards
# nothing at all. Two of them have their own detailed cases above (CW1, CW2);
# they are repeated here so the whole set is provable in one run.

def _flip_tree_to_crlf(root: Path) -> None:
    flipped = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name == "PUBLISHED.json":
            continue
        blob = p.read_bytes()
        if b"\x00" in blob:
            continue
        try:
            blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        crlf = blob.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        if crlf != blob:
            p.write_bytes(crlf)
            flipped += 1
    assert flipped > 5, "nothing was flipped, so this case proves nothing"


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _stray_pyc(root: Path) -> None:
    p = root / "src" / "invisible_core" / "__pycache__" / "prefs.cpython-312.pyc"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00\x0f\r\n" + b"not really bytecode" * 4)


NOISE = [
    ("gitignore", lambda r: _write(r, ".gitignore", "__pycache__/\n*.pyc\ndist/\n")),
    ("gitattributes", lambda r: _write(r, ".gitattributes", "* text=auto\n*.png binary\n")),
    ("crlf_whole_tree", _flip_tree_to_crlf),
    ("the_gate_itself", lambda r: _write(r, "scripts/version_gate.py",
                                         "# an edit to the gate is not a release\n")),
    ("notes_and_makefile", lambda r: (_write(r, "NOTES.md", "# scratch\n"),
                                      _write(r, "Makefile", "test:\n\tpytest -q\n"))),
    ("githooks", lambda r: _write(r, ".githooks/pre-push", "#!/bin/sh\nexit 0\n")),
    ("workflows", lambda r: _write(r, ".github/workflows/publish.yml", "on: push\n")),
    ("stray_pyc", _stray_pyc),
]


@pytest.mark.parametrize("name,noise", NOISE, ids=[n for n, _ in NOISE])
def test_none_of_the_eight_ordinary_afternoons_makes_the_gate_fire(core, name, noise):
    noise(core)

    r = run_gate(core)
    text = out_of(r)
    assert "the content changed but the version did not" not in text, text
    assert "GATE BROKEN" not in text, text
    # The tree is otherwise what was published, so the only verdict left is that
    # there is nothing to release.
    assert r.returncode == NOTHING_TO_RELEASE, text
    assert "nothing to release" in text.lower()


def test_the_default_run_builds_twice_and_the_build_is_reproducible(published_baseline, tmp_path):
    """The whole comparison rests on the build being deterministic. The gate
    checks that itself, and a self-mismatch is exit 2 (gate broken), never exit
    1 (release refused) - a toolchain upgrade must not read as a violation."""
    work = tmp_path / "core"
    shutil.copytree(published_baseline, work)
    cmd = [sys.executable, str(GATE), "--project-root", str(work),
           "--ledger", str(work / "PUBLISHED.json"), "show"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, out_of(r)   # exit 2 here would mean not reproducible
    assert json.loads(r.stdout)["version"] == "18.0.0"


def test_the_index_cross_check_asks_about_THIS_project(monkeypatch):
    """One gate, three projects - and it asked the wrong index for two of them.

    `--index-json-url` declared `default=DEFAULT_INDEX_JSON_URL`, evaluated when
    the parser is BUILT, which is before `main()` resolves which project this
    is. So the flag always arrived non-None holding invisible-core's URL, and
    `_index_url_for` prefers a non-None args value over the resolved global.

    Measured 2026-07-28 running `check --verify-index` in the manager:

        RELEASE REFUSED: the index serves 8 version(s) of invisible_firefox
        that the ledger does not record: 18.0.0 ... 18.7.0

    Those are CORE versions, reported as the manager's. Neither consumer's
    index cross-check had ever asked about its own package.

    Same shape as the `BINARY_VERSION` default-argument bug this file's
    meta-rule is about: a constant bound once, at definition time.

    Driven at `_index_url_for`, not through a whole gate run: which URL the
    gate WOULD use must not depend on ledger state, and for a project whose
    current version is already published byte-identically the gate exits before
    the cross-check and the assertion would pass over nothing.
    """
    import invisible_core.release as R

    seen: list[str] = []
    monkeypatch.setattr(R, "cmd_check",
                        lambda args: (seen.append(R._index_url_for(args)), 0)[1])

    for root, expect in ((REPO_ROOT, "invisible-core"),
                         (REPO_ROOT.parent / "invisible_firefox", "invisible-firefox"),
                         (REPO_ROOT.parent / "invisible_playwright", "invisible-playwright")):
        if not (root / "pyproject.toml").is_file():
            pytest.skip("not the workbench - the sibling repos are not here")
        seen.clear()
        R.main(["--project-root", str(root), "check", "--verify-index"])
        assert seen, f"{root.name}: the gate never resolved an index URL"
        url = seen[0]
        assert f"/pypi/{expect}/" in url, (
            f"{root.name} would cross-check against {url} - a gate that asks "
            f"another package's index answers confidently about the wrong thing")

    # And an explicit flag still wins, because a --repository upload lands on
    # another index and has to be checkable against it.
    seen.clear()
    R.main(["--project-root", str(REPO_ROOT), "check",
            "--index-json-url", "https://example.invalid/pypi/x/json"])
    assert seen and seen[0] == "https://example.invalid/pypi/x/json", seen


def test_a_recorded_entry_carries_every_field_the_ledger_promises():
    """The ledger had two shapes, and nine of eleven entries had the richer one.

    `record` wrote seven fields; the entries back-filled by hand from the index
    carried eight, the extra one being `requires_dist`. Nothing broke, because
    `check` reads requires_dist out of the artifact it just built and never out
    of the ledger - which is exactly why the drift could last: a field that is
    written by one path, absent from another and read by neither.

    It matters anyway. The ledger is the only record of what a published version
    DECLARED it needed once the tag has moved on, and this project's entire
    coupling is an exact pin, so "which core did 0.4.4 ask for" is the first
    question asked about any release that behaved oddly.

    Asserted as an exact set, not a subset: a subset check passes while the entry
    quietly grows a field that half the readers do not know about, which is the
    shape this test exists to close.
    """
    import inspect

    from invisible_core import release

    src = inspect.getsource(release.cmd_record)
    start = src.index('ledger["released"].append(')
    written = set(re.findall(r'"(\w+)":', src[start:src.index("})", start)]))

    expected = {"version", "seal_tag", "published_at", "requires_dist",
                "wheel_filename", "sdist_filename", "wheel", "sdist"}
    assert written == expected, (
        f"the recorded entry writes {sorted(written)}; the ledger's shape is "
        f"{sorted(expected)}. Adding a field means every reader of an older entry "
        f"has to tolerate its absence; removing one means a published version "
        f"stops being able to answer a question about itself.")


def test_every_ledger_entry_in_this_repo_has_the_fields_the_gate_needs():
    """The gate refuses a ledger entry with no `wheel_filename` by name: "it is
    the only thing in the entry that corroborates the version string - without it
    one edited field turns a published version into an unpublished one".

    That refusal was met by a hand-written back-fill on 2026-08-02, which is the
    only way an entry gets written outside `record`. This walks what is actually
    on disk rather than what the writer intends.
    """
    import json

    ledger = json.loads((REPO_ROOT / "PUBLISHED.json").read_text(encoding="utf-8"))
    required = {"version", "published_at", "wheel_filename", "sdist_filename",
                "wheel", "sdist"}
    bad = {}
    for entry in ledger["released"]:
        missing = sorted(required - set(entry))
        if missing:
            bad[entry.get("version", "?")] = missing
    assert not bad, f"ledger entries missing fields the gate reads: {bad}"
