"""The pre-push lint gate checks exactly what the CI job checks.

A test file with F811 (an argument named like an imported fixture) once passed
the local pre-push hook and turned the wrapper's CI job red on a release branch: the
hook ran pytest, which does not look at that, and CI ran ruff, which does. The
hook now runs ruff too, with the rules this repository declares under
`[tool.invisible.hooks] lint` in pyproject.toml; the command itself is the
core's. What nothing else holds together is the two copies of the rule list -
the one the hook reads and the one the workflow spells - so this does.

The pyproject key is the source the hook reads, so it is the one the workflow
is compared to rather than the other way round: the hook cannot parse a
workflow, and a rule list that exists in only one of the two places is exactly
the drift this guards.
"""
from __future__ import annotations

import pathlib
import re
import shlex
import tomllib

import pytest

pytestmark = pytest.mark.unit

_REPO = pathlib.Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO / ".github" / "workflows" / "ci.yml"

#: What `invisible_core.hooks` appends after `--select <rules>`. If the core's
#: command changes, this is where this repository notices.
_THE_REST = ["--no-cache", "--output-format", "concise", "."]


def _declared() -> str:
    data = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    value = data["tool"]["invisible"]["hooks"].get("lint")
    assert value, ("[tool.invisible.hooks] declares no `lint`, so the pre-push "
                   "hook does not run the rules CI refuses on")
    return ",".join(value) if isinstance(value, list) else value.replace(" ", "")


def _ci_commands() -> list[list[str]]:
    text = _WORKFLOW.read_text(encoding="utf-8")
    return [shlex.split(m.group(0)) for m in
            re.finditer(r"python -m ruff check [^\r\n]*", text)]


def test_the_hook_lints_the_rules_the_ci_job_lints():
    if not _WORKFLOW.is_file():
        pytest.skip("an installed copy has no workflow")
    commands = _ci_commands()
    assert len(commands) == 1, (
        f"expected exactly one `ruff check` step in ci.yml, found "
        f"{len(commands)}: the gate would be compared against the wrong one")
    cmd = commands[0]
    select = cmd[cmd.index("--select") + 1]
    assert select == _declared(), (
        f"ci.yml selects {select!r} but pyproject.toml declares lint = "
        f"{_declared()!r}. The pre-push hook runs the declared one; change both "
        f"together, or a push passes the hook and fails CI.")
    rest = cmd[cmd.index("--select") + 2:]
    assert rest == _THE_REST, (
        f"ci.yml runs ruff with {rest} after --select; the hook runs "
        f"{_THE_REST}. They must be the same command.")
