"""Two fixes that were made and then left unguarded.

Both were listed as still-open in `70-known-bugs.md` on 2026-07-25 and both had
in fact been fixed in the code by 2026-07-26. What had NOT happened is a test:
reverting either one left the suite fully green, 75 passed. A fix nothing holds
in place is a fix with a return date, and this project's own rule is that a
gate which has only ever printed PASS is not a gate.

  B  the editable reattach command is built through ``format_command`` so a
     path containing a space survives being pasted back. `C:\\Users\\First
     Last\\...` is an ordinary Windows path, and this printed line is the whole
     handed-over undo for the one repair that costs the user something - it
     detaches their environment from their working tree.
  C  ``_run_pip`` is bounded by a timeout. It runs from the first line of a
     consumer's package ``__init__``, so a pip that stalls stalls the IMPORT,
     and ``capture_output=True`` means there is not one character on screen to
     explain why.

Every test here is written so that reverting the fix turns it red - that was
checked by actually reverting each one, not by reading the assertion.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time

import pytest

from invisible_core import _pin

pytestmark = pytest.mark.unit

#: A directory name of the kind that is completely ordinary on Windows and
#: breaks every naive f-string that renders a command.
SPACED = r"C:\Users\First Last\src\invisible_core"


# ── B: the reattach command has to survive being pasted back ──────────────

def test_the_editable_reattach_command_tokenises_back_to_four_arguments():
    """The failure this had, measured: the printed line tokenised to SIX
    arguments and pip exited 1 with "is not a valid editable requirement",
    naming a truncated path that does not exist."""
    rendered = _pin.format_command(["pip", "install", "-e", SPACED])
    tokens = shlex.split(rendered, posix=False)
    assert len(tokens) == 4, f"{rendered!r} tokenises to {len(tokens)}: {tokens}"
    assert tokens[3].strip('"') == SPACED


def test_the_repair_message_quotes_a_spaced_editable_path():
    """Through the real code path rather than through format_command directly.

    ``pin_problem`` is what a user actually sees, so asserting on
    ``format_command`` alone would pass even if the caller stopped using it -
    which is exactly the mutation that left the suite green.
    """
    report = _pin.pin_problem(
        dist_name="invisible-playwright",
        want="18.2.0",
        have="18.1.0",
        editable_path=SPACED,
    )
    assert report is not None, "a real mismatch produced no message at all"
    assert "pip install -e" in report
    line = next(l for l in report.splitlines() if "pip install -e" in l)
    tokens = shlex.split(line.strip(), posix=False)
    assert len(tokens) == 4, f"the printed line splits into {len(tokens)}: {tokens}"
    assert tokens[3].strip('"') == SPACED


def test_repair_core_hands_back_a_reattach_command_that_survives_a_space():
    """The SECOND call site, and the one that matters most.

    ``pin_problem`` only warns; ``repair_core`` has already detached the
    environment from the user's checkout by the time it prints this, so the
    string is the entire handed-over undo. Covering only ``pin_problem`` left
    this one free: reverting it to an f-string kept the suite green, which is
    how it got here in the first place.
    """
    result = _pin.repair_core(
        dist_name="invisible-playwright", want="18.2.0",
        editable=SPACED, execute=False, out=[],
    ) if _repair_takes_editable() else None
    if result is None:
        # Signature differs; assert on the module's own construction instead,
        # which is still the real expression rather than a re-implementation.
        import ast

        src = _pin.__file__
        tree = ast.parse(open(src, encoding="utf-8").read())
        assigns = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "recovery" for t in n.targets)
        ]
        assert assigns, "no `recovery = ...` left in the module"
        rendered = ast.unparse(assigns[0].value)
        assert "format_command" in rendered, (
            "repair_core builds its reattach command by interpolation again: "
            f"{rendered}")
        return
    assert result.recovery is not None
    tokens = shlex.split(result.recovery, posix=False)
    assert len(tokens) == 4, f"{result.recovery!r} splits into {len(tokens)}"


def _repair_takes_editable() -> bool:
    import inspect

    try:
        params = inspect.signature(_pin.repair_core).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return False
    return {"editable", "execute", "out"} <= set(params)


def test_a_path_without_spaces_is_left_unquoted():
    """The quoting must be conditional, or every message grows noise and the
    common case stops looking like something to paste."""
    plain = _pin.format_command(["pip", "install", "-e", "/home/u/src/core"])
    assert plain == "pip install -e /home/u/src/core"


# ── C: a stalled pip must not stall an import ─────────────────────────────

def test_a_stalled_command_is_stopped_rather_than_waited_on(monkeypatch):
    """Drives a real subprocess that sleeps far past the bound.

    Not a mocked timeout: the thing in doubt is whether the bound is passed to
    subprocess at all, and a mock of subprocess.run would only confirm that the
    code calls the function it calls.
    """
    monkeypatch.setenv(_pin.INSTALL_TIMEOUT_ENV, "1")
    started = time.monotonic()
    outcome = _pin._run_pip(
        [sys.executable, "-c", "import time; time.sleep(30)"], execute=True)
    elapsed = time.monotonic() - started

    assert outcome.ok is False
    assert elapsed < 15, f"waited {elapsed:.1f}s on a 1s bound"
    assert "did not finish within" in outcome.detail
    assert _pin.INSTALL_TIMEOUT_ENV in outcome.detail, (
        "the message must name the knob, or the only remedy a user has is to "
        "stop using the package")


def test_the_timeout_is_a_real_bound_and_not_merely_configurable(monkeypatch):
    """A default that is effectively infinite would satisfy every other test
    here while leaving the import hangable."""
    monkeypatch.delenv(_pin.INSTALL_TIMEOUT_ENV, raising=False)
    assert 0 < _pin._install_timeout() <= 900, (
        f"default bound is {_pin._install_timeout()}s, which is not a bound")


def test_a_malformed_knob_falls_back_instead_of_disabling_the_bound(monkeypatch):
    """Read on the import path. A typo in an env var must not break somebody's
    import, and must not quietly turn the timeout off either - the second is
    the dangerous direction and the one worth pinning."""
    default = None
    monkeypatch.delenv(_pin.INSTALL_TIMEOUT_ENV, raising=False)
    default = _pin._install_timeout()
    for bad in ("", "  ", "abc", "-5", "0", "1e999x"):
        monkeypatch.setenv(_pin.INSTALL_TIMEOUT_ENV, bad)
        got = _pin._install_timeout()
        assert got == default, f"{bad!r} gave {got}, not the default {default}"


def test_a_command_that_exits_nonzero_is_reported_not_raised():
    """Nothing raises out of _run_pip: it is called from an import, where an
    exception is the failure mode being avoided."""
    outcome = _pin._run_pip([sys.executable, "-c", "raise SystemExit(3)"],
                            execute=True)
    assert outcome.ok is False
    assert "3" in outcome.detail


def test_not_executing_runs_nothing_and_still_renders_the_command():
    """The seam is used for FORMATTING as well as running; if it only fired on
    execution it would be dead in normal operation."""
    outcome = _pin._run_pip(["pip", "install", "-e", SPACED], execute=False)
    assert outcome.ok is False
    assert outcome.detail == "not executed"
    assert shlex.split(outcome.command, posix=False)[3].strip('"') == SPACED
