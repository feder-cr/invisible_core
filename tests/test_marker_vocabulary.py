"""One marker vocabulary for the surviving repositories, and it stays one.

It was THREE repositories until 2026-08-18, when `invisible_firefox` was
deleted. The history below is left as it happened - the incident is what bought
this file - but the list it runs over is now `invisible_core` and
`invisible_playwright`. See `_REPOS`.

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

#: TWO since 2026-08-18, not three. `invisible_firefox` was deleted that day -
#: the GitHub repository is gone and so is the checkout beside this one. The
#: package remains on PyPI, unyanked, but there is no tree here to compare
#: against, and that is what every check in this file reads.
#:
#: THE REMOVAL HAD TO BE MADE HERE, and leaving the name in was not the safe
#: option. Every helper below calls `pytest.skip` the moment ONE listed repo is
#: absent, so a dead entry did not narrow these checks - it turned all of them
#: off. Measured the same day, before this list was corrected: 22 of 22 skips in
#: `pytest -q` read "not the workbench - the sibling repos are not here", ON the
#: workbench, and the marker/addopts/strict-markers comparison between the two
#: SURVIVING repos had stopped running entirely. A green suite that compares
#: nothing is the exact failure this file was written to end.
_REPOS = ["invisible_core", "invisible_playwright"]
_RELEASE = Path(__file__).resolve().parents[2]


def _e_vendorizzato(rel: str) -> bool:
    """Il file appartiene al fork Playwright vendorizzato (``_pw`` o ``_driver``)?

    Da quando il fork e' in git (2026-08-26, voce 23 di 72-next-steps.md), questi
    controlli lo scansionano insieme al nostro codice. Ma il fork non e' nostro:
    il suo ``_pw/_impl/_transport.py`` usa sequenze ANSI ``\\x1b[...`` per colorare
    l'output di terminale, byte pulite ma il cui VALORE contiene 0x1B. Questo gate
    esiste per cogliere la NOSTRA corruzione da heredoc, non per fare il linter del
    codice di Microsoft: il fork si esclude, come ``tests/playwright-upstream`` e'
    gia' escluso dall'sdist per la stessa ragione (non e' roba nostra).
    """
    parti = rel.replace("\\", "/").split("/")
    return "_pw" in parti or "_driver" in parti

# Where the patched Firefox source lives, per `10-repo-layout.md`. Two entries
# because the two build trees are SEPARATE clones, Windows and WSL. Only used
# to resolve test names the workbench docs cite from that repo; a machine that
# has neither skips those checks rather than reporting a name as missing.
_FIREFOX_SOURCE_CANDIDATES = (
    Path("C:/ff/source"),
    Path.home() / "ff-build" / "firefox-150",
)


def _pytest_config(repo: str) -> dict:
    path = _RELEASE / repo / "pyproject.toml"
    if not path.is_file():
        pytest.skip("not the workbench - the sibling repos are not here")
    with path.open("rb") as fh:
        return tomllib.load(fh)["tool"]["pytest"]["ini_options"]


def test_every_repo_declares_the_same_markers():
    got = {r: _pytest_config(r)["markers"] for r in _REPOS}
    first = got[_REPOS[0]]
    for repo, markers in got.items():
        assert markers == first, (
            f"{repo}'s marker vocabulary has drifted from {_REPOS[0]}'s.\n"
            f"  only here:  {sorted(set(markers) - set(first))}\n"
            f"  only there: {sorted(set(first) - set(markers))}\n"
            f"A marker that means two things selects two different sets under "
            f"one name, and nothing downstream can tell which one it got.")


def test_every_repo_runs_the_same_selection_by_default():
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

#: `invisible_firefox` STAYS here after its 2026-08-18 deletion, unlike in
#: `_REPOS`. This set is a blocklist, not a worklist: it names what the
#: user-install runner does NOT have, so a leftover module-level import of the
#: dead package is still something this gate should report rather than wave
#: through. Removing the name would only make the check blinder.
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
    """F821, F811 and F601 in CI, in all three, or the class comes straight back.

    Both rules earned their place on 2026-07-28. `throwaway_venv` was used at one
    line of the manager's install e2e and imported nowhere, which took that job
    red at RUNTIME - after a parse-level guard had passed it, because the name
    resolves fine on any machine where the shared helper is importable. And six
    annotations in the wrapper named `Renderer`, a type defined in no module in
    the repository: harmless only because `from __future__ import annotations`
    never evaluates an annotation, so nothing ever tried to resolve it.

    F601 joined them on 2026-08-01, for the same reason and after the same kind
    of incident: a dict literal with a REPEATED KEY silently keeps only the last
    one. Adding a row to the consumer contract under a key that already appeared
    later in the same literal discarded it without a word, and the test it was
    meant to satisfy went on failing with a message that named the very entry
    just written. Python does not warn, and no test can see the entry that was
    thrown away. Zero violations in all three repos when it went in.

    None of the three is a style question and none is findable by running tests,
    which is why this is a short list rather than a full lint config. All were at
    ZERO violations in all three repos when the step went in, so it is a gate and
    not a backlog - if it starts failing, fix the code, do not widen the select.
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
        absent = sorted({"F821", "F811", "F601"} - selected)
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
            if _e_vendorizzato(rel):
                continue
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
            if _e_vendorizzato(rel):
                continue
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


def test_every_python_a_repo_promises_is_actually_run():
    """`requires-python` is a promise; the CI matrix is what keeps it.

    Measured 2026-08-01, with all three declaring `>=3.11`: the core ran 3.12
    only, the manager ran 3.12 only, the wrapper ran 3.11 and 3.12. Three
    quarters of the promise was untested, and the user who opened issue #51 was
    on 3.14 - a version no job in any of the three repositories had ever executed
    a line on.

    The check is CONTIGUITY FROM THE FLOOR, not a hardcoded list: a repo decides
    where its ceiling is, but it may not skip a version in between, and it may
    not raise the floor without the matrix following. Both are silent otherwise -
    the wheel installs and a user finds out.
    """
    import re

    floor_re = re.compile(r'requires-python\s*=\s*"\s*>=\s*(\d+)\.(\d+)')
    axis_re = re.compile(r'(?m)^\s*python:\s*\[([^\]]*)\]')
    version_re = re.compile(r'"(\d+\.\d+)"')

    problems = []
    for repo, rel in _DEFAULT_SUITE_WORKFLOW.items():
        wf = _RELEASE / repo / rel
        pyproject = _RELEASE / repo / "pyproject.toml"
        if not wf.is_file() or not pyproject.is_file():
            pytest.skip("not the workbench - the sibling repos are not here")

        m = floor_re.search(pyproject.read_text(encoding="utf-8"))
        if not m:
            problems.append(repo + ": no `requires-python = >=X.Y` to compare against")
            continue
        floor = (int(m.group(1)), int(m.group(2)))

        axis = axis_re.search(wf.read_text(encoding="utf-8"))
        if not axis:
            problems.append(
                repo + ": " + rel + " has no `python:` matrix axis, so it runs one version")
            continue
        got = sorted(tuple(int(n) for n in v.split("."))
                     for v in version_re.findall(axis.group(1)))
        if not got:
            problems.append(repo + ": the python axis parsed empty")
            continue

        want = [(floor[0], minor) for minor in range(floor[1], got[-1][1] + 1)]
        missing = ["%d.%d" % v for v in want if v not in got]
        if missing:
            problems.append(
                "%s: declares >=%d.%d and runs %s, never %s"
                % (repo, floor[0], floor[1], ["%d.%d" % v for v in got], missing))

    assert not problems, (
        "a version these packages promise is not run by anything:"
        + "".join(chr(10) + "  " + p for p in problems))


def test_every_published_package_uploads_without_a_stored_credential():
    """Three packages were on PyPI when this was written; only one had a
    publish workflow.

    The other two were uploaded by hand from one machine with a long-lived PyPI
    token on it. That token is a standing credential whose blast radius is the
    whole project, and a hand-run twine has no gate in front of it - the ordering
    rules live in somebody's memory rather than in a `needs:`.

    All three moved to publish through the `pypi` GitHub Environment's OIDC
    trust relationship, minted per run. This asserts the two properties that
    make that true and that a well-meaning edit can undo: the upload job asks
    for `id-token: write`, and NOTHING in the file reaches for a stored
    password.

    A token creeping back is not a syntax error and would work, which is exactly
    why it needs a test rather than a review.

    `invisible_firefox` was one of the three and its publish.yml is what this
    was first measured against; it is not checked below any more, because its
    checkout went with the repo on 2026-08-18. The PyPI package it left behind
    (13 versions, unyanked) is a fact about the index, not about a workflow file
    this test could still read - there is no tree here to read it from. The
    other two are asserted below, over `_DEFAULT_SUITE_WORKFLOW`, which already
    lists only the repos that still exist.
    """
    import re

    forbidden = re.compile(
        r"(password\s*:|PYPI_API_TOKEN|PYPI_TOKEN|TWINE_PASSWORD|secrets\.PYPI)")
    problems = []
    for repo in _DEFAULT_SUITE_WORKFLOW:
        wf = _RELEASE / repo / ".github/workflows/publish.yml"
        if not (_RELEASE / repo).is_dir():
            pytest.skip("not the workbench - the sibling repos are not here")
        if not wf.is_file():
            problems.append(repo + ": no .github/workflows/publish.yml, so it is "
                                   "published by hand with a long-lived token")
            continue
        text = wf.read_text(encoding="utf-8")
        if "id-token: write" not in text:
            problems.append(repo + ": publish.yml never asks for id-token: write, "
                                   "so it cannot be using trusted publishing")
        if "environment: pypi" not in text:
            problems.append(repo + ": the upload job names no `environment: pypi`, "
                                   "which is where the trust relationship lives")
        hit = forbidden.search(text)
        if hit:
            problems.append(
                repo + ": publish.yml reaches for a stored credential ("
                + hit.group(0) + "), which is the thing OIDC replaced")

    assert not problems, (
        "the publish path can be authenticated by something other than a"
        " short-lived OIDC token:"
        + "".join(chr(10) + "  " + p for p in problems))


def test_no_workflow_reads_a_version_its_pyproject_does_not_have():
    """`dynamic = ["version"]` means there is no literal to read, and a workflow
    that reads one anyway raises KeyError on its first line.

    This is not hypothetical and it was not caught by anything. The core declares
    its version dynamic - derived from the packaged seal's tag plus
    CORE_REVISION, which is the whole point of the seal - and
    `user-install.yml` read `tomllib.load(...)["project"]["version"]`. It raised
    `KeyError: 'version'` on every release, on both runners, from the day it was
    written: the check that a user can `pip install` this from the index and
    launch it had never once executed a line past that point.

    It stayed invisible because the workflow only runs on a release event, so
    nobody looking at a push saw it, and because a red run that has always been
    red reads as a known condition rather than a fault. Found 2026-08-01 by
    opening one.

    The rule: derive the version the way the PACKAGE derives it. That is what
    publish.yml already did in the same repository, four files away.
    """
    import re

    reads_literal = re.compile(r'\[\s*"project"\s*\]\s*\[\s*"version"\s*\]')
    declares_dynamic = re.compile(r'(?m)^\s*dynamic\s*=\s*\[[^\]]*"version"')

    problems = []
    for repo in _DEFAULT_SUITE_WORKFLOW:
        root = _RELEASE / repo
        pyproject = root / "pyproject.toml"
        if not pyproject.is_file():
            pytest.skip("not the workbench - the sibling repos are not here")
        if not declares_dynamic.search(pyproject.read_text(encoding="utf-8")):
            continue          # a literal version is there to be read
        for wf in sorted((root / ".github" / "workflows").glob("*.yml")):
            if reads_literal.search(wf.read_text(encoding="utf-8")):
                problems.append(
                    repo + "/" + wf.name + ' reads ["project"]["version"] while '
                    "pyproject declares it dynamic: that is a KeyError on the "
                    "first line that touches it, every run")

    assert not problems, (
        "a workflow reads a version that does not exist in its pyproject:"
        + "".join(chr(10) + "  " + p for p in problems))


def test_the_workbench_docs_name_no_test_that_does_not_exist():
    """A doc that cites a test by name is claiming that test exists.

    This is the D1 defect `18-gate-inventory.md` records about itself, and on
    2026-08-02 it was found alive in three more places, the largest being the
    CENTRAL TABLE of the release runbook: `17-release-seal-spec.md` mapped
    fourteen audit cells to fourteen test names, and not one of the names
    existed. The file existed and every assertion described was real - the table
    had simply stopped matching its subject while reading as authoritative, in
    the document somebody follows to cut a release.

    A stale name costs more than a missing one. It sends the next reader to
    `grep`, the grep returns nothing, and the honest conclusions available are
    "the doc is wrong" or "the coverage was deleted" - and telling those two
    apart is the afternoon this gate exists to save.

    THE CONVENTION: a name in BACKTICKS is a claim that it exists. A historical
    mention - "it was called X until it was renamed" - goes without them. That
    keeps the rule mechanical instead of asking a scanner to understand tense.

    Skipped outside the workbench: these docs are not part of any package.
    """
    import re

    workbench = _RELEASE.parent
    docs = workbench / "docs" / "firefox-stealth-architecture"
    if not docs.is_dir():
        pytest.skip("not the workbench - the architecture docs are not here")

    defined = set()
    alberi = []
    for repo in _DEFAULT_SUITE_WORKFLOW:
        tests = _RELEASE / repo / "tests"
        if not tests.is_dir():
            pytest.skip("not the workbench - the sibling repos are not here")
        alberi.append(tests)
    alberi.append(_RELEASE.parent / "tests")
    # `20-our-patches.md` cites the SOURCE repo's own validators by name, and
    # they live outside every tree above. Scanning only `release/*/tests` made
    # this gate RED on a citation that was perfectly true - measured 2026-08-12
    # on `test_manifest_family_set_equals_the_68`, which does exist, in
    # `scripts/` of the Firefox source. A gate that is red for a false positive
    # is worse than a stale doc: it teaches the next reader to ignore it.
    sorgente = [p for p in _FIREFOX_SOURCE_CANDIDATES if (p / "scripts").is_dir()]
    if not sorgente:
        pytest.skip("the Firefox source tree is not on this machine, so the "
                    "names the docs cite from it cannot be judged")
    alberi.append(sorgente[0] / "scripts")

    for albero in alberi:
        if not albero.is_dir():
            continue
        for path in albero.rglob("test_*.py"):
            defined.update(re.findall(r"(?m)^\s*(?:async )?def (test_[a-z0-9_]+)",
                                      path.read_text(encoding="utf-8", errors="replace")))
            defined.add(path.stem)          # docs cite files by name too

    phantom = {}
    for doc in sorted(docs.glob("*.md")):
        cited = set(re.findall(r"`(test_[a-z0-9_]+)`",
                               doc.read_text(encoding="utf-8")))
        gone = sorted(cited - defined)
        if gone:
            phantom[doc.name] = gone

    assert not phantom, (
        "these docs name tests that do not exist, which sends the next reader to "
        "a grep that returns nothing:\n  "
        + "\n  ".join(f"{f}: {names}" for f, names in phantom.items())
        + "\nEither the name changed and the doc did not follow, or the coverage "
          "went. If the mention is HISTORICAL, write it without backticks.")


def test_every_open_bug_says_how_to_re_check_it():
    """An OPEN heading is a claim about TODAY, and nothing was re-reading it.

    Measured 2026-08-12, and the measurement is what this gate exists to stop
    from repeating. Work was picked off this file by reading HEADINGS: one of
    them said OPEN over a body that already carried the A/B closing it (6
    sessions dead in 10 with the defect, 0 in 10 with the fix, Fisher p about
    0.005), and the owner had to point it out. Two more headings said OPEN over
    a body that was half closed - "Chiuso subito: `_summarize` now refuses with
    exit 3", "RESOLVED the slowness half, and it was not ours".

    The audit that followed opened all nineteen and found exactly ONE stale in
    substance. So the entries were maintained; what was missing is that their
    state was not in a field anyone could READ without opening 83k characters.

    THE CONVENTION: every OPEN entry carries a `> **Ricontrollo:**` line saying
    how to decide today whether it is still true - a command where one exists,
    and an honest "not verified since it was found" where re-checking needs a
    build, a retail judge or a proxy that is up. The date is not enforced: a
    gate that demands a fresh measurement on nineteen entries needing browsers
    would be permanently red, and a permanently red gate teaches people to
    ignore it - which is the defect this same file was already carrying in
    another test.

    TRIAGE and NOTE headings are exempt: they are not claims that something is
    still broken.
    """
    import re

    workbench = _RELEASE.parent
    doc = workbench / "docs" / "firefox-stealth-architecture" / "70-known-bugs.md"
    if not doc.is_file():
        pytest.skip("not the workbench - the architecture docs are not here")

    testo = doc.read_text(encoding="utf-8")
    voci = re.split(r"(?m)^## ", testo)
    muti = []
    for voce in voci:
        if not voce.startswith("OPEN"):
            continue
        titolo = voce.split(chr(10))[0].strip()
        if not re.search(r"(?m)^> \*\*Ricontrollo:\*\* \S", voce):
            muti.append(titolo[:96])

    assert not muti, (
        "these OPEN entries do not say how to decide today whether they are "
        "still true, so the only way to read this file is to open every entry "
        "- which is how a closed bug got proposed as work on 2026-08-12:"
        + "".join(chr(10) + "  " + t for t in muti)
        + chr(10) + "Add a `> **Ricontrollo:**` line: a command where one "
        "exists, or an honest statement of what re-checking would need.")


def test_a_doc_that_names_a_test_function_names_the_file_holding_it():
    """Naming the function without the file is the half that costs the routing.

    The gate above checks that a cited test EXISTS, which is the cheap half. It
    says nothing about whether the reader can FIND it, and that is the half that
    was measured to fail. On the routing bench, 2026-08-12, item 6: the docs
    name `test_no_pref_is_emitted_as_a_python_float` in two places and never
    name `tests/test_prefs.py`, so three runs out of three had to guess the file
    from the function's description ("asserts the property over the whole
    emitted surface") and three out of three guessed `test_prefs_surface.py` -
    which exists, sits beside it, and does not hold the gate. Two of the three
    caught themselves by grepping for the function name; the third did not, and
    the item lost the casella in four consecutive rounds.

    THE PERIMETER IS THE SECTION, not a line window. Measured while writing
    this: a +-6 line window reports 71 orphans and a section-scoped one reports
    45, because a list of eight names under one heading names its file once at
    the top and a window flags the last six. A reader inside the section can see
    the file; that is the standard the check has to hold, or it fails honest
    prose.

    A name that IS a file stem is exempt: it already names the file. A function
    defined in more than one file passes if any of them is named.
    """
    import re

    workbench = _RELEASE.parent
    docs = workbench / "docs" / "firefox-stealth-architecture"
    if not docs.is_dir():
        pytest.skip("not the workbench - the architecture docs are not here")

    alberi = [_RELEASE / repo / "tests" for repo in _DEFAULT_SUITE_WORKFLOW]
    alberi.append(workbench / "tests")
    definita = {}
    stem = set()
    for albero in alberi:
        if not albero.is_dir():
            continue
        for path in albero.rglob("test_*.py"):
            stem.add(path.stem)
            for nome in re.findall(
                    r"(?m)^\s*(?:async )?def (test_[a-z0-9_]+)",
                    path.read_text(encoding="utf-8", errors="replace")):
                definita.setdefault(nome, set()).add(path.name)
    if not definita:
        pytest.skip("not the workbench - the sibling repos are not here")

    orfane = {}
    for doc in sorted(docs.glob("*.md")):
        righe = doc.read_text(encoding="utf-8").split(chr(10))
        tagli = [i for i, r in enumerate(righe) if re.match(r"^#{1,6} ", r)]
        tagli.append(len(righe))
        for i, riga in enumerate(righe):
            for m in re.finditer(r"`(test_[a-z0-9_]+)`", riga):
                nome = m.group(1)
                if nome in stem or nome not in definita:
                    continue          # e' un file, o non esiste: lo dice l'altro gate
                a = max([t for t in tagli if t <= i], default=0)
                b = min([t for t in tagli if t > i], default=len(righe))
                sezione = chr(10).join(righe[a:b])
                if not any(f in sezione for f in definita[nome]):
                    orfane.setdefault(doc.name, []).append(
                        nome + " -> " + "/".join(sorted(definita[nome])))

    assert not orfane, (
        "these docs name a test function without naming, anywhere in the same "
        "section, the file that holds it - so the only way to reach it is a "
        "grep, and a plausible sibling file is what gets guessed instead:"
        + "".join(chr(10) + "  " + f + ": " + ", ".join(sorted(set(v)))
                  for f, v in sorted(orfane.items())))
