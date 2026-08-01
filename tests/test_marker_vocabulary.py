"""One marker vocabulary for the three repositories, and it stays one.

WHAT WENT WRONG. `integration` meant "several modules together, no browser" in
`invisible_core` and `invisible_playwright`, and "launches the real patched
Firefox binary named by the seal" in `invisible_firefox`. Nothing said so; the
two definitions simply sat in two pyprojects.

The consequences were not cosmetic:

  * the manager's default selection was `not integration and not e2e`, correct
    for ITS meaning and wrong for the shared one, so its every-push suite was
    shaped by a rule written for one test out of eighty-five;
  * CLAUDE.md's "integration is reserved for explicit release runs" was true in
    one repo and false in the other two, where the marker names the tests that
    cover the release wiring - exactly the ones a push most needs;
  * `invisible_core`'s addopts excluded `slow` while never declaring it, so the
    exclusion matched nothing and the word meant nothing;
  * a hook comment written on 2026-07-27, copied from the manager into the
    module that now speaks for all three, carried the wrong contract with it.

Nothing could have caught any of that: three pyprojects, no comparison. This is
the comparison. It reads the sibling repos when the workbench is present and
skips otherwise, the same shape as the pre-push stub check next door - an
installed copy has no siblings and no opinion about them.
"""
from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPOS = ["invisible_core", "invisible_playwright", "invisible_firefox"]
_RELEASE = Path(__file__).resolve().parents[2]


def _pytest_config(repo: str) -> dict:
    path = _RELEASE / repo / "pyproject.toml"
    if not path.is_file():
        pytest.skip("not the workbench - the sibling repos are not here")
    with path.open("rb") as fh:
        return tomllib.load(fh)["tool"]["pytest"]["ini_options"]


def test_all_three_declare_the_same_markers():
    got = {r: _pytest_config(r)["markers"] for r in _REPOS}
    first = got[_REPOS[0]]
    for repo, markers in got.items():
        assert markers == first, (
            f"{repo}'s marker vocabulary has drifted from {_REPOS[0]}'s.\n"
            f"  only here:  {sorted(set(markers) - set(first))}\n"
            f"  only there: {sorted(set(first) - set(markers))}\n"
            f"A marker that means two things selects two different sets under "
            f"one name, and nothing downstream can tell which one it got.")


def test_all_three_run_the_same_selection_by_default():
    """The default selection IS the pre-push gate: `invisible_core.hooks` runs
    a bare `pytest -q` in each repo, so whatever addopts says is what "never
    push red" means there."""
    got = {r: _pytest_config(r)["addopts"] for r in _REPOS}
    first = got[_REPOS[0]]
    assert all(v == first for v in got.values()), got


def test_every_excluded_marker_is_actually_declared():
    """`-m 'not slow'` against an undeclared marker excludes nothing and reads
    like a rule. That was live in invisible_core: `slow` was excluded, never
    declared, and therefore meaningless."""
    for repo in _REPOS:
        cfg = _pytest_config(repo)
        declared = {m.split(":")[0].strip() for m in cfg["markers"]}
        excluded = {w for w in cfg["addopts"].replace("'", " ").split()
                    if w in ("slow", "e2e", "integration", "unit", "linux_only")}
        missing = excluded - declared
        assert not missing, (
            f"{repo} excludes {sorted(missing)} by default without declaring "
            f"it, so the exclusion matches nothing")


def test_strict_markers_is_on_so_a_typo_cannot_be_silent():
    """Without it, `@pytest.mark.uint` is a warning nobody reads: the test still
    runs, and every `-m unit` selection quietly misses it. The manager had a
    real instance - a marker used by `test_process_layer.py` and never
    registered."""
    for repo in _REPOS:
        assert "--strict-markers" in _pytest_config(repo)["addopts"], repo


# ---------------------------------------------- the suite-collapse tripwire

