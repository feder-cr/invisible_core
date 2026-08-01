"""The automatic repair must never overwrite a working tree.

`_pin.repair_core` runs `pip install --force-reinstall` from the FIRST LINE of a
consumer's `__init__`. On a developer's machine that replaces their editable
checkout with the published wheel, and on 2026-07-27 it did so three times in
one session. Twice the cost was not inconvenience but a corrupted measurement:

  * three tests went red naming fixes that were sitting on disk - the suite was
    reading site-packages;
  * five mutation checks reported SURVIVED against a gate that was sound,
    because mutating the checkout could not affect what ran.

Nothing said the package under test had been swapped. That is the failure mode
worth guarding: not the install itself, but that it is silent.

The check that could have caught it already existed. `__main__` had a
three-valued detector over four independent signals which refuses on `unknown` -
and `__main__` is the CLI, forbidden from installing anything. What guarded the
command that DOES install was one file read with two answers. The careful check
protected the command that never runs; both now live in `_env` and the strong
one guards the real path.
"""
from __future__ import annotations

import io

import pytest

from invisible_core import _env, _pin

pytestmark = pytest.mark.unit


def _repair(monkeypatch, verdict, *, ran=None):
    """Drive repair_core with a fixed editability verdict and a recording pip."""
    monkeypatch.setattr(_pin, "_REPAIR_ATTEMPTED", False)
    monkeypatch.delenv(_pin.AUTOFIX_ATTEMPTED_ENV, raising=False)
    monkeypatch.delenv(_pin.AUTOFIX_ENV, raising=False)
    monkeypatch.setattr(_env, "_dist_facts", lambda _n: object())
    monkeypatch.setattr(_env, "_editable_of", lambda _f: verdict)
    calls = []
    monkeypatch.setattr(_pin, "INSTALL_RUNNER",
                        lambda cmd, execute=False: calls.append((cmd, execute))
                        or _pin.InstallOutcome(False, _pin.format_command(cmd), "stubbed"))
    if ran is not None:
        ran.append(calls)
    out = io.StringIO()
    return _pin.repair_core(dist_name="invisible-playwright", want="18.4.0",
                            core_preimported=False, stream=out), calls, out.getvalue()


def test_an_editable_install_is_refused_and_pip_is_never_run(monkeypatch):
    verdict = _env.Editability(_env.EDITABLE, r"C:\src\core",
                               "direct_url.json says dir_info.editable")
    result, calls, _ = _repair(monkeypatch, verdict)
    assert result.attempted is False
    assert calls == [], "pip ran against an editable install"
    assert "EDITABLE" in result.reason and r"C:\src\core" in result.reason
    assert "install -e" in result.reason, (
        "the refusal has to hand back the command that fixes it; a refusal with "
        "no remedy is how a gate gets switched off")


def test_an_UNKNOWN_verdict_is_also_refused(monkeypatch):
    """`safe_to_reinstall` is `state == NOT_EDITABLE`, never `state != EDITABLE`.

    Every way the old check was defeated produced an ABSENCE of evidence, and an
    absence of evidence must not read as permission to overwrite somebody's work.
    """
    verdict = _env.Editability(_env.UNKNOWN, "", "no direct_url.json, files not located")
    result, calls, _ = _repair(monkeypatch, verdict)
    assert result.attempted is False
    assert calls == []


def test_an_ordinary_index_install_is_still_repaired(monkeypatch):
    """The guard must not turn the repair off for the users it exists for: a
    broken environment from a plain `pip install` is exactly the case it fixes."""
    verdict = _env.Editability(_env.NOT_EDITABLE, "",
                               "no direct_url.json and the files live in site-packages")
    result, calls, _ = _repair(monkeypatch, verdict)
    assert calls, "the repair was refused for a normal index install"
    assert calls[0][1] is True, "the command was formatted but never executed"


def test_a_broken_probe_refuses_rather_than_assuming_it_is_safe(monkeypatch):
    """If the detector itself raises, the answer is still no.

    THIS TEST USED TO ASSERT THE OPPOSITE OF ITS OWN NAME, and could not fail.
    Its only assertion was `result is not None`, and `repair_core` returns a
    `RepairResult` on every path. It also named `_pin.RunOutcome`, which does not
    exist - the type is `InstallOutcome` - and the resulting `AttributeError` was
    swallowed by `repair_core`'s own `except Exception` around the runner, so a
    test standing on a stale API stayed green.

    The inline comment defended the permissive branch: refusing "would brick every
    environment whose metadata is merely unusual". Read on 2026-08-01 and weighed
    rather than overridden - it does not hold. Refusing does not brick anything:
    the repair declines and prints the command to run by hand, which is what every
    other refusal in this module already does. Proceeding runs
    `pip install --force-reinstall` over what may be a working tree, which is the
    incident of 2026-07-27 that corrupted three measurements, and it happened
    again during this refactor when a mutation removed the guard.

    An absence of evidence is not permission. That is the rule `_env` was written
    for and states in its own docstring, and this file's name is
    `test_editable_is_never_overwritten`.
    """
    monkeypatch.setattr(_pin, "_REPAIR_ATTEMPTED", False)
    monkeypatch.delenv(_pin.AUTOFIX_ATTEMPTED_ENV, raising=False)
    monkeypatch.delenv(_pin.AUTOFIX_ENV, raising=False)

    def boom(_n):
        raise RuntimeError("metadata unreadable")

    monkeypatch.setattr(_env, "_dist_facts", boom)
    calls = []
    monkeypatch.setattr(_pin, "INSTALL_RUNNER",
                        lambda cmd, execute=False: calls.append(cmd)
                        or _pin.InstallOutcome(False, "stubbed", ""))
    result = _pin.repair_core(dist_name="invisible-playwright", want="18.4.0",
                              core_preimported=False, stream=io.StringIO())
    assert not calls, (
        "the installer ran after the probe raised - an unreadable probe is not "
        "permission to overwrite whatever is on disk")
    assert result.attempted is False, result
    assert "could not determine" in result.reason, result.reason
    assert _pin.AUTOFIX_ENV in result.reason, (
        "a refusal must name the way out, or the user is stuck: " + result.reason)


def test_the_strong_detector_is_the_one_the_installer_uses(monkeypatch):
    """Both detectors existed; the weak one guarded the real command. This
    asserts the wiring, because the wiring is the whole fix."""
    import inspect

    src = inspect.getsource(_pin.repair_core)
    assert "_editable_of" in src and "safe_to_reinstall" in src, (
        "repair_core no longer consults the three-valued detector")
