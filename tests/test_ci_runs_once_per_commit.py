"""One CI run per commit, and a new push replaces the run it supersedes.

`ci.yml` used to trigger on `push: branches: ["**"]` AND on `pull_request`, so
every commit pushed to a branch with an open pull request started fifteen jobs
twice. About fifty of the queued jobs of the account were such duplicates while
the account's job limit was full. A pull request is covered by `pull_request`;
`push` stays only for main (and for the tags the publish workflow reads).

What the workflows promise, checked here on the files themselves:

  1. A workflow with a `pull_request` trigger has no `push` trigger that also
     matches feature branches (an unfiltered push, `**`, or a wildcard).
  2. The push triggers a gate reads are still there: `publish.yml` on `v*` tags,
     and `ci.yml` on main (the context the branch protection requires is the
     `gate` job; it keeps its name).
  3. A workflow that runs on pull requests has a concurrency group that cancels
     on pull requests and on nothing else: a push to main, a tag, a schedule or
     a manual run each get a group of their own and are never cancelled.
     Publish and scheduled workflows never cancel at all.
  4. A hunt workflow has its own group and caps every matrix at 8 parallel
     jobs.

The checkers are tested on known-bad inputs first, because a check that has
only ever said "fine" is not known to be able to say anything else.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

#: The one expression a cancelling group may use, whitespace aside.
CANCEL_ONLY_PULL_REQUESTS = "${{github.event_name=='pull_request'}}"
#: Publishing and scheduled workflows: an upload or a snapshot that has started
#: is allowed to finish.
NEVER_CANCEL = {"publish.yml", "publish-wiki.yml", "counters.yml"}
MAX_HUNT_PARALLEL = 8


def load(text: str) -> dict:
    spec = yaml.safe_load(text)
    # PyYAML reads the key `on` as the boolean True.
    if True in spec and "on" not in spec:
        spec["on"] = spec.pop(True)
    return spec


def triggers(spec: dict) -> dict:
    on = spec.get("on")
    if isinstance(on, str):
        return {on: {}}
    if isinstance(on, list):
        return {e: {} for e in on}
    return {e: (c or {}) for e, c in (on or {}).items()}


def _flat(expr) -> str:
    return re.sub(r"\s+", "", str(expr))


def problems_double_run(name: str, spec: dict) -> list[str]:
    ev = triggers(spec)
    if "pull_request" not in ev or "push" not in ev:
        return []
    push = ev["push"]
    branches = push.get("branches")
    if not branches:
        if push.get("tags") and not push.get("branches-ignore"):
            return []  # tags only
        return [f"{name}: `push` without a branch list also runs on feature branches"]
    bad = [b for b in branches if b != "main"]
    if bad:
        return [f"{name}: `push` matches {bad}, which a pull request already covers"]
    return []


def problems_concurrency(name: str, spec: dict) -> list[str]:
    ev = triggers(spec)
    conc = spec.get("concurrency")
    cancel = conc.get("cancel-in-progress") if isinstance(conc, dict) else None
    out = []
    if name in NEVER_CANCEL:
        if conc is not None and cancel is not False:
            out.append(f"{name}: publishing/scheduled workflow may cancel ({cancel!r})")
        return out
    if "hunt" in name:
        return out  # judged by problems_hunt
    if "pull_request" not in ev:
        # no pull request to replace: it must not cancel anything, anywhere.
        if cancel not in (None, False) and _flat(cancel) != CANCEL_ONLY_PULL_REQUESTS:
            out.append(f"{name}: cancels without a pull request ({cancel!r})")
        return out
    if not isinstance(conc, dict):
        return [f"{name}: runs on pull requests and has no concurrency group"]
    group = str(conc.get("group", ""))
    if _flat(cancel) != CANCEL_ONLY_PULL_REQUESTS:
        out.append(f"{name}: cancel-in-progress is {cancel!r}, not "
                   f"`github.event_name == 'pull_request'`")
    if "github.event.pull_request.number" not in group:
        out.append(f"{name}: the group is not keyed on the pull request number")
    if "github.run_id" not in group:
        out.append(f"{name}: outside a pull request the group is not unique to the run, "
                   f"so a newer pending run of main replaces an older one")
    if "github.ref" in group or "head_ref" in group:
        out.append(f"{name}: the group is keyed on the ref, which is main for every push to main")
    return out


def problems_hunt(name: str, spec: dict) -> list[str]:
    if "hunt" not in name:
        return []
    out = []
    conc = spec.get("concurrency")
    if not isinstance(conc, dict) or "hunt" not in (
            str(conc.get("group", "")) + str(spec.get("name", ""))).lower():
        out.append(f"{name}: a hunt needs a concurrency group of its own")
    for job, body in (spec.get("jobs") or {}).items():
        strategy = (body or {}).get("strategy")
        if not isinstance(strategy, dict) or "matrix" not in strategy:
            continue
        cap = strategy.get("max-parallel")
        if not isinstance(cap, int) or cap > MAX_HUNT_PARALLEL:
            out.append(f"{name}: matrix job `{job}` has max-parallel {cap!r} "
                       f"(needs an integer <= {MAX_HUNT_PARALLEL})")
    return out


def all_workflows():
    return sorted(WORKFLOWS.glob("*.yml"))


# ----------------------------------------------------- the checkers can fail

OLD_CI = """
name: ci
on:
  push:
    branches: ["**"]
  pull_request:
"""
BAD_CONCURRENCY = """
name: x
on:
  pull_request:
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
"""
GOOD_CONCURRENCY = """
name: x
on:
  push:
    branches: [main]
  pull_request:
concurrency:
  group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && format('pr-{0}', github.event.pull_request.number) || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
"""
HUNT_WITHOUT_CAP = """
name: e2e hunt
on:
  workflow_dispatch:
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
jobs:
  suite:
    strategy:
      matrix:
        include: ${{ fromJSON(needs.plan.outputs.m) }}
"""


def test_the_old_trigger_is_caught():
    assert problems_double_run("ci.yml", load(OLD_CI))
    assert problems_double_run("ci.yml", load(GOOD_CONCURRENCY)) == []


def test_a_group_keyed_on_the_ref_that_cancels_everywhere_is_caught():
    assert len(problems_concurrency("ci.yml", load(BAD_CONCURRENCY))) >= 3
    assert problems_concurrency("ci.yml", load(GOOD_CONCURRENCY)) == []


def test_a_hunt_matrix_without_a_cap_is_caught():
    assert problems_hunt("e2e-hunt.yml", load(HUNT_WITHOUT_CAP))
    capped = HUNT_WITHOUT_CAP.replace("    strategy:\n", "    strategy:\n      max-parallel: 8\n")
    assert problems_hunt("e2e-hunt.yml", load(capped)) == []
    too_many = capped.replace("max-parallel: 8", "max-parallel: 9")
    assert problems_hunt("e2e-hunt.yml", load(too_many))


# ------------------------------------------------------ the workflows here

def test_no_workflow_runs_twice_for_a_commit_of_a_pull_request():
    problems = []
    for path in all_workflows():
        problems += problems_double_run(path.name, load(path.read_text(encoding="utf-8")))
    assert not problems, "\n".join(problems)


def test_the_push_triggers_a_gate_reads_are_still_there():
    ci = triggers(load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8")))
    assert ci["push"]["branches"] == ["main"], "ci must run on every commit of main"
    assert "pull_request" in ci, "ci must run on pull requests: the `gate` context comes from there"
    publish = triggers(load((WORKFLOWS / "publish.yml").read_text(encoding="utf-8")))
    assert publish["push"]["tags"] == ["v*"], "publish must start on a release tag"


def test_the_context_the_branch_protection_requires_keeps_its_name():
    spec = load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    assert "gate" in spec["jobs"] and "name" not in spec["jobs"]["gate"]
    assert spec["name"] == "ci"


def test_every_workflow_cancels_on_pull_requests_and_nowhere_else():
    problems = []
    for path in all_workflows():
        problems += problems_concurrency(path.name, load(path.read_text(encoding="utf-8")))
    assert not problems, "\n".join(problems)


def test_a_hunt_has_its_own_group_and_caps_its_matrices():
    problems = []
    for path in all_workflows():
        problems += problems_hunt(path.name, load(path.read_text(encoding="utf-8")))
    assert not problems, "\n".join(problems)