def test_every_repo_refuses_a_run_in_which_nothing_ran():
    """A selection that collapses is a green tick, and pytest cannot see it.

    `-m` with a typo'd marker, a `norecursedirs` that swallowed the tests, an
    addopts edit - all of them exit 0 having run almost nothing. Each repo's CI
    therefore reads the count back out of the report and refuses below a floor.

    THIS repo did not have one. Found 2026-07-28 while fixing the wrapper's,
    which fired correctly when 322 tests moved out and the floor stayed at 600.
    The core - the package both consumers pin - was the only one of the three
    without the guard, which is the same asymmetry that had left it as the only
    one whose pre-push hook ran no tests at all.

    Asserted on the CI workflow that runs the DEFAULT selection, not on every
    workflow: an e2e or install job legitimately runs a handful.
    """
    import re

    wanted = {
        "invisible_core": ".github/workflows/ci.yml",
        "invisible_playwright": ".github/workflows/tests.yml",
        "invisible_firefox": ".github/workflows/ci.yml",
    }
    missing = []
    for repo, rel in wanted.items():
        path = _RELEASE / repo / rel
        if not path.is_file():
            pytest.skip("not the workbench - the sibling repos are not here")
        text = path.read_text(encoding="utf-8")
        m = re.search(r"if passed < (\d+)", text)
        if not m:
            missing.append(f"{repo}: {rel} never compares the count")
            continue
        floor = int(m.group(1))
        if floor < 1:
            missing.append(f"{repo}: floor is {floor}, which refuses nothing")
    assert not missing, (
        "a repository's CI cannot tell a collapsed selection from a pass:\n  "
        + "\n  ".join(missing))


# ------------------------ the CI must arrange what the suite asserts

#: The workflow in each repo that runs the DEFAULT selection - the same mapping
#: the collapse tripwire above uses, and for the same reason: an e2e or install
#: job legitimately runs a handful of tests and arms nothing.
_DEFAULT_SUITE_WORKFLOW = {
    "invisible_core": ".github/workflows/ci.yml",
    "invisible_playwright": ".github/workflows/tests.yml",
    "invisible_firefox": ".github/workflows/ci.yml",
}


def test_a_repo_whose_suite_demands_armed_hooks_arms_them_in_ci():
    """The assertion is shared; the thing that satisfies it was not.

    MEASURED 2026-07-28. `assert_hooks_are_armed` moved into
    `invisible_core.testing` so that all three repos could make the claim, and
    all three began making it. Only `invisible_core`'s workflow ran
    `install_hooks.py` - it had needed that step since the day its own version of
    the test was written. So the wrapper and the manager went red on every push,
    on all four matrix legs, with a message about `core.hooksPath` that reads
    like a developer's mistake rather than a missing CI step.

    A GitHub checkout IS a git work tree, which is why the helper's "skip outside
    a git checkout" guard cannot cover this: the honest arrangement is for CI to
    do what a developer does once, not for the assertion to quietly excuse
    itself. Arming it on the runner also means `install_hooks.py` is exercised on
    every leg instead of only on the one machine that ever ran it by hand.

    This test is the comparison that did not exist: whoever adds the assertion to
    a fourth repo gets told about the step in the same run.
    """
    import re

    demanded, armed, missing = [], [], []
    for repo in _REPOS:
        root = _RELEASE / repo
        if not root.is_dir():
            pytest.skip("not the workbench - the sibling repos are not here")
        tests_dir = root / "tests"
        wants = any(
            "assert_hooks_are_armed" in path.read_text(encoding="utf-8", errors="ignore")
            for path in tests_dir.rglob("test_*.py")) if tests_dir.is_dir() else False
        if not wants:
            continue
        demanded.append(repo)
        wf = root / _DEFAULT_SUITE_WORKFLOW[repo]
        if not wf.is_file():
            missing.append(f"{repo}: {_DEFAULT_SUITE_WORKFLOW[repo]} does not exist")
            continue
        text = wf.read_text(encoding="utf-8")
        # Either spelling: the core checks first and installs on failure, the
        # consumers just install. What matters is that the step is there.
        if re.search(r"install_hooks\.py", text):
            armed.append(repo)
        else:
            missing.append(
                f"{repo}: its suite calls assert_hooks_are_armed but "
                f"{_DEFAULT_SUITE_WORKFLOW[repo]} never runs install_hooks.py, so "
                f"every CI run fails on core.hooksPath being unset")

    assert demanded, (
        "no repo's suite asserts the hooks are armed any more. If that assertion "
        "was deliberately dropped, delete this test with it - otherwise it is "
        "passing over an empty set")
    assert not missing, "\n  ".join(["the CI does not arrange what the suite demands:"]
                                    + missing)


