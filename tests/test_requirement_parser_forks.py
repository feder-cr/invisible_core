"""Three implementations read `invisible-core==X.Y.Z`. This keeps them agreeing.

WHERE THEY ARE.

  * `invisible_core.pin.parse_requirement` + `normalise_name` - the real one, run
    at import time in every consumer (both of them when this was written -
    invisible_playwright and invisible_firefox, the latter deleted
    2026-08-18);
  * `scripts/sync_core_pin.py` in the workbench - its own `normalise()` and its
    own regex;
  * `core-on-index.yml` in the wrapper repo - an inline `norm()` and `PIN_RE`,
    written into the workflow body.

WHY THREE. Neither copy can import the first one. `sync_core_pin.py` compares a
core CHECKOUT against the consumers and deliberately reads `_version.py` by path
rather than importing an installed core, because the installed one is exactly what
might be stale. `core-on-index.yml` exists to answer "is the core on the index
yet?" and runs BEFORE anything is installed - importing the package it is probing
for would be circular.

So three copies is the right answer, and the missing part was that nothing
compared them. Recorded as open item 8 in `18-gate-inventory.md`, "two remaining
forks of the requirement parser", and as §F's recurring shape: a duplicated rule
agrees on the day it is written.

WHAT DISAGREEMENT COSTS. All three answer the same question about the same line of
the same `pyproject.toml`. If the workflow's regex stops matching a shape the
consumer's runtime accepts, CI reports "no pin found" and refuses to run on a repo
that is correctly pinned. If it matches a shape the runtime rejects, CI goes green
on a pin that will fail at import on a user's machine.

The two copies are read as TEXT and their functions exec'd in an isolated
namespace - that is the point, since the whole reason they exist is that they
cannot be imported.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from invisible_core import pin

pytestmark = pytest.mark.unit

_RELEASE = pathlib.Path(__file__).resolve().parents[2]
_WORKBENCH = _RELEASE.parent
_SYNC = _WORKBENCH / "scripts" / "sync_core_pin.py"
_WORKFLOW = _RELEASE / "invisible_playwright" / ".github" / "workflows" / "core-on-index.yml"

#: The shapes a real `[project].dependencies` entry can take. Every one of these
#: has appeared in one of the three repos or in a PEP the resolver implements.
_NAMES = [
    "invisible-core", "invisible_core", "Invisible.Core", "INVISIBLE-CORE",
    "invisible--core", "invisible.core", "invisible_playwright", "requests",
]

_REQUIREMENTS = [
    "invisible-core==18.8.0",
    "invisible_core==18.8.0",
    "invisible-core == 18.8.0",
    "invisible-core[extra]==18.8.0",
    "invisible-core==18.8.0 ; python_version >= '3.10'",
    "invisible-core>=18.0.0",
    "invisible-core",
    "requests==2.31.0",
    "invisible-core @ git+https://example.invalid/x.git",
]


def _exec_block(text: str, names) -> dict:
    """Compile just the requested top-level defs/assignments out of a source text.

    Not `exec(whole_file)`: both sources do real work at import (one talks to an
    index, the other reads a checkout). Only the statements that DEFINE the names
    asked for are executed, in a namespace holding nothing but `re`.
    """
    import ast

    tree = ast.parse(text)
    wanted, keep = set(names), []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            keep.append(node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in wanted:
                    keep.append(node)
    ns: dict = {"re": re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "<extracted>", "exec"), ns)
    missing = sorted(wanted - set(ns))
    assert not missing, (
        f"could not extract {missing} from the source. Either it was renamed - "
        f"in which case this comparison has stopped covering it and must be "
        f"updated, not deleted - or it stopped being a top-level definition")
    return ns


def _inline_python(workflow_text: str) -> str:
    """The `python - <<'PY' ... PY` heredoc body out of the workflow."""
    m = re.search(r"python - <<'PY'\n(.*?)\n\s*PY\n", workflow_text, re.DOTALL)
    assert m, "the workflow no longer contains a `python - <<'PY'` block"
    body = m.group(1)
    # The block is indented to sit inside the YAML `run: |` scalar.
    indent = min((len(l) - len(l.lstrip()) for l in body.splitlines() if l.strip()),
                 default=0)
    return "\n".join(l[indent:] if len(l) >= indent else l for l in body.splitlines())


@pytest.mark.skipif(not _SYNC.is_file(), reason="not the workbench")
def test_the_sync_script_normalises_names_exactly_as_the_package_does():
    ns = _exec_block(_SYNC.read_text(encoding="utf-8"), ["normalise"])
    differing = {n: (pin.normalise_name(n), ns["normalise"](n))
                 for n in _NAMES if pin.normalise_name(n) != ns["normalise"](n)}
    assert not differing, (
        "sync_core_pin.normalise disagrees with invisible_core.pin.normalise_name:"
        "\n  " + "\n  ".join(f"{n}: package={a!r} script={b!r}"
                             for n, (a, b) in differing.items()))


@pytest.mark.skipif(not _WORKFLOW.is_file(), reason="not the workbench")
def test_the_workflows_inline_parser_agrees_with_the_package():
    ns = _exec_block(_inline_python(_WORKFLOW.read_text(encoding="utf-8")),
                     ["norm", "PIN_RE"])

    differing = {n: (pin.normalise_name(n), ns["norm"](n))
                 for n in _NAMES if pin.normalise_name(n) != ns["norm"](n)}
    assert not differing, (
        "core-on-index.yml's norm() disagrees with the package:\n  " +
        "\n  ".join(f"{n}: package={a!r} workflow={b!r}"
                    for n, (a, b) in differing.items()))

    # And the specifier half: for each shape, does the workflow see the same
    # "this is invisible-core pinned to exactly V" that the package sees?
    disagree = []
    for req in _REQUIREMENTS:
        parsed = pin.parse_requirement(req)
        ours = None
        if parsed is not None and pin.normalise_name(parsed.name) == "invisible-core":
            spec = getattr(parsed, "specifier", "") or ""
            if spec.startswith("==") and "," not in spec:
                ours = spec[2:]
        m = ns["PIN_RE"].match(req)
        theirs = (m.group("version")
                  if m and ns["norm"](m.group("name")) == "invisible-core" else None)
        if ours != theirs:
            disagree.append(f"{req!r}: package={ours!r} workflow={theirs!r}")
    assert not disagree, (
        "the workflow and the package read different pins out of the same "
        "requirement strings:\n  " + "\n  ".join(disagree) +
        "\n\nA shape the workflow misses stops CI on a correctly pinned repo; a "
        "shape it wrongly accepts lets CI go green on a pin that fails at import.")


@pytest.mark.skipif(not _WORKFLOW.is_file(), reason="not the workbench")
def test_the_comparison_is_not_vacuous():
    """Its known-bad input, twice over.

    If `_exec_block` returned an empty namespace, or if `_REQUIREMENTS` held no
    shape that actually pins the core, both tests above would pass over nothing -
    which is the empty-set failure this codebase keeps finding in its own gates.
    """
    ns = _exec_block(_inline_python(_WORKFLOW.read_text(encoding="utf-8")),
                     ["norm", "PIN_RE"])
    assert ns["norm"]("Invisible_Core") == "invisible-core"
    assert ns["PIN_RE"].match("invisible-core==18.8.0").group("version") == "18.8.0"

    pinned = [r for r in _REQUIREMENTS
              if (p := pin.parse_requirement(r)) is not None
              and pin.normalise_name(p.name) == "invisible-core"
              and (getattr(p, "specifier", "") or "").startswith("==")]
    assert len(pinned) >= 4, (
        f"only {len(pinned)} of the shapes tried are actually an exact core pin, "
        f"so the specifier comparison is barely exercised: {pinned}")
