"""The job that installs the package as a user does runs on the release that just closed. This is the check.

    python -m invisible_core.install_job              # 1 if user-install.yml is not wired so
    python -m invisible_core.install_job --selftest   # the known-bad corpus

THE JOB NEVER RAN AT RELEASE TIME ([B347]). Every repository's
`.github/workflows/user-install.yml` installs the published package from the
index, as a user would, and it was triggered by `release: published`. The
Release is created by the publish workflow with GITHUB_TOKEN, and an event that
token causes starts no other workflow: on 2026-10-18 the job had run on a
release once in the wrapper's history (v0.35.8) and never in the core's,
selenium's or puppeteer's, so the check of what users get was not made when it
mattered, and nothing said so.

It runs on the LEDGER pull request now: `pull_request` with `paths` naming
`PUBLISHED.json`. That PR exists only after the upload and `record`, carries the
changelog date the index recorded, and is what main becomes; its runs wait for
approval like the PR's other checks, and merging the ledger waits for them, so
the release does not close before the install of what it published has passed.

WHAT IS CHECKED, in the repository you are standing in:

  * the job runs on a pull request that changes `PUBLISHED.json`, and on no
    `release:` event, which would be a trigger that reads as live and is not;
  * its "wait for the index" step is scoped to that pull request and bounded:
    the index serves a new version some time after the upload, a run that does
    not wait reports a missing publish that happened two minutes earlier, and a
    wait with no deadline hangs where it should fail.

Each repository runs this on ITS OWN tree in its own CI, so a change to its
workflow is judged in the pull request that makes it. The check used to be a
test in the core reading the sibling repositories' shared clones, which could
not change together: whatever the order, the core's own pre-push was red
between one release and the next (item 79's class, a check judging a tree
nobody was changing).

KNOWN LIMITS: the `on:` block is read as text, as written by hand in these
repositories (block or inline lists, quoted or not), not by a YAML parser, which
the core does not depend on. An anchor or a reference would not be followed.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

WORKFLOW = pathlib.Path(".github") / "workflows" / "user-install.yml"
LEDGER = "PUBLISHED.json"
WAIT_STEP = "wait for the index"

_DEADLINE = re.compile(r"deadline\s*=\s*time\.monotonic\(\)\s*\+\s*\d+")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _significant(lines):
    return [l for l in lines if l.strip() and not l.strip().startswith("#")]


def _strip_comment(text: str) -> str:
    return re.sub(r"\s+#.*$", "", text).strip()


def _items(value: str) -> list:
    """`[a, "b"]` or `a` as a list of bare words."""
    value = _strip_comment(value)
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [v.strip().strip("'\"") for v in value.split(",") if v.strip()]


def triggers(text: str) -> dict:
    """The `on:` block as {event: [its lines]}; an inline `on: [a, b]` gives
    each event no lines."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(?:on|\"on\"|'on'):\s*(.*)$", line)
        if not m:
            continue
        if _strip_comment(m.group(1)):
            return {event: [] for event in _items(m.group(1))}
        block = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and not nxt.startswith((" ", "\t")) and not nxt.lstrip().startswith("#"):
                break
            block.append(nxt)
        block = _significant(block)
        if not block:
            return {}
        level = min(_indent(l) for l in block)
        events, current = {}, None
        for l in block:
            if _indent(l) == level:
                current = _strip_comment(l).rstrip(":").strip().strip("'\"")
                events[current] = []
            elif current is not None:
                events[current].append(l)
        return events
    return {}


def _paths(lines: list) -> list:
    """The `paths:` of an event's lines, inline or as a block list."""
    for i, line in enumerate(lines):
        m = re.match(r"^\s*paths:\s*(.*)$", line)
        if not m:
            continue
        if _strip_comment(m.group(1)):
            return _items(m.group(1))
        out = []
        for nxt in lines[i + 1:]:
            item = re.match(r"^\s*-\s*(.+)$", nxt)
            if not item:
                break
            out.extend(_items(item.group(1)))
        return out
    return []