# ------------- the install-e2e files must collect with only pytest present

#: The workflow that runs a stranger's install, in each repo.
_USER_INSTALL_WORKFLOW = ".github/workflows/user-install.yml"

#: What the runner actually has. `user-install.yml` installs pip, pytest and
#: packaging and NOTHING else, deliberately - the package under test goes into a
#: venv the fixture builds from the index, and a second copy on the runner's path
#: would mean every assertion read the wrong one.
_ON_THE_RUNNER = {"pytest", "packaging", "pip"}

_FIRST_PARTY = {"invisible_core", "invisible_playwright", "invisible_firefox"}


def _install_e2e_files(root: Path) -> list[Path]:
    """The test files that repo's user-install workflow names, read from the
    workflow rather than guessed - a hand-kept list is a shorter list that
    drifts, which is the failure mode this whole file exists for."""
    import re

    wf = root / _USER_INSTALL_WORKFLOW
    if not wf.is_file():
        return []
    found = []
    for hit in re.findall(r"tests/[A-Za-z0-9_./-]+\.py", wf.read_text(encoding="utf-8")):
        path = root / hit
        if path.is_file() and path not in found:
            found.append(path)
    return found


def test_no_install_e2e_file_imports_a_package_the_runner_does_not_have():
    """MEASURED 2026-07-28, on all three repos, twice in one day.

    The venv helpers moved into `invisible_core.testing` so the three suites
    could share them, and these files imported them from there. On a development
    machine that is fine - an editable install is always on the path. On the
    runner it is `ModuleNotFoundError` at COLLECTION, so the job goes red having
    tested nothing, and the message names a module rather than the mistake.

    The core hit it first and grew a guard inside its own install-e2e file, which
    is where a guard has to live if it must run WITHOUT imports. That copy could
    not be shared, so the two consumers hit the identical wall hours later.

    This is the version that covers all of them: it runs in the ordinary suite,
    where importing is fine, and PARSES the files. The list of files comes out of
    each repo's workflow, so a file added to the job is covered the same day.

    Skipped outside the workbench - an installed copy has no sibling repos.
    """
    checked, offenders = [], []
    for repo in _REPOS:
        root = _RELEASE / repo
        if not root.is_dir():
            pytest.skip("not the workbench - the sibling repos are not here")
        for path in _install_e2e_files(root):
            checked.append(f"{repo}/{path.relative_to(root).as_posix()}")
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # Module level only. Inside a function is fine, and is how these
                # files reach the VENV's copy through a subprocess.
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if node.col_offset != 0:
                    continue
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""])
                for name in names:
                    top = name.split(".")[0]
                    if top in _FIRST_PARTY or (
                            top not in _ON_THE_RUNNER
                            and top not in sys.stdlib_module_names):
                        offenders.append(
                            f"{repo}/{path.relative_to(root).as_posix()} "
                            f"line {node.lineno}: {name}")

    assert checked, (
        "no install-e2e file was found in any repo's user-install workflow. "
        "Either the workflows stopped naming test files - in which case this "
        "test is passing over an empty set - or the pattern that finds them "
        "no longer matches")
    assert not offenders, (
        "these files import something the user-install runner does not have, at "
        "module level:\n  " + "\n  ".join(offenders) +
        f"\n\nThe runner has only {sorted(_ON_THE_RUNNER)}, so this is a "
        f"collection error: the job reports failure having run nothing. Keep the "
        f"helpers local in these files - the duplication is the point.")


