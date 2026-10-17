"""An install instruction that is not uv is refused. This is the check.

    python -m invisible_core.way_in              # 1 if a refused instruction is found
    python -m invisible_core.way_in --selftest   # the known-bad corpus

THE WAY IN IS uv. The owner chose it on 2026-09-06, tried pip the next morning
and withdrew it the same day, on a measurement: `pip install` outside a venv is
refused by PEP 668 on Ubuntu 24.04, and a README with no explanations cannot
carry the explanation that would fix that. On 2026-10-17 the owner made it
"uv everywhere", and on that day the three clients' READMEs still said
`pip install`, so did their CONTRIBUTING files and 37 pages of the wrapper's
docs, and so did every remedy the code printed. Nothing failed, because nothing
compared any of them with the decision. This does.

TWO PERIMETERS, because an instruction reaches a person two ways:

  * the DOCUMENTS they copy from: every tracked Markdown file, and the issue
    templates, which suggest an answer. Except `CHANGELOG.md`: it is history,
    and a release that shipped with a pip line did ship with it.
  * the MESSAGES the code prints: every string literal in the tracked
    `src/**/*.py` that is not a docstring. A remedy in an error is an
    instruction too, and the one read at the worst moment: on 2026-10-17 the
    core's own pin repair told a uv user to run a pip their venv did not have
    (`installer.py` has the measurement). Comments and docstrings describe pip
    as a fact of the world and are not shown to anybody, so they are not read.
    The commands the code RUNS or prints are built by `installer.install`, as
    separate tokens, which is why a literal is never needed.

WHAT IS REFUSED: a command that installs through pip or pipx - `pip install`,
`pip3 install`, `pip3.12 install`, `pip.exe install`, a pip reached by path
(`.venv/bin/pip install`), `python -m pip install`, `py -m pip install`,
`pipx install`, `pipx run` - and through the other installers a page could
teach instead (`pipenv install`, `poetry add`, `pdm add`, `conda install`,
`easy_install`). "Everywhere" is not a list of the routes somebody happened to
remember. `uv pip install` is uv, and passes.

KNOWN LIMITS, said out loud because a gate whose limits are unstated gets
trusted too far. A message assembled from separate tokens (`"pip" + " install"`)
is not seen; that is the form `installer.install` uses on purpose, so the limit
is the price of having a way to write the command at all. A negation still
fires: "do not run pip install" is refused, and the remedy is to say what to run
instead. And the corpus of refused text has to contain refused text, so this
module's own file is the one source the second perimeter skips.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import subprocess
import sys

#: A command that installs without uv. The negative lookbehind keeps `pip`
#: from matching inside a longer word (`rapid install`, `pip-tools`); a path
#: in front of it (`bin/pip`) is allowed through, because that is still pip.
_REFUSED = re.compile(
    r"(?<![\w.-])(?:"
    r"(?:python[\d.]*|py)(?:\.exe)?\s+-m\s+pip\s+install"
    r"|pip(?:\d+(?:\.\d+)?)?(?:\.exe)?\s+install"
    r"|pipx(?:\.exe)?\s+(?:install|run)"
    r"|pipenv\s+install|poetry\s+add|pdm\s+add"
    r"|(?:conda|mamba|micromamba)\s+install|easy_install"
    r")\b",
    re.IGNORECASE)

#: `uv pip install` is uv. Checked on the text before a match, not with a
#: lookbehind, because the space between the two words is not one fixed width.
_UV_BEFORE = re.compile(r"(?<![\w.-])uv(?:\.exe)?\s+$", re.IGNORECASE)

#: History, not an instruction: what a release shipped stays written as it was.
HISTORY = ("CHANGELOG.md",)


def refused_in(text: str) -> list:
    """The refused commands in `text`, as the matched strings."""
    out = []
    for m in _REFUSED.finditer(text):
        before = text[:m.start()]
        if _UV_BEFORE.search(before):
            continue
        out.append(m.group(0))
    return out


def scan_document(text: str) -> list:
    """(line number, line) for every line of a document that teaches a way in
    other than uv."""
    return [(n, line.strip()) for n, line in enumerate(text.splitlines(), 1)
            if refused_in(line)]


def _docstrings(tree: ast.AST) -> set:
    """The ids of the Constant nodes that are docstrings."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            first = body[0] if body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                out.add(id(first.value))
    return out


