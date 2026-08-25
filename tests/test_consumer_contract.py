"""What the consumer imports from this package, asserted from inside it.

WHY. `invisible-playwright` pins `invisible-core==` to an exact version, so a
name that disappears here does not fail somebody's build - it fails their
IMPORT, on the machine of whoever upgrades next, with a traceback naming a
symbol rather than a version. That already happened: `IANA_TO_POSIX_TZ` was
taken by a bare module-level import ten minutes after it was written, and a
user hit it on the browser launch path within minutes of the release.

Nothing in this repository knew any of that. The consumer is a separate
repository on its own release cadence; this suite is what runs before this
package is pushed, and it had no idea which of its names were load-bearing.

Written when there were two consumers - `invisible-firefox` pinned the same
way, and everything below dated 2026-07-27 was measured against both. It was
deleted on 2026-08-18; see `_CONSUMERS` below for what that took out of the
frozen contract and why.

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

#: ONE consumer since 2026-08-18. `invisible_firefox` was deleted that day - the
#: GitHub repository is gone and so is the checkout beside this one.
#:
#: The dead name could not stay. The live cross-check below skips as soon as ONE
#: listed consumer has no `src/`, so leaving it in did not merely narrow that
#: test, it switched it off: measured on 2026-08-18, the only thing keeping
#: CONTRACT honest against the surviving wrapper was reporting a skip on the
#: workbench itself, and the frozen list could then drift with nothing to say so.
#:
#: The 13 versions of `invisible-firefox` still on PyPI are not an argument for
#: keeping it: every one of them pins `invisible-core==` to an exact version, so
#: they resolve the core they were released against and cannot be broken by a
#: name leaving this package now.
_CONSUMERS = ("invisible_playwright",)

#: Frozen 2026-07-27 from the two consumers' sources. Every name here is imported
#: at least once by shipped consumer code - not by their tests, which may reach
#: for anything.
CONTRACT = {
    "invisible_core": {
        "BINARY_VERSION", "FIREFOX_UPSTREAM_VERSION", "GeoTimezoneError",
        "IANA_TO_POSIX_TZ", "_geo", "_headless", "_proxy",
        "_webgl_personas", "config",
        "configure_proxy", "constants", "download", "ensure_binary",
        "ensure_geoip_mmdb", "forced_gpu_class",
        "get_default_args", "get_default_stealth_prefs",
        "make_virtual_display", "prefs", "prepare_session_geo",
        "resolve_session_locale", "resolve_session_timezone",
        "tz_env",
        # `LaunchPlan`, `build_launch_plan` and `generate_profile` left on
        # 2026-08-18 with the deletion of `invisible_firefox`, which was the only
        # consumer that imported them: it launched the engine DIRECTLY, so the
        # launch plan and the profile generator were its entry points, while the
        # wrapper reaches the same work through Playwright.
        #
        # Same rule as every other departure in this file, and it is the rule the
        # docstring states: a contract that over-claims freezes this package for
        # nobody. None of the three is deleted from the core, all three are still
        # exported, and the core's own suite covers `build_launch_plan`
        # (test_launch.py, test_prefs_composition.py) and `generate_profile`
        # (eleven files). `LaunchPlan` had no other mention anywhere in this
        # suite, which is why the public-export check at the bottom of this file
        # was written in the same commit rather than left as a gap.
        #
        # The 13 versions of `invisible-firefox` still on PyPI are not a reason to
        # keep the rows: each pins `invisible-core==` exactly, so it resolves the
        # core it was released against and no later change here can reach it.
        # compose_session_prefs joined on 2026-08-01 and took two rows with it.
        # The wrapper's build_prefs was the third place stacking layers on top of
        # translate_profile_to_prefs in its own order; now it asks for the one
        # composition, so it no longer names `translate_profile_to_prefs` or
        # `cloak_prefs` itself. Neither is deleted from the core - both are
        # public and exported - they are simply not load-bearing for a consumer
        # any more, and a contract that over-claims freezes this package for
        # nobody.
        "compose_session_prefs",
        # consent_region_lang joined on 2026-08-09 and DELETED a table in the
        # wrapper rather than adding one here: `_TZ_TO_REGION`, 22 IANA zones
        # mapped to a country and a language for the Google CONSENT cookie,
        # while the session locale is resolved in this package against 55
        # countries. A Romanian session said `ro-RO` in navigator.language and
        # `en+FX` in the cookie. Same ordering constraint as every new name:
        # the core has to be on the index before a wrapper release uses it.
        "consent_region_lang",
    },
    "invisible_core._fpforge": {
        "Profile", "_network", "_sampler", "generate_profile", "profile",
    },
    "invisible_core.constants": {
        # `BINARY_VERSION` left this MODULE's row on 2026-08-18 with the deletion
        # of `invisible_firefox`, which was the only consumer reaching for it
        # here. The name is unchanged and still guarded: the wrapper imports it
        # from the package, so it is still listed under `invisible_core` above,
        # and test_constants.py / test_seal_version.py cover the derivation.
        # The Windows taskbar, added 2026-08-09. The wrapper carried its own
        # _TASKBAR_H = 40 while this package declared 48 and the engine's
        # compiled floor was 48, so the default viewport was derived from one
        # number and screen.availHeight from another. The wrapper reads the
        # profile field at the use sites and imports this only to keep the old
        # name resolving to the same declaration. NOTE the ordering this
        # creates: the name is new here, so the core must be on the index
        # BEFORE a wrapper release can use it.
        "TASKBAR_PX",
        # Same story, same day, and the same ordering constraint: the wrapper's
        # _CHROME_W / _CHROME_H were module constants at 14 and 91, and the 14
        # was not merely duplicated but WRONG - stock Firefox 151 answers
        # outerWidth - innerWidth = 0. Measured 2026-08-09.
        "CHROME_W",
        "CHROME_H",
    },
    "invisible_core.launch": {
        # The font manifest handover, added 2026-08-08. The engine builds its
        # font list during app startup, before the caller's prefs exist on the
        # Juggler path, so the manifest travels as an env var pointing at a
        # content-addressed file - and it is verified against the engine's own
        # face files first, because metrics for a font that is not there do not
        # raise, they just lay the page out wrong.
        "FontManifestMismatch", "cached_font_manifest_path",
        "verify_font_manifest",
    },
    "invisible_core.download": {
        "cache_root", "ensure_binary",
        # Le tre righe qui sotto sono entrate il 2026-08-24 con
        # `invisible_playwright._node`, che procura il Node su cui gira il driver
        # biforcato: lo scarica da nodejs.org al primo uso e ne verifica il
        # checksum. Sono nomi PRIVATI, e listarli qui e' esattamente il punto:
        # un consumatore che si appoggia a un nome privato senza dichiararlo
        # crea una dipendenza che questo pacchetto puo' rompere senza
        # accorgersene, ed e' quello che il gate ha appena impedito.
        #
        # Perche' appoggiarsi a loro invece di riscriverli nel wrapper: scaricare
        # con una scadenza, sommare uno sha256 e leggere un file di checksum sono
        # gia' scritti e gia' provati qui. Averne un secondo esemplare nel wrapper
        # sarebbe lo stesso fatto in due posti - la regola 16 - e i due
        # divergerebbero al primo bug corretto da una parte sola.
        #
        # Verificato che esistano nel wheel PUBBLICATO e non solo nell'albero di
        # lavoro, perche' un consumatore puo' usare solo cio' che l'indice ha.
        "_download_file", "_parse_checksums", "_sha256_file",
        # `engine_status` left on 2026-08-18 with the deletion of
        # `invisible_firefox`, which showed the engine's state in its UI. Not
        # deleted from the core and still covered by test_doctor_fix.py,
        # test_seal_cache.py and test_seal_engine_guard.py.
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
        f"invisible-playwright imports it at module level.\n"
        f"That package pins invisible-core to an exact version, so this is not "
        f"a build failure for it - it is an ImportError naming a symbol, on "
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
            pytest.skip("not the workbench - the consumer is not beside this checkout")
        for mod, names in _imports_in(src).items():
            live.setdefault(mod, set()).update(names)

    new = {m: sorted(n - CONTRACT.get(m, set())) for m, n in live.items()}
    new = {m: n for m, n in new.items() if n}
    assert not new, (
        "the consumer imports names this contract does not list, so nothing here "
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
        f"the set of PRIVATE modules the consumer depends on changed: {private}.\n"
        "If it grew, a refactor that looks internal now breaks the published "
        "consumer package. If it shrank, update this test - that is progress "
        "worth recording.")


def test_every_name_the_package_exports_publicly_resolves():
    """`__all__` is a promise to everyone, not only to the consumers next door.

    WHY IT EXISTS, and why the date matters. Until 2026-08-18 the contract above
    was doing this job by accident: `invisible_firefox` launched the engine
    directly, so it imported `LaunchPlan`, `build_launch_plan` and
    `generate_profile`, and the CONTRACT row was the only thing in this entire
    suite that named `LaunchPlan` at all. When that repository was deleted the
    rows had to go - a contract that claims an importer who no longer exists is
    the drift this file exists to catch - and deleting them would have taken the
    only guard on a PUBLIC export with them.

    So the property moves to where it always belonged. CONTRACT answers "what do
    the neighbours import"; this answers "what does the package promise a
    stranger", which is a question about this package alone and does not change
    when a consumer comes or goes.

    It is not a formality. `from invisible_core import X` for an X in `__all__`
    that no longer resolves is an ImportError naming a symbol, on the machine of
    whoever upgrades next - the same failure mode the module docstring opens
    with, and the reason `IANA_TO_POSIX_TZ` cost a user a browser launch. A star
    import raises AttributeError at import time on the first missing name, so a
    typo in `__all__` breaks the package for everybody who ever wrote one.
    """
    import invisible_core

    exported = list(invisible_core.__all__)
    assert exported, "invisible_core.__all__ is empty, so this test guards nothing"

    duplicates = sorted({n for n in exported if exported.count(n) > 1})
    assert not duplicates, (
        f"__all__ lists {duplicates} more than once. Harmless to import and a "
        f"reliable sign the list is being appended to without being read.")

    missing = sorted(n for n in exported if not hasattr(invisible_core, n))
    assert not missing, (
        f"invisible_core.__all__ promises {missing}, which the package does not "
        f"provide.\nThat is an ImportError for `from invisible_core import "
        f"<name>` and an AttributeError for anyone who wrote `import *`.\n"
        f"Either export the name or take it out of __all__.")