def test_every_repo_runs_the_undefined_name_check():
    """F821 and F811 in CI, in all three, or the class comes straight back.

    Both rules earned their place on 2026-07-28. `throwaway_venv` was used at one
    line of the manager's install e2e and imported nowhere, which took that job
    red at RUNTIME - after a parse-level guard had passed it, because the name
    resolves fine on any machine where the shared helper is importable. And six
    annotations in the wrapper named `Renderer`, a type defined in no module in
    the repository: harmless only because `from __future__ import annotations`
    never evaluates an annotation, so nothing ever tried to resolve it.

    Neither is a style question and neither is findable by running tests, which
    is why this is the pair rather than a full lint config. There were ZERO
    violations in all three repos when the step went in, so it is a gate and not
    a backlog - if it ever starts failing, fix the name, do not widen the select.
    """
    import re

    missing = []
    for repo, rel in _DEFAULT_SUITE_WORKFLOW.items():
        path = _RELEASE / repo / rel
        if not path.is_file():
            pytest.skip("not the workbench - the sibling repos are not here")
        text = path.read_text(encoding="utf-8")
        m = re.search(r"ruff check[^\n]*--select\s+(\S+)", text)
        if not m:
            missing.append(f"{repo}: {rel} never runs ruff check --select")
            continue
        selected = set(m.group(1).split(","))
        absent = sorted({"F821", "F811"} - selected)
        if absent:
            missing.append(f"{repo}: {rel} selects {sorted(selected)}, missing {absent}")
    assert not missing, (
        "an undefined name would reach main unchallenged in:\n  " + "\n  ".join(missing))


