"""A README that promises to beat everything is refused. This is the check.

The circumvention claim itself is OPEN: the owner decided on 2026-08-08 that
naming a protection vendor and saying the product gets past it is allowed, after
measuring that those searches are winnable. What is not allowed is a claim no
measurement can support, and the shape of that claim is mechanical: a word that
quantifies EVERYTHING, next to a word of getting past something, next to a word
naming a protection, in one sentence. "It passes every bot detection test" is
that sentence, and it was live on a public README on 2026-09-22.

    python -m invisible_core.claims              # 1 if a refused claim is found
    python -m invisible_core.claims --selftest   # the known-bad corpus

⛔ IT LIVES HERE BECAUSE THE CHECK THAT FOUND IT COULD ONLY RUN BY HAND. It was a
script in a private workbench that walks the account over the GitHub API, so it
ran when somebody remembered, and never on the pull request that introduced a
sentence. A gate that runs after the text is public is an audit, not a gate. So
the LOGIC is here, once, and each repository runs it on every change, the same
arrangement as `invisible_core.english`.

WHAT IS PER REPOSITORY is which files are read, declared under
`[tool.invisible.claims] files = [...]` in `pyproject.toml`, defaulting to
`README.md`. A declared file that does not exist is REFUSED: a perimeter that
names nothing looks like a clean pass and checks nothing.

⛔ AND THE DEFAULT IS THE README ON PURPOSE, MEASURED 2026-09-23. The rule is a
conjunction of three words in a sentence, and it was calibrated on READMEs: one
true positive and zero false ones across the three repositories. Run over every
tracked Markdown file of the same three it flags 92 sentences, and nearly all of
them are NEGATIONS - "this project does not sell a bypass for any vendor" - or
questions in a FAQ. A gate that cries wolf is a gate somebody switches off, so
the perimeter is where the rule is exact, and the README is also the page PyPI
shows, which is where a promise is read the most.

THREE SCANS, AND ONLY TWO REFUSE:

  * unsupportable - an absolute, a circumvention word and a protection word in
    one sentence. REFUSED on grounds of truth, not of policy.
  * fraud - account-creation terms. REFUSED: there is no automation reading of
    "sms activation" or "bulk accounts", and that half was never opened.
  * claims - a circumvention word next to a protection word. REPORTED, never
    refused, so the 2026-08-08 decision stays visible in every run instead of
    disappearing from the tooling.

KNOWN LIMITS, said out loud because a gate whose limits are unstated gets
trusted too far. A negation still fires: "does not bypass any WAF" is refused,
which is why docs are outside the default perimeter. An absolute BOUNDED by a
number - "passed all five detection suites" - is checkable and is refused
anyway; the remedy is to write the figure instead of the quantifier, which is
better copy regardless. And it reads English only.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

#: Words that assert getting past something. BASE FORMS: `has` derives the
#: inflections, and they must not be spelled out here. Until 2026-09-22 this
#: list carried them by hand while PROTECTION did not, and one letter defeated
#: the pair: "No captchas." was called clean for months because "captchas" does
#: not match "captcha" at a word edge.
CIRCUMVENTION = (
    "bypass",
    "evade", "evasion",
    "defeat",
    "beat",
    "crack",
    "solve", "solver",
    "undetected", "undetectable",
    "pass",
)

#: Protection technologies and controls. Not forbidden words - most are
#: unavoidable when describing this work. They are the other half of the pair.
PROTECTION = (
    "captcha", "recaptcha", "hcaptcha",
    "bot detection", "bot-detection", "anti-bot", "antibot",
    "bot mitigation", "waf", "detection",
    "fingerprint",
)

#: Words that quantify EVERYTHING. Beside the other two halves they are a
#: promise no measurement supports.
ABSOLUTES = ("every", "any", "all", "always", "100%", "guaranteed")

#: Account-creation fraud. Flagged on its own: no reading of these is testing.
STANDALONE = (
    "sms-activation", "sms activation",
    "account creation", "account-creation",
    "bulk accounts", "multi-accounting",
)

#: The inflections `has` derives: a CLOSED set of English suffixes, not a
#: stemmer. The match still ends at a word edge, so "pass" reaches "passes" and
#: "passed" and never "passport".
_SUFFIXES = ("", "s", "es", "ing", "ed")

_EDGE_BEFORE = r"(?<![A-Za-z0-9])"
_EDGE_AFTER = r"(?![A-Za-z0-9])"


def _inflected(word: str) -> str:
    """Regex alternation for `word` and its ordinary English inflections.

    Longest first, so the alternation does not settle on the bare stem and
    leave the suffix outside the word-edge lookahead.
    """
    forms = {word + suffix for suffix in _SUFFIXES}
    if word.endswith("e"):                       # evade -> evading, evaded
        forms |= {word[:-1] + suffix for suffix in ("ing", "ed")}
    ordered = sorted(forms, key=len, reverse=True)
    return "(?:" + "|".join(re.escape(f) for f in ordered) + ")"


def has(text: str, words) -> list:
    """The words of `words` present in `text`: whole-word, case-insensitive,
    and INFLECTION-AWARE. The one place that knows a word has other forms."""
    return [w for w in words
            if re.search(_EDGE_BEFORE + _inflected(w) + _EDGE_AFTER,
                         text, re.IGNORECASE)]


def sentences(text: str) -> list:
    """Markdown as sentences, without code, HTML or link targets.

    By SENTENCE and not by field: pairing words across a whole README would
    flag everything. Code blocks and inline code go (a command naming a vendor
    is not a claim), HTML goes (a title `div` gave three findings), and a link
    keeps its text and loses its URL.
    """
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"`[^`]*`", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    return [re.sub(r"\s+", " ", s).strip()
            for s in re.split(r"(?<=[.!?])\s+|\n\n+", t) if s.strip()]


def scan_unsupportable(text: str) -> list:
    """The sentences that promise to beat everything. REFUSED.

    The conjunction is of THREE for a measurement, not for caution: the
    absolute with the protection word alone gives 1 true and 4 false positives
    on the three READMEs ("Every session gets a distinct fingerprint" is a fact
    about our own behaviour). With the circumvention word too: 1, and none false.
    """
    out = []
    for s in sentences(text):
        c, p, a = has(s, CIRCUMVENTION), has(s, PROTECTION), has(s, ABSOLUTES)
        if c and p and a:
            out.append("%r promises to beat everything: %s next to %s and %s"
                       % (s[:120], a, c, p))
    return out


def scan_fraud(text: str) -> list:
    """Account-creation fraud terms. REFUSED."""
    return ["%r describes account-creation fraud, not testing" % t
            for t in has(text, STANDALONE)]


def scan_claims(text: str) -> list:
    """A circumvention word next to a protection word, per sentence. REPORTED,
    NOT REFUSED: open since 2026-08-08 by owner decision."""
    out = []
    for s in sentences(text):
        c, p = has(s, CIRCUMVENTION), has(s, PROTECTION)
        if c and p:
            out.append("%r: %s next to %s" % (s[:100], c, p))
    return out


DEFAULT_FILES = ("README.md",)


def files_for(root: pathlib.Path) -> tuple:
    """The files `[tool.invisible.claims]` declares, or the README.

    An absent declaration means the default perimeter, never an empty one.
    """
    import tomllib

    path = pathlib.Path(root) / "pyproject.toml"
    if not path.is_file():
        return DEFAULT_FILES
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    section = data.get("tool", {}).get("invisible", {}).get("claims", {})
    return tuple(section.get("files", DEFAULT_FILES))


def judge(root: pathlib.Path, files) -> tuple:
    """(refused, reported, missing) for `files` under `root`.

    Each finding is (path, sentence). A file that does not exist goes to
    `missing` and is a refusal of its own: it would otherwise read as clean.
    """
    refused, reported, missing = [], [], []
    for name in files:
        path = pathlib.Path(root) / name
        if not path.is_file():
            missing.append(name)
            continue
        text = path.read_bytes().decode("utf-8", "replace")
        refused += [(name, f) for f in scan_unsupportable(text) + scan_fraud(text)]
        reported += [(name, f) for f in scan_claims(text)]
    return refused, reported, missing


#: (name, text) that MUST be refused. Each one is a CLASS of variation, not an
#: element: plural, verb form, case, hyphen, the sentence that was really live.
KNOWN_BAD = (
    ("the sentence that was live on 2026-09-22",
     "Open source, and it passes every bot detection test."),
    ("a plural protection word",
     "It solves all captchas."),
    ("a verb form of the circumvention word",
     "Bypassing any WAF, guaranteed."),
    ("upper case",
     "EVADES EVERY FINGERPRINT CHECK."),
    ("the hyphenated protection word",
     "Always undetected by anti-bot systems."),
    ("inside a link, which keeps its text",
     "[Beats every captcha](https://example.com)."),
    ("fraud wording",
     "An sms-activation service for bulk accounts."),
)

#: (name, text) that must NOT be refused. Weighed like the others: a gate that
#: refuses everything is as useless as one that passes everything.
MUST_NOT_FIRE = (
    ("an absolute and a protection word with no circumvention word",
     "Every session gets a distinct fingerprint."),
    ("a circumvention claim with no absolute: open, only reported",
     "It bypasses Cloudflare bot detection."),
    ("an absolute and a circumvention word with no protection word",
     "Pass any seed to reproduce a run."),
    ("'pass' must not reach inside 'passport'",
     "Every passport photo detection example is in the docs."),
    ("'detection' must not reach 'detective'",
     "It beats every detective novel."),
    ("a command in a code block is not a claim",
     "Run this:\n\n```\nbypass-every-captcha --all\n```\n"),
    ("inline code is not a claim",
     "The flag `--solve-every-captcha` does not exist."),
    # The case the fenced-block rule exists for. Without it the inline rule
    # still pairs backticks, and on an ordinary block that removes the content
    # anyway - measured, the first corpus could not tell the rule was there. A
    # stray backtick in the code shifts the pairing and leaves the line exposed.
    ("a fenced block whose code holds a stray backtick",
     "Example:\n\n```\nx`\nbypass every captcha\n```\n"),
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
    print("--- mutations that MUST be refused ---")
    for name, text in KNOWN_BAD:
        hit = bool(scan_unsupportable(text) or scan_fraud(text))
        print("  %s: %s" % ("killed" if hit else "SURVIVED", name))
        bad += not hit
    print("--- cases that must NOT be refused ---")
    for name, text in MUST_NOT_FIRE:
        hit = bool(scan_unsupportable(text) or scan_fraud(text))
        print("  %s: %s" % ("FALSE POSITIVE" if hit else "silent", name))
        bad += hit
    # And the report half must still report: a relaxed gate that quietly
    # stopped seeing anything is the failure mode of a gate that only informs.
    if not scan_claims("It bypasses Cloudflare bot detection."):
        print("  SURVIVED: an open claim is no longer even reported")
        bad += 1

    # SILENT on this repository as it stands: a gate born red on what already
    # exists teaches people to bypass it.
    print("--- the real repository ---")
    try:
        root = git_root()
        refused, _reported, missing = judge(root, files_for(root))
        if missing or refused:
            print("  %d missing, %d refused" % (len(missing), len(refused)))
            bad += 1
        else:
            print("  silent")
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
    files = files_for(root)
    refused, reported, missing = judge(root, files)

    if missing:
        print("REFUSED: %d declared file(s) do not exist in %s: %s"
              % (len(missing), root, ", ".join(missing)))
        print("A perimeter that names nothing reads as a clean pass. Fix "
              "[tool.invisible.claims] files, or remove the entry.")
        return 1
    if refused:
        print("REFUSED: %d sentence(s) promise what no measurement supports:"
              % len(refused))
        for name, finding in refused:
            print("   %s: %s" % (name, finding))
        print()
        print("A circumvention claim is open since 2026-08-08. An ABSOLUTE beside "
              "it is not a stronger claim, it is one nothing can support. Name "
              "what was measured instead: a figure is checkable, 'every' is not.")
        return 1
    for name, finding in reported:
        print("reported, not refused (open since 2026-08-08): %s: %s"
              % (name, finding))
    # A green says what it checked.
    print("claims: clean (%s: %d file(s) read: %s)"
          % (root, len(files), ", ".join(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
