"""What the two consumers import from this package, asserted from inside it.

WHY. `invisible-playwright` and `invisible-firefox` pin `invisible-core==` to an
exact version, so a name that disappears here does not fail somebody's build - it
fails their IMPORT, on the machine of whoever upgrades next, with a traceback
naming a symbol rather than a version. That already happened: `IANA_TO_POSIX_TZ`
was taken by a bare module-level import ten minutes after it was written, and a
user hit it on the browser launch path within minutes of the release.

Nothing in this repository knew any of that. The consumers are separate
repositories on separate release cadences; this suite is what runs before this
package is pushed, and it had no idea which of its names were load-bearing.

MEASURED 2026-07-27, by parsing both consumers' sources: 57 names across eight
modules, and **23 of them came from modules whose leading underscore says
private** - `_pin` alone supplied 19. That is not a style complaint: a refactor
inside `_pin`, entirely reasonable to whoever reads the underscore, breaks two
published packages at import time.

Fixed the same day, in the two ways that needed no renaming spree. `_pin` became
`pin`, because nineteen load-bearing names is a public module whatever it is
called. And the consumers stopped naming `_geo`, `_headless`, `_proxy`,
`_webgl_personas`, `prefs`, `config`, `constants` and `download` at all - almost
every name they wanted was already exported by the package itself, so they ask
`invisible_core` for it. Private surface after: **6 names across 2 modules.**

WHAT THIS FILE IS AND IS NOT. It is a list of names that must keep existing,
with their kind (callable, class, constant). It is NOT a signature check: the
consumers' own suites test behaviour, and duplicating that here would be a
second acceptance set for the same code, which is the defect this pass has been
removing everywhere else.

WHEN IT GOES RED. Either put the name back, or delete its row here IN THE SAME
COMMIT as the consumer change that stops needing it - and then the consumers'
pins have to move together. A red row is the question "did you mean to break
them?" being asked before the release rather than after.

The list is READ FROM THE CONSUMERS when they are beside this checkout, so it
cannot silently fall behind; the frozen copy below is what runs everywhere else.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

pytestmark = pytest.mark.unit

_RELEASE = pathlib.Path(__file__).resolve().parents[2]
_CONSUMERS = ("invisible_playwright", "invisible_firefox")

#: Frozen 2026-07-27 from the two consumers' sources. Every name here is imported
#: at least once by shipped consumer code - not by their tests, which may reach
#: for anything.
CONTRACT = {
    "invisible_core": {
        "BINARY_VERSION", "FIREFOX_UPSTREAM_VERSION", "GeoTimezoneError",
        "IANA_TO_POSIX_TZ", "LaunchPlan", "_geo", "_headless", "_proxy",
        "_webgl_personas", "build_launch_plan", "cloak_prefs", "config",
        "configure_proxy", "constants", "download", "ensure_binary",
        "ensure_geoip_mmdb", "forced_gpu_class", "generate_profile",
        "get_default_args", "get_default_stealth_prefs",
        "make_virtual_display", "prefs", "prepare_session_geo",
        "resolve_session_locale", "resolve_session_timezone",
        "translate_profile_to_prefs", "tz_env",
    },
    "invisible_core._fpforge": {
        "Profile", "_network", "_sampler", "generate_profile", "profile",
    },
    "invisible_core.constants": {
        "BINARY_VERSION",
    },
    "invisible_core.download": {
        "cache_root", "engine_status", "ensure_binary",
        # Two rows left on 2026-08-01 with the CLI reduction that removed the
        # `clear-cache` subcommand: download.clear_cache, and __main__.main with
        # the whole module row. Neither is deleted from the core - the core's own
        # tests still cover them - they are simply no longer load-bearing for a
        # consumer, and a contract that over-claims freezes this package for
        # nobody.
        # iter_cached_engines joined the contract on 2026-08-01: the wrapper's
        # `fetch` checks every cached tree against the seal on every run, which
        # is what the removed `doctor` subcommand used to do on request.
        "iter_cached_engines",
    },
    "invisible_core.pin": {
        "AUTOFIX_ENV", "CORE_NAME", "PinDeclaration", "Requirement",
        "SKIP_ENV", "assert_core_pin", "canonical_requirement",
        "declared_core_pin", "editable_core_path", "enforce_core_pin",
        "installed_core_version", "normalise_name", "parse_requirement",
        "pin_declaration", "pin_from_requirements", "pin_problem",
        "pin_report", "recorded_core_version", "repair_core",
    },
    "invisible_core.process": {
        "JobObjectGuard", "LifetimeGuard", "NullGuard", "SessionToken",
        "TOKEN_VAR", "alive", "find_processes", "guard_for", "psutil",
        "terminate", "wait_until_gone",
    },
    "invisible_core.seal": {
        # engine_problems joined on 2026-08-01, with iter_cached_engines above:
        # the wrapper's `fetch` checks every cached tree against the seal on
        # every run and prints WHY a tree does not match, which is what the
        # removed `doctor` subcommand used to do on request.
        "EngineMismatch", "active_seal", "engine_problems", "verify_engine",
    },
}


def _imports_in(root: pathlib.Path) -> dict:
    found: dict = {}
    for path in root.rglob("*.py"):
        if "playwright-upstream" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and \
                    (node.module or "").startswith("invisible_core"):
                found.setdefault(node.module, set()).update(
                    a.name for a in node.names)
    return found


@pytest.mark.parametrize("module,names", sorted(CONTRACT.items()))
def test_every_name_a_consumer_imports_still_exists(module, names):
    mod = importlib.import_module(module)
    missing = sorted(n for n in names if not hasattr(mod, n))
    assert not missing, (
        f"{module} no longer provides {missing}, and shipped code in "
        f"invisible-playwright / invisible-firefox imports it at module level.\n"
        f"Those packages pin invisible-core to an exact version, so this is not "
        f"a build failure for them - it is an ImportError naming a symbol, on "
        f"the machine of whoever upgrades next.\n"
        f"Put it back, or delete its row from CONTRACT in the same commit as the "
        f"consumer change that stops needing it.")


def test_the_frozen_contract_still_matches_the_consumers():
    """The list cannot quietly fall behind the packages it describes.

    Skipped outside the workbench: an installed copy of this package has no
    sibling repositories to read, and the frozen list is what protects it there.
    """
    live: dict = {}
    for consumer in _CONSUMERS:
        src = _RELEASE / consumer / "src"
        if not src.is_dir():
            pytest.skip("not the workbench - the consumers are not beside this checkout")
        for mod, names in _imports_in(src).items():
            live.setdefault(mod, set()).update(names)

    new = {m: sorted(n - CONTRACT.get(m, set())) for m, n in live.items()}
    new = {m: n for m, n in new.items() if n}
    assert not new, (
        "the consumers import names this contract does not list, so nothing here "
        f"would notice if they were removed:\n  {new}\n"
        "Add them to CONTRACT.")

    gone = {m: sorted(n - live.get(m, set())) for m, n in CONTRACT.items()}
    gone = {m: n for m, n in gone.items() if n}
    assert not gone, (
        "CONTRACT lists names no consumer imports any more. Delete them - a "
        f"contract that over-claims freezes this package for nobody:\n  {gone}")


def test_the_private_modules_in_the_contract_are_named_as_such():
    """The finding, kept visible rather than filed away.

    A module with a leading underscore tells a reader of THIS package that they
    are free to change it. When a consumer imports from it, they are not.

    It was 23 names across three private modules on 2026-07-27, briefly 25 again
    on 2026-07-28 when both consumers had to be reverted to `_pin` - an exact
    pin means they may only use what the INDEX has, and the rename carried no
    version bump. Publish, move the pin, then use the name: all three happened
    and it was 6 across two.

    It is 5 across ONE since 2026-08-01. `__main__` left with the CLI reduction:
    the wrapper's `cli` used to delegate to the core's entry point and now owns
    its two commands outright. What remains is `_fpforge`, the sampler package,
    aliased wholesale by the wrapper's own back-compat shim. This test is what
    stops the number climbing back without somebody deciding to.
    """
    private = {m: len(n) for m, n in CONTRACT.items()
               if m.split(".")[-1].startswith("_")}
    assert private == {'invisible_core._fpforge': 5}, (
        f"the set of PRIVATE modules the consumers depend on changed: {private}.\n"
        "If it grew, a refactor that looks internal now breaks two published "
        "packages. If it shrank, update this test - that is progress worth "
        "recording.")
