"""Refuse to run the suite against a DIFFERENT copy of the package than the one
being edited.

This exists because the failure it catches is invisible and it has already cost
real work. On 2026-07-27, mid-release, `pytest` in this repo was reading
`site-packages` rather than `src/`, and it produced two separate wrong verdicts
in the same hour:

  * three tests went red claiming fixes that were sitting on disk were absent -
    they were reading the published wheel, which predated them;
  * five mutation checks all reported SURVIVED, i.e. "this gate is not a gate",
    because mutating the checkout's files could not affect what the suite read.
    The gate was fine. The measurement was not.

How it happens without anyone doing anything wrong: bumping `CORE_REVISION`
makes the consumers' `invisible-core==` pin mismatch, `enforce_core_pin` runs at
the first line of a consumer's `__init__`, its autofix defaults to ON, and it
performs a real `pip install --force-reinstall` of the PUBLISHED core over the
editable install. Any process that imports a consumer does it - a test run in
another repo, another editor window, a background job. Reinstalling editable does
not help for long, because the next consumer import repairs it straight back.

The check is cheap and it fails with the remedy. It only applies in a git
checkout: an unpacked sdist ships `src/` and `tests/` too, and there the
installed copy is legitimately the one under test.
"""
from __future__ import annotations

import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]


def pytest_configure(config):
    if not (_REPO / ".git").exists():
        return                       # not a dev checkout; nothing to compare against

    try:
        import invisible_core
    except Exception:
        # NOT importable is not this guard's business. It broke `user-install.yml`
        # on 2026-07-27 with `INTERNALERROR> ModuleNotFoundError`: that workflow
        # collects from the checkout before the package is on the path, so an
        # unconditional import here turned a guard into the thing that fails the
        # run. A gate that breaks a run it was not asked about is worse than the
        # bug it watches for.
        return

    installed = pathlib.Path(invisible_core.__file__).resolve()
    expected = (_REPO / "src" / "invisible_core").resolve()
    if expected in installed.parents:
        return

    raise pytest.UsageError(
        "\n"
        "  This suite is about to test a DIFFERENT copy of invisible_core than\n"
        "  the one in this checkout, so every result would be about the wrong\n"
        f"  files.\n\n"
        f"    importing : {installed}\n"
        f"    editing   : {expected}\n\n"
        "  Almost always this is the pin autofix: a consumer's import saw a\n"
        "  version mismatch and force-reinstalled the PUBLISHED core over the\n"
        "  editable install. Fix and keep it fixed with:\n\n"
        "    python -m pip install -e . --no-deps\n"
        "    export INVISIBLE_CORE_AUTOFIX=off\n\n"
        "  The second line matters: without it the next consumer import undoes\n"
        "  the first.")
