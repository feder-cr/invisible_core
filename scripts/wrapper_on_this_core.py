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
    wrapper_on_this_core.py plan    --wrapper DIR --os linux|windows --parts N [--collect]
        prints the plan the legs share: the estimated seconds of each part and what it
        runs. Without --collect a long file gets no node list (it is shown whole).
    wrapper_on_this_core.py run     --wrapper DIR --binary PATH --os linux|windows --part N --parts N
        runs the part the way the wrapper's own e2e runner does
        (invisible_core.testing.e2e: the same command, four browsers at once, the
        wall-clock tests alone at the end) and judges the junit: red on a failure, on
        a SKIPPED test, on a pass that ran nothing, or on a file of the part of which
        no test ran.

Which part runs what is a PLAN (`plan_parts`), the same on every leg: the seconds each
test file took are in scripts/wrapper_test_seconds.json (estimated from the logs of
the wrapper's e2e, for balancing the legs only: it never decides the verdict). A file
over SPLIT_OVER_SECONDS is cut into chunks of its test nodes (the ids come from
`pytest --collect-only` in the leg); the units (a whole file, or a chunk) are then
handed to the parts longest first, each to the part with the least estimated time.

Standard library only, except `engine` and `run`, which import the installed core.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

__all__ = ["EXCLUDED", "computed_list", "excluded_nodes", "files_of", "judge_junit", "load_seconds",
           "missing_from", "plan_parts", "problems_in_junit"]

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
    (None, "tests/test_a_missing_system_theme_pref_refuses_the_launch_e2e.py",
     "every test of the file asks engine_at_least(\"firefox-65\") and the engine the seal names is older "
     "(the engine gate skips); on Windows the refusal tests also skip by themselves (skipif on win32: "
     "the system theme is declared, and its absence refused, on Linux only)"),
    ("windows", "tests/test_fission_strategy1_bench.py::test_same_site_other_port_stays_in_process",
     "it asks for the processes of a site by name, which only Linux has (tests/_processes.py: "
     "pytest.skip off Linux; Windows names no process, ProcessUtils_none.cpp)"),
    ("windows", "tests/test_fission_strategy1_bench.py::test_cross_site_iframe_gets_its_own_process",
     "the same oracle as the test above: it asks for the processes of a site by name on its first "
     "line, and tests/_processes.py skips off Linux"),
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


# ------------------------------------------------------------------ the plan

#: A test file the table has no row for is taken to have run this many seconds in
#: parallel (and none serial).
DEFAULT_SECONDS = 30.0
#: A file whose parallel seconds are over this is cut into chunks of its nodes.
SPLIT_OVER_SECONDS = 90.0
#: The browsers at once of a leg (the `--procs` of the run): a parallel second of
#: the table counts 1/PARALLEL_WIDTH of a second of a leg, a serial one counts 1.
PARALLEL_WIDTH = 4
SECONDS_TABLE = Path(__file__).with_name("wrapper_test_seconds.json")


def load_seconds(system: str, path: Path | None = None) -> dict:
    """The table of this system: {test file: {"parallel": s, "serial": s, "nodes": {...}}}."""
    with open(path or SECONDS_TABLE, encoding="utf-8") as fh:
        return json.load(fh)[system]


def _unit_seconds(parallel: float, serial: float) -> float:
    return parallel / PARALLEL_WIDTH + serial


def _is_excluded(node: str, nodes_out: list[str]) -> bool:
    # --deselect matches by prefix, so a parametrized test is left out whole.
    return any(node == n or node.startswith(n + "[") for n in nodes_out)


def _split_file(rel: str, row: dict, nodes: list[str], k: int) -> list[dict]:
    """``nodes`` of a file cut into ``k`` chunks balanced by the seconds the table has
    for each node (an unknown node: the average of the file's known ones)."""
    prefix = rel + "::"
    par = {prefix + n: sec for n, sec in row.get("nodes", {}).items()}
    ser = {prefix + n: sec for n, sec in row.get("serial_nodes", {}).items()}
    known = list(par.values()) + list(ser.values())
    average = sum(known) / len(known) if known else DEFAULT_SECONDS

    def cost(node: str) -> float:
        if node in ser:
            return ser[node]
        return par.get(node, average) / PARALLEL_WIDTH

    k = max(1, min(k, len(nodes)))
    chunks = [{"nodes": [], "seconds": 0.0} for _ in range(k)]
    for node in sorted(nodes, key=lambda n: (-cost(n), n)):
        target = min(chunks, key=lambda c: c["seconds"])  # the first of equals: deterministic
        target["nodes"].append(node)
        target["seconds"] += cost(node)
    return [c for c in chunks if c["nodes"]]


def plan_parts(files: list[str], system: str, parts: int, collect, table: dict | None = None) -> list[dict]:
    """What each of the ``parts`` legs runs; the same on every leg.

    ``collect(file)`` gives the node ids of a file (`pytest --collect-only -q`); it is
    asked only for the files the table has as long. Returns one dict per part, in the
    order of the parts: {"files": whole files, "nodes": node ids of the chunks,
    "seconds": the estimate}. Left out first: the files and nodes EXCLUDED here.
    """
    table = load_seconds(system) if table is None else table
    whole, nodes_out = excluded_nodes(system)
    kept = sorted(f for f in files if f not in whole)

    rows = {f: table.get(f, {"parallel": DEFAULT_SECONDS, "serial": 0.0}) for f in kept}
    secs = {f: _unit_seconds(r["parallel"], r.get("serial", 0.0)) for f, r in rows.items()}
    target = sum(secs.values()) / parts

    units: list[dict] = []
    for f in kept:
        if f in table and rows[f]["parallel"] > SPLIT_OVER_SECONDS:
            nodes = sorted(n for n in collect(f) if not _is_excluded(n, nodes_out))
            if nodes:
                k = max(2, math.ceil(secs[f] / target)) if target > 0 else 2
                units += [{"file": f, **c} for c in _split_file(f, rows[f], nodes, min(k, parts))]
                continue
        units.append({"file": f, "nodes": None, "seconds": secs[f]})

    out = [{"files": [], "nodes": [], "seconds": 0.0} for _ in range(parts)]
    for u in sorted(units, key=lambda u: (-u["seconds"], u["file"], u["nodes"] or [])):
        part = min(out, key=lambda p: p["seconds"])
        if u["nodes"] is None:
            part["files"].append(u["file"])
        else:
            part["nodes"] += u["nodes"]
        part["seconds"] += u["seconds"]
    for part in out:
        part["files"].sort()
        part["nodes"].sort()
        part["seconds"] = round(part["seconds"], 1)
    return out


def files_of(part: dict) -> list[str]:
    """Every test file a part touches: the whole ones and those of its node ids."""
    return sorted(set(part["files"]) | {n.split("::", 1)[0] for n in part["nodes"]})


def collect_nodes(wrapper: Path, env: dict):
    """A ``collect`` for plan_parts: the node ids of a file, as the legs see them."""
    def collect(rel: str) -> list[str]:
        cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", rel]
        done = subprocess.run(cmd, cwd=wrapper, env=env, capture_output=True, text=True)
        nodes = [ln.strip() for ln in done.stdout.splitlines() if ln.startswith(rel + "::")]
        if done.returncode != 0 or not nodes:
            # A leg that fell back to the whole file would run what another leg runs too.
            raise RuntimeError(f"cannot list the nodes of {rel} (pytest exit {done.returncode}):\n"
                               f"{done.stdout[-2000:]}\n{done.stderr[-2000:]}")
        return nodes
    return collect


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


def _cmd_plan(args) -> int:
    wrapper = Path(args.wrapper).resolve()
    collect = collect_nodes(wrapper, dict(os.environ)) if args.collect else (lambda f: [])
    plan = plan_parts(computed_list(wrapper), args.os, args.parts, collect)
    for n, part in enumerate(plan, 1):
        print(f"part {n}/{args.parts} {args.os}: ~{part['seconds']} s, {len(part['files'])} file(s), "
              f"{len(part['nodes'])} node(s)")
        for f in part["files"]:
            print("  " + f)
        for node in part["nodes"]:
            print("  " + node)
    return 0


def _cmd_run(args) -> int:
    from invisible_core.testing import e2e

    wrapper = Path(args.wrapper).resolve()
    files = computed_list(wrapper)
    _, nodes = excluded_nodes(args.os)
    env = dict(os.environ)  # unlike run_e2e.main, INVISIBLE_CORE_PIN is kept: the pin is what this job overrides
    env["INVPW_BINARY_PATH"] = args.binary
    env["STEALTHFOX_E2E_BINARY"] = args.binary
    env.pop("PYTEST_ADDOPTS", None)
    plan = plan_parts(files, args.os, args.parts, collect_nodes(wrapper, env))
    for n, part in enumerate(plan, 1):
        print(f"[wrapper-on-this-core] plan: part {n}/{args.parts} ~{part['seconds']} s, "
              f"{len(part['files'])} file(s) + {len(part['nodes'])} node(s)")
    chosen = plan[args.part - 1]
    mine = files_of(chosen)
    print(f"[wrapper-on-this-core] {len(files)} files computed, part {args.part} of {args.parts} "
          f"on {args.os}: {len(chosen['files'])} whole file(s), {len(chosen['nodes'])} node(s) "
          f"of the long files, ~{chosen['seconds']} s")
    for only, target, reason in EXCLUDED:
        if only in (None, args.os):
            print(f"[wrapper-on-this-core] left out: {target}: {reason}")
    if not mine:
        print("[wrapper-on-this-core] this part has no file to run", file=sys.stderr)
        return 1

    out = Path(args.junit_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    selections = [(args.procs, "not slow and not e2e")] + e2e.passes(args.procs)
    extra_nodes = ["--deselect=" + n for n in nodes]
    codes, junits = [], []
    for i, (width, selection) in enumerate(selections):
        junit = out / f"pass-{i}.xml"
        junits.append(junit)
        cmd = e2e.command(width, chosen["files"] + chosen["nodes"] + extra_nodes + ["-o", "junit_family=xunit2",
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
    s = sub.add_parser("plan")
    s.add_argument("--wrapper", required=True)
    s.add_argument("--os", required=True, choices=["linux", "windows"])
    s.add_argument("--parts", type=int, required=True)
    s.add_argument("--collect", action="store_true")
    s = sub.add_parser("run")
    s.add_argument("--wrapper", required=True)
    s.add_argument("--binary", required=True)
    s.add_argument("--os", required=True, choices=["linux", "windows"])
    s.add_argument("--part", type=int, required=True)
    s.add_argument("--parts", type=int, required=True)
    s.add_argument("--procs", default="4")
    s.add_argument("--junit-dir", default="wrapper-on-this-core-junit")
    args = p.parse_args(argv)
    return {"list": _cmd_list, "compare": _cmd_compare, "engine": _cmd_engine, "plan": _cmd_plan,
            "run": _cmd_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