def test_no_test_uses_a_globally_routable_placeholder_ip():
    """`1.2.3.4` belongs to someone. RFC 5737 exists for this.

    Fourteen uses across four files, in all three repos, as the stand-in for an
    egress IP - open item 11. Not a leak: it is nobody's real address here. The
    cost is the scanner. A secrets sweep that whitelists the documentation ranges
    keeps flagging these forever, and a check whose output is always noise is one
    that stops being read, which is how a real address eventually gets through.

    192.0.2.0/24, 198.51.100.0/24 and 203.0.113.0/24 are reserved for exactly this
    and can never route anywhere. The rest of these suites already used them; the
    four files simply predated the convention.

    An address is recognised by its OCTETS, not by being quoted. The first cut
    required a quote either side, and `1.2.3.4` inside a proxy URL - which is how
    `test_sx.py` used it - has quotes around the whole URL: the mutation putting
    that one address back SURVIVED. Four octets in 0-255 is what an IP is.
    """
    import re

    candidate = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")

    def is_documentation_or_private(a, b, c, d):
        if a in (0, 10, 127, 255):
            return True
        if a == 192 and b == 168:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
        if (a, b, c) in ((192, 0, 2), (198, 51, 100), (203, 0, 113)):
            return True
        if a == 169 and b == 254:                      # link-local
            return True
        return False

    def prose_lines(text):
        """Line numbers covered by a docstring, derived with ast.

        Comments are skipped by their `#`; a docstring needs parsing. This test's
        OWN docstring names the address it is about, and the first version of the
        check duly reported this file - a gate that cannot describe itself without
        failing is one somebody will silence.
        """
        covered = set()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return covered
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = getattr(node, "body", None)
            if not body or not isinstance(body[0], ast.Expr):
                continue
            val = body[0].value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                covered.update(range(val.lineno, (val.end_lineno or val.lineno) + 1))
        return covered

    offenders = []
    for repo in _REPOS:
        tests = _RELEASE / repo / "tests"
        if not tests.is_dir():
            pytest.skip("not the workbench - the sibling repos are not here")
        for path in sorted(tests.rglob("test_*.py")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            prose = prose_lines(text)
            for i, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith("#") or i in prose:
                    continue
                for m in candidate.finditer(line):
                    octets = tuple(int(x) for x in m.groups())
                    if any(o > 255 for o in octets):   # not an address at all
                        continue
                    if is_documentation_or_private(*octets):
                        continue
                    offenders.append(f"{repo}/{path.name}:{i}: {m.group(0)}")
    assert not offenders, (
        "these tests use a globally routable address as a placeholder; use an RFC "
        "5737 range (192.0.2.x, 198.51.100.x, 203.0.113.x) so a secrets scan can "
        "whitelist them:" + chr(10) + "  " + (chr(10) + "  ").join(offenders))


def test_every_user_install_workflow_waits_for_the_index_on_a_release():
    """The most expensive kind of red: one that looks like the real defect.

    `user-install.yml` runs on `release:`, which fires seconds after the upload,
    and PyPI does not serve a new version to pip immediately. MEASURED
    2026-07-28: `invisible-firefox` 0.2.6 was uploaded, the release created about
    forty seconds later, and the job's `pip install` still resolved 0.2.5 - so it
    reported a forgotten publish that had happened two minutes earlier, which is
    exactly the failure this workflow exists to catch. Indistinguishable.

    The wait lives in the workflow rather than in the four install-e2e test files:
    the race only exists on that one event, and those files may not import a
    shared helper (the runner has no invisible_core - see the gate above), so a
    Python version would be four copies of a poll loop.

    Two properties, not one. It must be there, and it must be BOUNDED: an
    unbounded wait converts a genuinely forgotten publish from a red run into a
    job that hangs until the runner's own timeout, which is worse than the flake.
    """
    import re

    missing = []
    for repo in _REPOS:
        path = _RELEASE / repo / ".github" / "workflows" / "user-install.yml"
        if not path.is_file():
            pytest.skip("not the workbench - the sibling repos are not here")
        text = path.read_text(encoding="utf-8")
        if "wait for the index" not in text:
            missing.append(f"{repo}: user-install.yml never waits for the index")
            continue
        if "github.event_name == 'release'" not in text:
            missing.append(f"{repo}: the wait is not scoped to the release event, so "
                           f"a scheduled run would poll for a real absence")
        if not re.search(r"deadline\s*=\s*time\.monotonic\(\)\s*\+\s*\d+", text):
            missing.append(f"{repo}: the wait has no deadline; a forgotten publish "
                           f"would hang the job instead of failing it")
    assert not missing, "\n  ".join(
        ["a publish would race its own verification in:"] + missing)


# ------------------- control characters, the self-inflicted one

#: Bytes that have no business in a source or documentation file. Tab and the two
#: newline bytes are excluded because they are legitimate whitespace; everything
#: else in C0 is either invisible or actively breaks the file.
_FORBIDDEN_BYTES = (set(range(0x00, 0x09)) | {0x0B, 0x0C}
                    | set(range(0x0E, 0x20)))

_TEXT_SUFFIXES = {".py", ".md", ".txt", ".yml", ".yaml", ".toml", ".json", ".cfg"}


def test_no_tracked_text_file_carries_an_invisible_control_character():
    r"""The defect this project keeps inflicting on itself.

    Authoring a file through a shell heredoc turns backslash escapes into the
    bytes they name. It happened FOUR times on 2026-07-28 alone:

      * `\b` became 0x08 inside a regex in the wrapper's `test_readme_claims.py`,
        so the pattern matched nothing and the test collected zero parameters;
      * `\f` and `\t` became 0x0C and 0x09 inside three Windows paths in
        `18-gate-inventory.md` and `80-decision-log.md`, where `C:\ff\source\...`
        rendered as `C:` + a formfeed + `f\source` + a tab. They sat there for
        DAYS reading as perfectly ordinary paths, in the row that told a reader
        where the make_seal gate's known-bad input lived;
      * and earlier the same week `\n` and `\0` broke four test files outright.

    The formfeed case is the one that makes this worth a gate rather than a habit:
    a corrupted path is INVISIBLE. It does not fail, it does not look wrong, and
    the reader who follows it finds nothing and concludes the documentation is
    unreliable. Python's own `splitlines()` even splits on 0x0C, so line numbers
    reported by tooling stop matching the editor.

    Zero violations across all three repos when this was written, so it is a gate
    and not a cleanup. Scoped to text suffixes: a `.png` or a `.ttf` is full of
    these bytes legitimately.
    """
    import subprocess

    # The WORKBENCH docs folder is in scope, and that is not an afterthought: the
    # three corrupted Windows paths that motivated this check were in
    # `18-gate-inventory.md` and `80-decision-log.md`, which live there and in no
    # published package. A gate scoped to the three repos would have missed every
    # instance it was written for. Measured while writing it, and again ten minutes
    # later, when the decision-log entry DESCRIBING the bug was itself written with
    # a formfeed, a backspace and a NUL in it.
    roots = [_RELEASE / repo for repo in _REPOS]
    workbench_docs = _RELEASE.parent / "docs"
    if workbench_docs.is_dir():
        roots.append(workbench_docs)

    offenders = []
    for root in roots:
        repo = root.name if root.name != "docs" else "workbench/docs"
        if not root.is_dir():
            pytest.skip("not the workbench - the sibling repos are not here")
        listing = subprocess.run(["git", "-C", str(root), "ls-files"],
                                 capture_output=True, text=True, timeout=120)
        assert listing.returncode == 0, listing.stderr
        names = [n for n in listing.stdout.splitlines() if n.strip()]
        assert names, f"{repo}: git tracks no files, so this check saw nothing"
        for rel in names:
            path = root / rel
            if path.suffix.lower() not in _TEXT_SUFFIXES or not path.is_file():
                continue
            try:
                blob = path.read_bytes()
            except OSError:
                continue
            bad = sorted({b for b in blob if b in _FORBIDDEN_BYTES})
            if bad:
                line = blob[:blob.index(bytes([bad[0]]))].count(b"\n") + 1
                offenders.append(
                    f"{repo}/{rel} line ~{line}: "
                    + ", ".join(f"0x{b:02X}" for b in bad))
    assert not offenders, (
        "these tracked text files contain invisible control characters, which is "
        "what a shell heredoc does to a backslash escape:\n  "
        + "\n  ".join(offenders) +
        "\n\nA 0x0C inside a Windows path renders as a plausible path and points "
        "nowhere. Rewrite the file with the Edit tool or read the text from a "
        "file, never by embedding escapes in a heredoc.")


def test_no_string_LITERAL_evaluates_to_a_control_character():
    r"""The subtler half, and the one that caught this test's own docstring.

    The check above reads file BYTES. A file can be byte-clean and still carry a
    corrupted string: `"C:\ff\source"` in a non-raw literal is four characters of
    path plus a FORMFEED, because Python resolves `\f` when it compiles. Nothing
    is wrong with the bytes on disk; the value the program uses is wrong.

    MEASURED while writing the byte check: its own docstring explained the bug
    using `C:\ff\source\...` in a non-raw string, so the docstring a reader sees
    contained 0x00, 0x08 and 0x0C. Python emitted one SyntaxWarning about `\s`
    and said nothing about the three valid escapes, because `\f` and `\b` ARE
    valid - they just never mean what a Windows path means.

    So: no string literal in these suites may evaluate to a C0 control character
    other than newline or tab. A Windows path wants a raw string or forward
    slashes; a literal that genuinely needs a formfeed can spell it `chr(12)` and
    say why.

    Parsed rather than compiled: reading these files with `ast` gets the resolved
    VALUE of every literal without importing anything.
    """
    import subprocess
    import warnings

    # Newline, tab and CARRIAGE RETURN. The first run flagged eight literals and
    # every one was a deliberate CRLF: SDP is specified with CRLF line endings
    # (`test_webrtc_realness.py`) and so is the checksum file format
    # (`test_download.py`). A gate whose first output is eight false positives is
    # one somebody switches off, so CR belongs here with the other two.
    #
    # What is left is the set no text format uses: NUL, backspace, vertical tab,
    # formfeed and the rest of C0. Those only ever appear by accident.
    allowed = {chr(10), chr(9), chr(13)}
    offenders = []
    for repo in _REPOS:
        root = _RELEASE / repo
        if not root.is_dir():
            pytest.skip("not the workbench - the sibling repos are not here")
        listing = subprocess.run(["git", "-C", str(root), "ls-files", "*.py"],
                                 capture_output=True, text=True, timeout=120)
        assert listing.returncode == 0, listing.stderr
        names = [n for n in listing.stdout.splitlines() if n.strip()]
        assert names, f"{repo}: git tracks no Python files; this check saw nothing"
        for rel in names:
            path = root / rel
            if not path.is_file():
                continue
            try:
                with warnings.catch_warnings():
                    # An invalid escape is a SyntaxWarning, and the point of this
                    # test is that the VALID ones are the dangerous half.
                    warnings.simplefilter("ignore", SyntaxWarning)
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                bad = sorted({c for c in node.value
                              if ord(c) < 0x20 and c not in allowed})
                if bad:
                    offenders.append(
                        f"{repo}/{rel} line {node.lineno}: "
                        + ", ".join(f"0x{ord(c):02X}" for c in bad))
    assert not offenders, (
        "these string literals evaluate to a control character, which is what a "
        "backslash in a non-raw literal does to a Windows path:\n  "
        + "\n  ".join(offenders) +
        "\n\nUse a raw string, forward slashes, or chr() with a comment. The file "
        "bytes look correct in every one of these, which is why the byte check "
        "next door cannot see them.")


def test_the_core_checks_daily_that_its_published_seals_still_resolve():
    """The structural half of issue #51.

    A published version whose seal names a DELETED engine release is a version
    nobody can make work: pip resolves it, the browser can never be downloaded.
    PyPI escaped that in August 2026 only because the retirement (everything
    below firefox-14) happened while every published core still named
    firefox-18. Escaping by luck is not a property.

    So `user-install.yml` asks the question directly, daily, against every
    version the index still serves. This asserts the step is there and that it
    fails on a missing tag rather than merely printing - the shape that made
    every earlier fail-open in this codebase.
    """
    path = _RELEASE / "invisible_core" / ".github" / "workflows" / "user-install.yml"
    if not path.is_file():
        pytest.skip("not the workbench")
    text = path.read_text(encoding="utf-8")
    # Whitespace-collapsed, because the phrases below are wrapped across source
    # lines inside the embedded script. The first version of this test searched
    # the raw text and failed on its own subject - the guard was there, split by
    # a line break and an indent.
    # Scoped to THIS step, not the whole file. `sys.exit(1)` also appears in the
    # suite-collapse step further down, so a whole-file search was satisfied by
    # an unrelated line: deleting this step's own exit left the check green.
    # Measured 2026-08-01, the third time today that a passing mention elsewhere
    # satisfied a gate.
    marker = "- name: every published seal points at an engine that still exists"
    assert marker in text, (
        "the daily check that a published seal still resolves is gone. Without it, "
        "retiring an engine strands every version sealed to it and we find out "
        "from a user")
    start = text.index(marker)
    nxt = text.find(chr(10) + "      - name:", start + 1)
    step = text[start:nxt if nxt != -1 else len(text)]
    flat = " ".join(step.split())
    assert "every published seal points at an engine that still exists" in flat, (
        "the daily check that a published seal still resolves is gone. Without it, "
        "retiring an engine strands every version sealed to it and we find out "
        "from a user")
    assert "sys.exit(1)" in flat, (
        "the check no longer exits non-zero, so a dead engine would be a green run "
        "with a warning in the log")
    # Fragments that live inside ONE string literal. "refusing to call that a
    # pass" is split across two adjacent literals in the script, so even a
    # whitespace-collapsed search sees `refusing to " "call that a pass` - the
    # second thing this test got wrong about its own subject.
    for guard in ("verified nothing", "no engine releases found"):
        assert guard in flat, (
            f"the empty-set refusal {guard!r} is gone: an API that returns nothing, "
            f"or an index with no sealed wheels, would read as a clean bill")
