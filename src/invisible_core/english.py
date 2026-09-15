"""The public repositories are English-only. This is the check that says so.

Why it exists, and it is not a style preference. On 2026-08-27 a whole new
subsystem - the `_juggler` client, its generators and its test files, about four
thousand lines - was written in ITALIAN and committed to a public repository.
Nothing caught it: the repo had been English since its first commit, `pytest`
was green throughout, and the pre-push hook had no opinion about the language a
file is written in. A convention that lives only in the heads of the people
writing the code is not a convention, it is a habit, and a habit skips a session.

    python -m invisible_core.english                 # 1 if Italian is found
    python -m invisible_core.english --range A..B    # only what a push adds
    python -m invisible_core.english --selftest      # the known-bad corpus

⛔ IT LIVES HERE BECAUSE IT WAS COPIED, AND THE COPIES DRIFTED. The check was a
script duplicated into two consumer repositories and never into this one - which
is how `invisible_core` itself, the package both consumers pin, went a year
without ever being looked at, carrying Italian prose in 29 files and Italian in
messages a user reads. And the drift was not hypothetical: measured 2026-09-15,
FOUR of the five exclusion entries in AIHawk's copy named paths that exist only
in the wrapper (`src/invisible_playwright/_pw/`, `_driver/`,
`_juggler/injected.js`, `tests/test_fork.py`), because the copy carried the other
repository's data with it. One of them, `_driver/`, is dead in both since Node
was removed.

So the split is: the LOGIC is here, once. What is per-repository - which folders
are vendored, which marker our patches carry - is declared by each repository in
its own `pyproject.toml`, under `[tool.invisible.english]`, next to
`[tool.invisible.hooks]`. And :func:`dead_exclusions` refuses an entry that names
nothing, which is the mechanism that would have caught the drift above instead of
a person noticing.

⛔ AND THE ROOT IS AN ARGUMENT, WHICH IS WHY THERE IS NO REFUSAL HERE. The script
version derived its root from `__file__`, so it could only ever judge its own
repository while looking like it judged whatever you pointed it at: run from
`invisible_core`, the wrapper's copy printed `english only: clean` about the
wrapper, and that sentence was believed for two steps while `invisible_core`
carried 32 lines of Italian docstring. The copy grew a guard that REFUSED when
the two disagreed. That guard is not ported: a module that takes the tree as a
parameter, defaulting to the git toplevel you are standing in, has nothing to
disagree with. The patch is gone because the shape that needed it is gone.

WHAT IT DOES NOT DO, said out loud because a gate whose limits are unstated gets
trusted too far. It does not detect Spanish, French or Portuguese. It does not
judge prose quality. It cannot see a single Italian noun with no Italian function
word near it: `def calcola(x)` on its own passes, and so does an error message of
four words - measured, `_headless.py` refused a Mac in Italian for weeks under
this threshold. What it catches is Italian PROSE, which is what comments and
docstrings are made of, and prose is what actually happened.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

#: Italian function words that are not English words, not Python keywords and
#: not plausible identifiers. ⛔ The list is deliberately CONSERVATIVE: every
#: entry was checked against the existing English files, which must stay silent.
#: Words left OUT on purpose, with the reason, because the temptation to add them
#: will come back:
#:
#:   `del`   - a Python keyword.
#:   `in`, `a`, `di`, `da`, `no`, `se`, `si`, `e`, `o` - too short, and several
#:             are English words or ordinary variable names.
#:   `come`  - too easy to reach as a fragment of English prose.
#:   `solo`  - an English word.
#:   `la`, `le`, `il`, `lo`, `un` - one- and two-letter variable names.
#:
#: What is left is prose glue. If two of these appear in one file, that file is
#: not written in English.
ITALIAN = (
    "perche", "quindi", "questo", "questa", "queste", "questi", "quello",
    "quella", "quelle", "quelli", "della", "delle", "degli", "dello",
    "nella", "nelle", "negli", "dalla", "dalle", "dagli", "sulla", "sulle",
    "alla", "alle", "agli", "sono", "essere", "viene", "vengono", "invece",
    "anche", "senza", "ogni", "tutto", "tutti", "tutta", "tutte", "cosa",
    "dove", "quando", "sempre", "cioe", "piu", "gia", "adesso", "allora",
    "prima", "dopo", "sotto", "sopra", "dentro", "fuori", "verso",
    "niente", "nessuno", "nessuna", "qualcosa", "qualcuno", "molto",
    "poco", "troppo", "abbastanza", "soltanto", "oppure", "mentre",
    "finche", "affinche", "benche", "sebbene", "poiche", "siccome",
    "quale", "quali", "chiunque", "ovunque", "comunque", "dunque",
    "infatti", "inoltre", "tuttavia", "eppure", "ancora", "appena",
    "subito", "spesso", "raramente", "davvero", "proprio", "stesso",
    "stessa", "stesse", "stessi", "altro", "altra", "altre", "altri",
)

#: ⛔ TWO distinct words, not one occurrence. One is how a false positive is
#: born: a proper noun, a quoted string, a URL, a vendor name.
THRESHOLD = 2

#: ⛔ `.js`, `.css` AND `.html` ARE IN HERE BECAUSE THE FRONT END IS WHERE THE
#: PROSE IS. Added 2026-09-11, after five files of AIHawk's served page were
#: found carrying Italian comments - four of them already pushed - while the
#: check reported clean on every run. It was not broken and had no false
#: negatives: it was looking at a different set of files. Before trusting any
#: gate, print what it covers AGAINST what exists.
EXTENSIONS = (".py", ".md", ".toml", ".cfg", ".yml", ".yaml",
              ".js", ".css", ".html")

#: ⛔ A PATCH BLOCK IS THE CONTIGUOUS RUN OF COMMENT LINES AROUND THE MARKER,
#: never a fixed number of lines. A fixed span was tried first and is wrong in
#: both directions: too small it truncates a 41-line block, too large it reaches
#: past a one-line comment into somebody else's code and reports their words as
#: ours. Measured with span 44: two files were flagged for Italian that sat in
#: upstream code below the patch.
COMMENT_STARTS = ("//", "#")
MAX_BLOCK = 80

#: ⛔ A LINE IS NOT A UNIT IN A BUNDLED FILE, and reading it as one made this
#: check useless the first time it saw a minified bundle: that file has lines of
#: 320.820 characters, because the injected sources are single-quoted strings
#: with their newlines written as two characters. Taking "16 lines after the
#: marker" there swallowed the entire injected script, so it reported Italian
#: belonging to upstream code hundreds of kilobytes from any patch of ours.
#: Above this width a line is scanned by CHARACTER window instead.
LONG_LINE = 2000

#: How much of a long line belongs to one comment, and where it stops. The
#: literal two-character `\n` is the real line break inside those strings.
WINDOW = 700
BEFORE = 200
NL_LITERAL = chr(92) + "n"


class Config:
    """What one repository declares about itself.

    ⛔ THIS IS THE HALF THAT MUST NOT BE SHARED, and copying it is what broke the
    old arrangement. `excluded` and `marker` describe a tree; the word list and
    the thresholds describe the Italian language. Putting both in one file and
    duplicating the file gave every repository the other one's vendored folders.
    """

    def __init__(self, excluded=(), marker=None, extensions=EXTENSIONS):
        self.excluded = tuple(excluded)
        self.marker = marker
        self.extensions = tuple(extensions)

    def __repr__(self) -> str:
        return ("Config(excluded=%r, marker=%r)" % (self.excluded, self.marker))


def config_for(root: pathlib.Path) -> Config:
    """Read `[tool.invisible.english]` out of a repository's `pyproject.toml`.

    A repository that declares nothing gets the defaults, which exclude nothing:
    an absent declaration must mean "scan everything", never "scan nothing".
    """
    import tomllib

    path = pathlib.Path(root) / "pyproject.toml"
    if not path.is_file():
        return Config()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    section = (data.get("tool", {}).get("invisible", {}).get("english", {}))
    return Config(
        excluded=section.get("excluded", ()),
        marker=section.get("marker"),
        extensions=section.get("extensions", EXTENSIONS),
    )


def italian_words(text: str) -> set:
    """The Italian function words present, as a set."""
    lowered = text.lower()
    return {w for w in ITALIAN
            if re.search(r"(?<![a-z0-9_])%s(?![a-z0-9_])" % w, lowered)}


def our_blocks(text: str, marker: str) -> str:
    """Only the parts of a vendored file that OUR patches wrote.

    ⛔ VENDORED IS NOT A BLANKET EXEMPTION, and that was the hole the first
    version had. Upstream folders are code we do not own, so scanning them whole
    would be noise - but they contain OUR patches, and on 2026-08-27 seven of
    those blocks were in Italian and completely invisible, the biggest of them in
    a file every user imports. The exclusion is by FOLDER; what is ours is
    identified by the marker, so inside an excluded path only the marked blocks
    are read.
    """
    lines = text.splitlines()
    kept: list = []
    for i, line in enumerate(lines):
        if marker not in line:
            continue
        if len(line) <= LONG_LINE:
            start = i
            while (start > 0
                   and lines[start - 1].strip().startswith(COMMENT_STARTS)
                   and i - start < MAX_BLOCK):
                start -= 1
            end = i
            while (end + 1 < len(lines)
                   and lines[end + 1].strip().startswith(COMMENT_STARTS)
                   and end - i < MAX_BLOCK):
                end += 1
            kept.extend(lines[start:end + 1])
            continue
        pos = 0
        while True:
            j = line.find(marker, pos)
            if j < 0:
                break
            end = line.find(NL_LITERAL, j)
            if not 0 < end - j < WINDOW:
                end = j + WINDOW
            kept.append(line[max(0, j - BEFORE):end])
            pos = j + 1
    return "\n".join(kept)


#: This module's own path inside a repository that ships it.
#: ⛔ THE SELF-EXEMPTION IS COMPUTED, NOT DECLARED. This file carries the
#: known-bad corpus below - it is Italian on purpose, because a gate that has
#: only ever printed PASS is not a gate - so it has to exempt itself. Writing
#: that exemption into each repository's config would be one more per-repo copy
#: of a fact this file already knows, and the first one to go stale.
_SELF = "src/invisible_core/english.py"


def inspect(path: str, text: str, config: Config) -> tuple:
    """(is_italian, words) for one file.

    A pure function on (path, text, config): the selftest mutates here and never
    touches the disk, which is what makes the mutations cost nothing to run.
    """
    normalised = path.replace(chr(92), "/")
    if normalised == _SELF:
        return (False, set())
    if normalised in config.excluded:
        return (False, set())
    if any(e.endswith("/") and normalised.startswith(e) for e in config.excluded):
        # ⛔ Excluded as a FOLDER, but our own patches inside it are still ours.
        if not config.marker:
            return (False, set())
        found = italian_words(our_blocks(text, config.marker))
        return (len(found) >= THRESHOLD, found)
    if not normalised.endswith(config.extensions):
        return (False, set())
    found = italian_words(text)
    return (len(found) >= THRESHOLD, found)


def tracked(root: pathlib.Path, rev_range=None) -> list:
    """The files to look at: everything tracked, or only what a range touches.

    ⛔ IT COMES FROM GIT, so a file you have just WRITTEN is not in it until it
    is staged. Measured 2026-09-08: this was run on a brand new script, printed
    clean, and the clean was about a set that did not contain the file. If a gate
    says clean about work you know you just wrote, the first hypothesis is not
    "I wrote it well", it is "it did not look".
    """
    if rev_range:
        r = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=d", rev_range],
            cwd=root, capture_output=True, text=True)
    else:
        r = subprocess.run(["git", "ls-files"], cwd=root,
                           capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("git refused: %s" % r.stderr.strip()[:200])
    return [x for x in r.stdout.splitlines() if x.strip()]


def scan(root: pathlib.Path, paths, config: Config) -> list:
    guilty = []
    for path in paths:
        f = pathlib.Path(root) / path
        if not f.exists():
            continue
        try:
            text = f.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        is_italian, found = inspect(path, text, config)
        if is_italian:
            guilty.append((path, sorted(found)))
    return guilty


def dead_exclusions(root: pathlib.Path, config: Config) -> list:
    """Declared exclusions that name nothing in this repository.

    ⛔ THIS IS THE CHECK THE COPIES DID NOT HAVE, and it is why they drifted in
    silence. An exclusion is a hole deliberately cut in a gate; one that names
    nothing is either a hole in the wrong wall or a piece of another repository's
    configuration that came along with a copy. Measured 2026-09-15 on AIHawk's
    copy: four of five entries named paths that exist only in the wrapper.

    A dead entry never makes a gate red, which is exactly why nobody finds it.
    """
    root = pathlib.Path(root)
    return [e for e in config.excluded if not (root / e.rstrip("/")).exists()]


def coverage(root: pathlib.Path, config: Config) -> tuple:
    """(covered, uncovered) tracked files, so a green can say what it looked at.

    ⛔ A GATE HAS A PERIMETER AND THE PERIMETER IS MEASURED. This exists because
    the extension list once missed `.js`, `.css` and `.html` while the served
    page was 67 KB of script: the check was silent and correct about a set that
    did not contain the prose.
    """
    covered, uncovered = [], []
    for rel in tracked(root):
        (covered if rel.endswith(config.extensions) else uncovered).append(rel)
    return covered, uncovered


# ── the known-bad corpus ────────────────────────────────────────────────────
#
# ⛔ IT LIVES IN THE MODULE AND NOT IN THE TEST, deliberately. The corpus is
# Italian on purpose, so whatever file holds it has to be exempt from the gate -
# and an exemption is a hole. Keeping it here means there is exactly ONE exempt
# path (this file, computed above) instead of two, and the test that uses it
# stays ordinary English with nothing special about it.

_ITA = ("# Questo commento e' scritto in italiano perche' nessuno "
        "controllava.\ndef f():\n    return 1\n")
_ENG = ("# This comment is in English, which is what the repo uses.\n"
        "def f():\n    return 1\n")

#: (name, path, text, must_fire). The path decides which branch of `inspect`
#: runs, so these double as the coverage of the exclusion logic.
KNOWN_BAD = (
    ("an Italian comment in a .py", "src/x.py", _ITA, True),
    ("an Italian document", "README.md", _ITA, True),
    ("Italian inside a docstring", "src/x.py",
     'def f():\n    """Questo fa qualcosa, quindi serve."""\n', True),
    ("Italian in a config file", "pyproject.toml",
     "# Questo pacchetto, quindi, dipende da\nname = 'x'\n", True),
    # ⛔ The real case: names translated, comments not. That is what a
    # half-finished translation looks like, and it is the likeliest way to get
    # this wrong a second time.
    ("English names, Italian comments", "src/x.py",
     "def click(selector):\n"
     "    # Prima si risolve, poi si guarda dove sta, quindi si clicca.\n"
     "    return selector\n", True),
    ("an Italian comment in a .js", "src/ui/js/x.js",
     "/* Prima si guarda dove sta, quindi si disegna. */" + chr(10)
     + "function draw(){ return 1; }" + chr(10), True),
    ("an Italian comment in a .css", "src/ui/css/x.css",
     "/* Questo pannello sta sopra, quindi non sposta niente. */" + chr(10)
     + "#rail{ position:absolute }" + chr(10), True),
    ("an Italian test", "tests/test_x.py",
     "def test_questo_funziona():\n"
     "    # Questo controlla che tutto sia a posto, quindi basta.\n"
     "    assert True\n", True),
    ("Italian in a sibling script", "scripts/gen_x.py", _ITA, True),
    ("Italian in a workflow", ".github/workflows/ci.yml",
     "# Questo lavoro gira sempre, quindi non si salta.\nname: ci\n", True),
    ("OUR patch inside a vendored folder", "vendored/_pw/_impl/_playwright.py",
     "\n".join([
         "def upstream(self):",
         "    # MODIFIED by us: chromium e webkit non esistono piu' in",
         "    # questo fork, quindi il messaggio lo dice invece di lasciare",
         "    # un AttributeError.",
         "    raise ValueError('x')",
     ]) + "\n", True),
)

#: The other half, and it is worth as much: what must stay SILENT.
MUST_NOT_FIRE = (
    ("ordinary English", "src/x.py", _ENG),
    # ⛔ One word is not prose, and this is the line that keeps false positives
    # out: a proper noun, a quoted string, a URL.
    ("a single Italian word (a name, a quotation)", "src/x.py",
     "# The vendor is called Sempre, which is a name and not a sentence.\n"),
    ("vendored upstream is not ours", "vendored/_pw/_impl/_page.py", _ITA),
    # ⛔ A minified line is not a unit: 3.000 characters of upstream code around
    # one patch of ours must not be read as part of it.
    ("a minified line around our patch", "vendored/_pw/bundle.js",
     "var a=1;" + ("x" * 3000)
     + " // questo e' upstream, quindi non si guarda affatto " + ("y" * 3000)
     + " // MODIFIED by us: nothing to see here " + ("z" * 3000)
     + " // e anche questo e' upstream, dunque niente" + "\n"),
    ("upstream code around our patch stays invisible",
     "vendored/_pw/_impl/_page.py",
     "\n".join(["# questo e' upstream, quindi non lo guardiamo affatto",
                "def upstream_thing():",
                "    return 1"]) + "\n"),
    ("a binary has no language", "assets/logo.png", _ITA),
    # ⛔ The self-exemption must be ONE PATH, never a prefix: a prefix would
    # quietly stop covering every other module in the package.
    ("this module, which carries the corpus", _SELF, _ITA),
)

#: The config the corpus above is written against.
CORPUS_CONFIG = Config(excluded=("vendored/_pw/",), marker="MODIFIED by us")


def selftest() -> int:
    bad = 0

    def expect(name, path, text, should_fire, config):
        nonlocal bad
        is_italian, found = inspect(path, text, config)
        if is_italian != should_fire:
            print("  %s: %s (words seen: %s)"
                  % ("SURVIVED" if should_fire else "FALSE POSITIVE",
                     name, sorted(found) or "none"))
            bad += 1
        else:
            print("  %s: %s" % ("killed" if should_fire else "silent", name))

    print("--- mutations that MUST fire ---")
    for name, path, text, _ in KNOWN_BAD:
        expect(name, path, text, True, CORPUS_CONFIG)

    print("--- cases that must NOT fire ---")
    for name, path, text in MUST_NOT_FIRE:
        expect(name, path, text, False, CORPUS_CONFIG)

    # ⛔ And the check worth more than all the others: SILENT on this repository
    # as it stands. A gate born red on what already exists teaches people to
    # bypass it, and this project has written that down twice.
    print("--- the real repository ---")
    try:
        root = git_root()
        config = config_for(root)
        dead = dead_exclusions(root, config)
        if dead:
            print("  %d declared exclusion(s) name nothing here: %s"
                  % (len(dead), ", ".join(dead)))
            bad += 1
        noisy = scan(root, tracked(root), config)
        if noisy:
            print("  %d files are still in Italian:" % len(noisy))
            for path, found in noisy[:12]:
                print("      %-58s %s" % (path, ", ".join(found[:4])))
            if len(noisy) > 12:
                print("      ... and %d more" % (len(noisy) - 12))
            bad += 1
        else:
            covered, uncovered = coverage(root, config)
            print("  silent: no tracked file is in Italian "
                  "(%d covered, %d outside the extensions)"
                  % (len(covered), len(uncovered)))
    except (RuntimeError, SystemExit) as failure:
        print("  not verifiable (%s)" % failure)

    print()
    print("selftest: %s" % ("ALL GOOD" if not bad else "%d PROBLEMS" % bad))
    return 1 if bad else 0


def git_root(start: "pathlib.Path | None" = None) -> pathlib.Path:
    """The repository you are standing in - the tree this run is about.

    There is no guessing and therefore no refusal: the subject is where the
    caller is, not where this module happens to be installed.
    """
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       cwd=str(start) if start else None,
                       capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        raise SystemExit("not inside a git repository, so there is no tree to "
                         "judge. Pass --root to name one.")
    return pathlib.Path(r.stdout.strip()).resolve()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", help="the repository to judge "
                                  "(default: the one you are standing in)")
    p.add_argument("--range", dest="rev_range",
                   help="only the files a range touches, e.g. origin/main..HEAD")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        return selftest()

    root = pathlib.Path(a.root).resolve() if a.root else git_root()
    config = config_for(root)

    dead = dead_exclusions(root, config)
    if dead:
        print("REFUSED: %d declared exclusion(s) name nothing in %s:"
              % (len(dead), root))
        for e in dead:
            print("   %s" % e)
        print()
        print("An exclusion is a hole cut in this gate on purpose. One that "
              "names nothing is either aimed at the wrong wall or came from "
              "another repository's configuration. Remove it or fix it.")
        return 1

    guilty = scan(root, tracked(root, a.rev_range), config)
    if not guilty:
        # ⛔ A GREEN SAYS WHAT IT CHECKED. The bare sentence was believed about
        # the wrong repository once already; naming the tree and the count costs
        # one line and makes that impossible to do silently.
        covered, uncovered = coverage(root, config)
        print("english only: clean (%s: %d file(s) read, %d outside the "
              "extensions)" % (root, len(covered), len(uncovered)))
        return 0
    print("%d file(s) are not in English:" % len(guilty))
    for path, found in guilty:
        print("   %-58s %s" % (path, ", ".join(found[:6])))
    print()
    print("This repository is public and English-only. Translate these files.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
