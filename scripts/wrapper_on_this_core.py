#!/usr/bin/env python3
"""The wrapper's own tests that stand on this core, run against this core.

The core is pinned by the wrapper and published BEFORE it, so for a release of the
core nothing used to run the wrapper's tests on the code about to be published:
the wrapper's CI installs the PINNED core from the index. This is the job that
does: `wrapper-on-this-core` of .github/workflows/ci.yml installs the wrapper's
`main`, puts THIS checkout's core over the pin, and runs the wrapper test files
that touch the core's Juggler client directly.

Which files is COMPUTED here, never typed (`computed_list`): a test file of the
wrapper that imports `invisible_core.juggler`, or that builds a `Lifecycle`, an
`InjectedScript` or a `Connection` by hand. A new wrapper test of that kind joins
the run the day it is written; nobody keeps a list.

    wrapper_on_this_core.py list    --wrapper DIR
        the files, one per line (sorted).
    wrapper_on_this_core.py compare --wrapper DIR (--main-script PATH | --main-has-no-script)
        exit 1 unless the list is not empty and holds every file the list of MAIN's
        copy of this script gives on the same tree. The list of main is computed in
        the same job, on the same wrapper, by the copy of this script that main has:
        a change of the patterns in a branch cannot shorten the run without being
        seen, and a test the wrapper deleted leaves both lists alike. What main's copy
        is can be said only by the workflow, which checks main out: a copy that cannot
        be read is red; "main has no copy" is declared with --main-has-no-script, and
        only for the pull request that adds this file.
    wrapper_on_this_core.py engine
        the engine the seal of the installed core names, which must be PUBLISHED: if
        its release or the asset of this system is not there it is exit 1 with the
        reason, never a skip. Prints the path of the fetched binary as the last line.
    wrapper_on_this_core.py run     --wrapper DIR --binary PATH --os linux|windows --part N [--parts 2]
        runs the files of the part the way the wrapper's own e2e runner does
        (invisible_core.testing.e2e: the same command, four browsers at once, the
        wall-clock tests alone at the end) and judges the junit: red on a failure, on
        a SKIPPED test, on a pass that ran nothing, or on a file of the part of which
        no test ran.

Standard library only, except `engine` and `run`, which import the installed core.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

__all__ = ["EXCLUDED", "computed_list", "excluded_nodes", "files_of_part", "judge_junit",
           "missing_from", "problems_in_junit"]

#: A wrapper test file that imports the Juggler client of the core.
IMPORTS_JUGGLER = re.compile(
    r"^[ \t]*(?:from[ \t]+invisible_core\.juggler\b"
    r"|import[ \t]+invisible_core\.juggler\b"
    r"|from[ \t]+invisible_core[ \t]+import[ \t]+[^#\n]*\bjuggler\b)",
    re.MULTILINE)
#: A wrapper test file that builds one of the core's objects by hand.
BUILDS_BY_HAND = re.compile(r"\b(?:Lifecycle|InjectedScript|Connection)\(")

#: Vendored upstream suite: it has its own dependencies and is never collected.
_NEVER_COLLECTED = "playwright-upstream"

#: What this run leaves out, and why. A skipped test turns the run red, so a test
#: that cannot run on a system is taken out HERE, by name, with its reason: not
#: left to skip. Each entry is (system or None for both, test file or node id,
#: reason).
EXCLUDED = (
    ("windows", "tests/test_juggler_posix_pipe.py",
     "the POSIX leg of the pipe does not execute on Windows (module-level skipif on win32)"),
    ("windows", "tests/test_a_missing_system_theme_pref_refuses_the_launch_e2e.py::test_a_missing_pref_refuses_the_launch",
     "the system theme is declared, and its absence refused, on Linux only (skipif on win32)"),
    (None, "tests/test_juggler_transport.py::test_the_workbench_inventory_and_the_package_cannot_DRIFT",
     "reads a script of the maintainer's workbench, which no clone has (pytest.skip)"),
    (None, "tests/test_juggler_injected.py::test_OUR_CLIENT_DOES_NOT_CROSS_into_the_page_realm",
     "reads a trap page of the maintainer's workbench, which no clone has (pytest.skip)"),
)


# ------------------------------------------------------------------ the list

def computed_list(wrapper: Path) -> list[str]:
    """The test files of the wrapper checked out at ``wrapper`` that stand on the
    core's Juggler client, as paths relative to it with `/`, sorted."""
    wrapper = Path(wrapper)
    tests = wrapper / "tests"
    found = []
    for path in sorted(tests.rglob("test_*.py")):
        rel = path.relative_to(wrapper).as_posix()
        if _NEVER_COLLECTED in rel.split("/"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if IMPORTS_JUGGLER.search(text) or BUILDS_BY_HAND.search(text):
            found.append(rel)
    return found


def missing_from(computed: list[str], reference: list[str]) -> list[str]:
    """The files of ``reference`` that ``computed`` no longer has."""
    return sorted(set(reference) - set(computed))


def _excluded_for(system: str) -> list[str]:
    return [target for only, target, _ in EXCLUDED if only in (None, system)]


def excluded_nodes(system: str) -> tuple[list[str], list[str]]:
    """(whole files, node ids) this system leaves out."""
    targets = _excluded_for(system)
    return ([t for t in targets if "::" not in t], [t for t in targets if "::" in t])


def files_of_part(files: list[str], system: str, part: int, parts: int) -> list[str]:
    """The files this leg runs: the list without what the system leaves out, every
    ``parts``-th file from the part's offset."""
    whole, _ = excluded_nodes(system)
    kept = [f for f in files if f not in whole]
    return [f for i, f in enumerate(kept) if i % parts == part - 1]


# ----------------------------------------------------------------- the judge

def _classname_of(rel: str) -> str:
    return rel[:-3].replace("/", ".")


def problems_in_junit(junit_paths: list[Path], files: list[str]) -> list[str]:
    """Every reason the run of ``files`` is red, read from the junit it wrote."""
    problems: list[str] = []
    total = 0
    seen: set[str] = set()
    for jp in junit_paths:
        if not Path(jp).exists():
            continue  # a pass that collected nothing writes none: judged by the total
        root = ET.parse(jp).getroot()
        for case in root.iter("testcase"):
            cls, name = case.get("classname") or "", case.get("name") or ""
            if not cls:
                problems.append(f"{jp.name}: a testcase with no classname (not xunit2): {name}")
                continue
            skipped = case.find("skipped")
            if skipped is not None and skipped.get("type") == "pytest.rerun":
                continue  # a failed attempt that was run again: its last attempt is another testcase
            total += 1
            for f in files:
                c = _classname_of(f)
                if cls == c or cls.startswith(c + "."):
                    seen.add(f)
            if case.find("failure") is not None or case.find("error") is not None:
                problems.append(f"FAILED {cls}::{name}")
            elif skipped is not None:
                reason = (skipped.get("message") or "").strip()
                problems.append(f"SKIPPED {cls}::{name}: {reason}")
    if total == 0:
        problems.append("no test ran at all: a run that ran nothing has not passed")
    for f in files:
        if f not in seen:
            problems.append(f"no test of {f} ran: it was deselected, not collected, or the run died first")
    return problems


def judge_junit(junit_paths: list[Path], files: list[str], codes: list[int]) -> list[str]:
    problems = problems_in_junit(junit_paths, files)
    # 5 is "nothing was collected": a pass of the run that had no test of its kind.
    bad = [c for c in codes if c not in (0, 5)]
    if bad:
        problems.append(f"pytest ended with code(s) {bad}")
    return problems


# ------------------------------------------------------------------ commands

def _cmd_list(args) -> int:
    for f in computed_list(Path(args.wrapper)):
        print(f)
    return 0


def _load_main_copy(path: Path):
    spec = importlib.util.spec_from_file_location("wrapper_on_this_core_main", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _cmd_compare(args) -> int:
    wrapper = Path(args.wrapper)
    mine = computed_list(wrapper)
    print(f"this checkout computes {len(mine)} test file(s) of the wrapper")
    if not mine:
        print("the computed list is EMPTY: the patterns find no wrapper test that stands on "
              "the core's Juggler client, so this job would run nothing", file=sys.stderr)
        return 1
    if args.main_has_no_script:
        print("main has no copy of this script (this is the change that adds it): declared, "
              "nothing to compare with")
        return 0
    try:
        reference = _load_main_copy(Path(args.main_script)).computed_list(wrapper)
    except Exception as exc:  # a copy of main that cannot be read is not "main has none"
        print(f"main's copy of this script cannot be run ({exc!r}): it is not known what the "
              f"list of main is", file=sys.stderr)
        return 1
    print(f"main's copy of this script computes {len(reference)} on the same tree")
    gone = missing_from(mine, reference)
    if gone:
        print("this checkout computes a SHORTER list than main: it lost\n  " + "\n  ".join(gone),
              file=sys.stderr)
        return 1
    return 0


def _cmd_engine(_args) -> int:
    from invisible_core.download import ensure_binary
    from invisible_core.seal import active_seal
    from invisible_core.constants import RELEASE_URL_TEMPLATE

    seal = active_seal()
    key = sys.platform
    asset = seal.asset_for(key, platform.machine())
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/releases/", RELEASE_URL_TEMPLATE)
    owner, repo = m.group(1), m.group(2)
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{seal.tag}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "wrapper-on-this-core"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
            names = {a.get("name") for a in json.load(r).get("assets", [])}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"the seal of this core names the engine {seal.tag}, and {owner}/{repo} has no "
                  f"release by that name: it is NOT PUBLISHED. Publish the engine first; a "
                  f"release of the core waits for it. This job does not skip.", file=sys.stderr)
        else:
            print(f"the release index answered {exc.code} for {seal.tag}: it is not known whether "
                  f"the engine is published", file=sys.stderr)
        return 1
    if asset.name not in names:
        print(f"the engine {seal.tag} is published but has no {asset.name} (the asset of this "
              f"system): NOT PUBLISHED for {key}. This job does not skip.", file=sys.stderr)
        return 1
    print(f"engine {seal.tag}: {asset.name} is published")
    print(ensure_binary())
    return 0


def _cmd_run(args) -> int:
    from invisible_core.testing import e2e

    wrapper = Path(args.wrapper).resolve()
    files = computed_list(wrapper)
    mine = files_of_part(files, args.os, args.part, args.parts)
    _, nodes = excluded_nodes(args.os)
    print(f"[wrapper-on-this-core] {len(files)} files computed, part {args.part} of {args.parts} "
          f"on {args.os}: {len(mine)} file(s)")
    for only, target, reason in EXCLUDED:
        if only in (None, args.os):
            print(f"[wrapper-on-this-core] left out: {target}: {reason}")
    if not mine:
        print("[wrapper-on-this-core] this part has no file to run", file=sys.stderr)
        return 1

    out = Path(args.junit_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)  # unlike run_e2e.main, INVISIBLE_CORE_PIN is kept: the pin is what this job overrides
    env["INVPW_BINARY_PATH"] = args.binary
    env["STEALTHFOX_E2E_BINARY"] = args.binary
    env.pop("PYTEST_ADDOPTS", None)

    selections = [(args.procs, "not slow and not e2e")] + e2e.passes(args.procs)
    extra_nodes = ["--deselect=" + n for n in nodes]
    codes, junits = [], []
    for i, (width, selection) in enumerate(selections):
        junit = out / f"pass-{i}.xml"
        junits.append(junit)
        cmd = e2e.command(width, mine + extra_nodes + ["-o", "junit_family=xunit2",
                                                       f"--junitxml={junit}", "-rs"],
                          selection=selection)
        print(f"[wrapper-on-this-core] -m '{selection}', browsers at once: {width}", flush=True)
        print("[wrapper-on-this-core] " + " ".join(cmd), flush=True)
        codes.append(subprocess.run(cmd, cwd=wrapper, env=env).returncode)
        if codes[-1] not in (0, 1, 5):
            break
    problems = judge_junit(junits, mine, codes)
    if problems:
        print("[wrapper-on-this-core] RED:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    print("[wrapper-on-this-core] green: every file of the part ran, nothing failed, nothing skipped")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("--wrapper", required=True)
    s = sub.add_parser("compare")
    s.add_argument("--wrapper", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--main-script")
    g.add_argument("--main-has-no-script", action="store_true")
    sub.add_parser("engine")
    s = sub.add_parser("run")
    s.add_argument("--wrapper", required=True)
    s.add_argument("--binary", required=True)
    s.add_argument("--os", required=True, choices=["linux", "windows"])
    s.add_argument("--part", type=int, required=True)
    s.add_argument("--parts", type=int, default=2)
    s.add_argument("--procs", default="4")
    s.add_argument("--junit-dir", default="wrapper-on-this-core-junit")
    args = p.parse_args(argv)
    return {"list": _cmd_list, "compare": _cmd_compare, "engine": _cmd_engine,
            "run": _cmd_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