def scan_source(text: str) -> list:
    """(line number, literal) for every string literal of a Python source that
    is not a docstring and teaches a way in other than uv. An f-string is read
    through its literal parts, which is where the words of a message are."""
    tree = ast.parse(text)
    skip = _docstrings(tree)
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip and refused_in(node.value)):
            out.append((node.lineno, node.value.strip()[:120]))
    return sorted(out)


def _tracked(root: pathlib.Path) -> list:
    r = subprocess.run(["git", "ls-files", "-z"], cwd=str(root),
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("git ls-files failed in %s: %s" % (root, r.stderr.strip()))
    # chr(0), not a literal: `git ls-files -z` ends each path with NUL, and a
    # control character in a literal is what this suite refuses on sight.
    return [p for p in r.stdout.split(chr(0)) if p]


def perimeter(root: pathlib.Path) -> tuple:
    """(documents, sources) under `root`, as paths relative to it."""
    tracked = _tracked(root)
    documents = [p for p in tracked
                 if (p.endswith(".md") and pathlib.PurePosixPath(p).name not in HISTORY)
                 or p.startswith(".github/ISSUE_TEMPLATE/")]
    here = pathlib.Path(__file__).resolve()
    sources = [p for p in tracked
               if p.startswith("src/") and p.endswith(".py")
               and (root / p).resolve() != here]
    return documents, sources


def judge(root: pathlib.Path) -> tuple:
    """(findings, documents read, sources read). Each finding is
    (path, line number, text)."""
    documents, sources = perimeter(root)
    findings = []
    for rel in documents:
        text = (root / rel).read_bytes().decode("utf-8", "replace")
        findings += [(rel, n, line) for n, line in scan_document(text)]
    for rel in sources:
        text = (root / rel).read_bytes().decode("utf-8", "replace")
        findings += [(rel, n, lit) for n, lit in scan_source(text)]
    return findings, len(documents), len(sources)


#: (name, document text) that MUST be refused. Each is a CLASS of variation.
KNOWN_BAD_DOCUMENTS = (
    ("the README line every client carried until 0.37.0",
     "pip install invisible-playwright"),
    ("a versioned pip", "pip3 install invisible-selenium"),
    ("a pip with its minor version", "pip3.12 install invisible-puppeteer"),
    ("pip through the interpreter", "python -m pip install -e \".[dev]\""),
    ("a versioned interpreter", "python3.12 -m pip install invisible-core"),
    ("the Windows launcher", "py -m pip install invisible-playwright"),
    ("pip reached by a path", "RUN /opt/venv/bin/pip install invisible-playwright"),
    ("pip.exe", "pip.exe install invisible-playwright"),
    ("pipx install", "pipx install invisible-playwright"),
    ("pipx run", "pipx run invisible-playwright fetch"),
    ("upper case", "PIP INSTALL INVISIBLE-PLAYWRIGHT"),
    ("pipenv", "pipenv install invisible-playwright"),
    ("poetry", "poetry add invisible-playwright"),
    ("pdm", "pdm add invisible-playwright"),
    ("conda", "conda install -c conda-forge invisible-playwright"),
    ("easy_install", "easy_install invisible-playwright"),
    ("inline, in prose", "To use the package you need `pip install invisible-playwright`."),
    ("an upgrade", "Upgrade with `pip install -U invisible-selenium`."),
)

#: (name, document text) that must NOT be refused.
MUST_NOT_FIRE_DOCUMENTS = (
    ("uv's own pip interface", "uv pip install -e \".[dev]\""),
    ("uv add", "uv add invisible-playwright"),
    ("uv run", "uv run invisible-playwright fetch"),
    ("pip as the tail of a longer word", "a mypip install script"),
    ("pip at the head of another tool's name", "pip-tools compile, pip-audit"),
    ("pip as a noun", "pip resolves the core from the index"),
    ("another language's installer", "npm install playwright"),
)

#: (name, Python source) that MUST be refused, through the source scan.
KNOWN_BAD_SOURCES = (
    ("a remedy in an exception",
     'raise ImportError("Run: pip install --force-reinstall invisible-playwright")\n'),
    ("a remedy in an f-string",
     'def f(x):\n    return f"Upgrade with `pip install -U {x}`"\n'),
)

#: (name, Python source) that must NOT be refused.
MUST_NOT_FIRE_SOURCES = (
    ("a docstring describing pip",
     'def f():\n    """`pip install --no-deps` leaves the core behind."""\n'),
    ("a comment", '# pip install x\nx = 1\n'),
    ("the command as tokens, the way installer.install builds it",
     'cmd = ["pip", "install", "invisible-core"]\n'),
)


def git_root(start: "pathlib.Path | None" = None) -> pathlib.Path:
    """The repository you are standing in: the subject is where the caller is,
    not where this module happens to be installed."""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       cwd=str(start) if start else None,
                       capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        raise SystemExit("not inside a git repository, so there is no tree to "
                         "judge. Pass --root to name one.")
    return pathlib.Path(r.stdout.strip()).resolve()


def selftest() -> int:
    bad = 0
    print("--- documents that MUST be refused ---")
    for name, text in KNOWN_BAD_DOCUMENTS:
        hit = bool(scan_document(text))
        print("  %s: %s" % ("killed" if hit else "SURVIVED", name))
        bad += not hit
    print("--- documents that must NOT be refused ---")
    for name, text in MUST_NOT_FIRE_DOCUMENTS:
        hit = bool(scan_document(text))
        print("  %s: %s" % ("FALSE POSITIVE" if hit else "silent", name))
        bad += hit
    print("--- sources that MUST be refused ---")
    for name, text in KNOWN_BAD_SOURCES:
        hit = bool(scan_source(text))
        print("  %s: %s" % ("killed" if hit else "SURVIVED", name))
        bad += not hit
    print("--- sources that must NOT be refused ---")
    for name, text in MUST_NOT_FIRE_SOURCES:
        hit = bool(scan_source(text))
        print("  %s: %s" % ("FALSE POSITIVE" if hit else "silent", name))
        bad += hit

    # SILENT on this repository as it stands: a gate born red on what already
    # exists teaches people to bypass it. Both perimeters must also have READ
    # something, or a clean result says nothing.
    print("--- the real repository ---")
    try:
        root = git_root()
        findings, documents, sources = judge(root)
        if findings or not documents:
            print("  %d finding(s), %d document(s) read" % (len(findings), documents))
            bad += 1
        else:
            print("  silent (%d documents, %d sources)" % (documents, sources))
    except SystemExit as failure:
        print("  not verifiable (%s)" % failure)
    print()
    print("selftest: %s" % ("ALL GOOD" if not bad else "%d PROBLEMS" % bad))
    return 1 if bad else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", help="the repository to judge "
                                  "(default: the one you are standing in)")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        return selftest()

    root = pathlib.Path(a.root).resolve() if a.root else git_root()
    findings, documents, sources = judge(root)
    if not documents:
        print("REFUSED: no document to read in %s, so a clean result would say "
              "nothing." % root)
        return 1
    if findings:
        print("REFUSED: %d install instruction(s) that are not uv:" % len(findings))
        for rel, n, text in findings:
            print("   %s:%d: %s" % (rel, n, text))
        print()
        print("The way in is uv. A document says `uv add <package>` (in a project "
              "`uv init` made) or `uv pip install`; a message builds its command "
              "with invisible_core.installer.install, which spells it for the tool "
              "that made the environment.")
        return 1
    # A green says what it checked.
    print("way_in: clean (%s: %d document(s), %d source file(s) read)"
          % (root, documents, sources))
    return 0


if __name__ == "__main__":
    sys.exit(main())
