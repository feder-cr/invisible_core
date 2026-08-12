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


@pytest.fixture(autouse=True)
def _no_geoip_download(monkeypatch, request):
    """Stop the GeoIP database download from escaping a test that thinks it is
    hermetic.

    `resolve_session_locale` ends in `ip_to_locale(ip, ensure_geoip_mmdb())`,
    and the ARGUMENT is evaluated before the call. A test that monkeypatches
    `ip_to_locale` has therefore protected nothing: `ensure_geoip_mmdb()` still
    runs first, still reaches the network, and when it fails the exception
    lands in the `except` clause that returns `en-US`.

    Measured 2026-08-12, CI run 31644820165 on the core: one job out of eight -
    windows-latest / 3.13, while windows 3.12 and 3.14 and every ubuntu passed -
    went red on `test_a_resolved_locale_stays_quiet` with
    `assert 'en-US' == 'it-IT'` and a `RemoteDisconnected` in the captured
    stderr. The test names its expectation correctly and had monkeypatched the
    resolver; the download underneath it had not been considered. Re-running
    the same commit was green, which is the shape that makes this expensive: it
    reads as a flake, so it gets re-run rather than fixed, and a red job on a
    push people are about to trust is exactly when nobody wants to investigate.

    THREE tests reach `resolve_session_locale` and none neutralised the
    download. One of the three is worse than red - it patches `ip_to_locale` to
    raise and asserts on the warning text, and the network failure produces a
    warning that satisfies the same assertion, so it passes for the wrong
    reason either way.

    Fixed HERE and not in the three call sites, per rule 16: after the fix the
    places that know "the suite does not download the GeoIP database" must be
    one, not three, or the fourth test written next month reopens it.

    The opt-out is the EXISTING `e2e` marker, not a new one. `e2e` is declared
    as "needs something outside this process - the network, the real patched
    binary, or a display", which is this exact case, and it is already excluded
    from the default selection. A new marker was the first idea and it is
    wrong twice over: `--strict-markers` would refuse it until declared, and
    the marker block is byte-identical across the three repositories with
    `test_marker_vocabulary.py` failing when they drift - so one convenience
    here would have to be copied into two other packages that have no use
    for it.

    It STUBS rather than refuses, and that is the second decision. Raising
    makes the defect loud but hands every future test a rule to remember, which
    is the same shape as the bug. Returning a path that no hermetic test ever
    opens - the three that reach here have all monkeypatched `ip_to_locale`, so
    the value is passed and dropped - makes the suite hermetic by construction
    and needs nobody to know. A test that really wants the database is `e2e` by
    the vocabulary's own definition and gets the real function back.
    """
    if request.node.get_closest_marker("e2e"):
        return
    import invisible_core.download as _dl

    sentinella = pathlib.Path(__file__).with_name("_geoip_db_not_downloaded_in_tests.mmdb")
    monkeypatch.setattr(_dl, "ensure_geoip_mmdb", lambda *a, **kw: sentinella,
                        raising=False)