def _wait_step(text: str) -> "str | None":
    """The text of the step named for the wait, up to the next step."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*-\s*name:\s*['\"]?%s" % re.escape(WAIT_STEP), line):
            step, level = [line], _indent(line)
            for nxt in lines[i + 1:]:
                if nxt.strip() and _indent(nxt) <= level:
                    break
                step.append(nxt)
            return "\n".join(step)
    return None


def problems(text: str) -> list:
    """What is wrong with a user-install workflow, as sentences; none when it is
    wired to run on the ledger pull request and wait there, bounded."""
    out = []
    events = triggers(text)
    if "release" in events:
        out.append("it is triggered by `release:`, which the publish workflow's "
                   "GITHUB_TOKEN never fires: the job did not run at release time")
    pr = events.get("pull_request")
    if pr is None:
        out.append("it does not run on the ledger pull request: `pull_request:` "
                   "with `paths: [%s]`" % LEDGER)
    elif LEDGER not in _paths(pr):
        out.append("its `pull_request:` is not scoped to `paths: [%s]`, so it does "
                   "not run on the ledger pull request alone" % LEDGER)
    step = _wait_step(text)
    if step is None:
        out.append("it does not wait for the index to serve the version (a step "
                   "named %r)" % WAIT_STEP)
    else:
        if "github.event_name == 'pull_request'" not in step:
            out.append("its wait for the index is not scoped to the pull request it "
                       "runs on (`if: github.event_name == 'pull_request'`)")
        if not _DEADLINE.search(step):
            out.append("its wait for the index has no deadline "
                       "(`deadline = time.monotonic() + N`), so a forgotten publish "
                       "would hang the job instead of failing it")
    return out


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


_WAIT = """      - name: wait for the index to serve this version
        if: github.event_name == '%s'
        shell: bash
        run: |
          python - <<'PY'
          import time
          deadline = time.monotonic() + 300
          PY
"""
_GOOD_ON = """on:
  workflow_dispatch:
  schedule:
    - cron: "37 6 * * 1"
  pull_request:
    paths:
      - PUBLISHED.json
"""


def _workflow(on: str, wait: "str | None" = "pull_request", bounded: bool = True) -> str:
    steps = ""
    if wait is not None:
        steps = _WAIT % wait
        if not bounded:
            steps = steps.replace("deadline = time.monotonic() + 300", "while True: pass")
    return "name: user-install\n\n%s\njobs:\n  install:\n    runs-on: ubuntu-24.04\n    steps:\n%s" % (on, steps)


KNOWN_BAD = (
    ("the old trigger, release published", _workflow(
        "on:\n  workflow_dispatch:\n  release:\n    types: [published]\n", "release")),
    ("both triggers", _workflow(_GOOD_ON + "  release:\n    types: [published]\n")),
    ("every pull request", _workflow("on:\n  pull_request:\n")),
    ("a pull request on other paths", _workflow(
        "on:\n  pull_request:\n    paths: [src/**]\n")),
    ("an inline list with release", _workflow("on: [push, release]\n")),
    ("no wait", _workflow(_GOOD_ON, None)),
    ("a wait scoped to release", _workflow(_GOOD_ON, "release")),
    ("a wait with no deadline", _workflow(_GOOD_ON, bounded=False)),
)
MUST_NOT_FIRE = (
    ("the ledger pull request, block list", _workflow(_GOOD_ON)),
    ("the ledger pull request, inline list", _workflow(
        "on:\n  workflow_dispatch:\n  pull_request:\n    paths: [\"PUBLISHED.json\"]\n")),
    ("comments in the on block", _workflow(
        "on:\n  # weekly, and on the ledger\n  pull_request:\n    paths:\n"
        "      - 'PUBLISHED.json'  # the ledger\n  schedule:\n    - cron: '0 0 * * 1'\n")),
)


def selftest() -> int:
    bad = 0
    print("--- workflows that MUST be refused ---")
    for name, text in KNOWN_BAD:
        hit = bool(problems(text))
        print("  %s: %s" % ("killed" if hit else "SURVIVED", name))
        bad += not hit
    print("--- workflows that must NOT be refused ---")
    for name, text in MUST_NOT_FIRE:
        found = problems(text)
        print("  %s: %s%s" % ("FALSE POSITIVE" if found else "silent", name,
                              " (%s)" % found[0] if found else ""))
        bad += bool(found)
    # SILENT on this repository as it stands: a gate born red on what already
    # exists teaches people to bypass it.
    print("--- the real repository ---")
    try:
        path = git_root() / WORKFLOW
        if path.is_file():
            found = problems(path.read_text(encoding="utf-8"))
            print("  %s" % ("silent" if not found else "; ".join(found)))
            bad += bool(found)
        else:
            print("  no %s here" % WORKFLOW.as_posix())
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
    path = root / WORKFLOW
    if not path.is_file():
        print("REFUSED: %s has no %s, so there is no job installing it as a user "
              "would." % (root, WORKFLOW.as_posix()))
        return 1
    found = problems(path.read_text(encoding="utf-8"))
    if found:
        print("REFUSED: %s:" % WORKFLOW.as_posix())
        for problem in found:
            print("   %s" % problem)
        return 1
    print("install_job: %s runs on the ledger pull request and waits for the index, "
          "bounded (%s)" % (WORKFLOW.as_posix(), root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
