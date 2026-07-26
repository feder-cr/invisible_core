"""No seed may draw a GPU that means "this machine has no GPU".

`ANGLE (Microsoft, Microsoft Basic Render Driver ...)` is the string Windows
reports when no display driver is installed - a VM or a bare server. Advertising
it as the machine's graphics card is a datacenter tell, and detectors weigh it
directly.

It was in the shipped pool at prob 0.012766 and drew on 21 of 1500 seeds. What
made 1.4% matter far more than 1.4%: **seed 42 was one of the 21**, and 42 is the
seed in the quickstart, so it is what anybody copying the documentation runs and
what any third party reproducing it benchmarks.

The evidence that it never belonged: `webgl_renderer_pool.json`, 444 rows of real
Firefox telemetry from browserforge, contains ZERO software renderers.

Fixed by REPLACING the slot in place with the pool's Intel HD persona, not by
deleting it. Deleting would have renormalised the distribution and moved every
cumulative boundary above it, changing the GPU of seeds that had nothing to do
with the bug - the same trap as removing an entry from CLEAN_RENDER_SEEDS, which
remapped 445 of 500 identities to repair 55. Measured after the change: exactly
21 of 1500 seeds moved, and they are the same 21.

The whole prefs dict was copied, not the two name strings. vendor, renderer AND
int_params all differed between the two personas, and a real GPU's name over a
rasterizer's parameter limits is a worse tell than the honest rasterizer was:
the identification service cross-checks renderer against getParameter values.

These tests exist because the bug report's own conclusion was "a pool constraint
with no test is how this got in".
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from invisible_core import get_default_stealth_prefs

pytestmark = pytest.mark.unit

_DATA = pathlib.Path(
    __import__("invisible_core").__file__).parent / "_fpforge" / "data"

#: Strings that mean "no GPU driver" on some platform. llvmpipe/softpipe are
#: Mesa's rasterizers, SwiftShader is Google's, "Microsoft Basic Render Driver"
#: is the Windows one. VMware SVGA and virgl are VM display adapters, which say
#: the same thing one level out.
_SOFTWARE = re.compile(
    r"basic render|llvmpipe|softpipe|swiftshader|software rasteriz|"
    r"mesa offscreen|generic renderer|virgl|vmware svga",
    re.I,
)

#: How many seeds to sweep. The bug sat at 1.4%, so a sweep of 100 would have
#: had a 1 in 4 chance of missing it entirely.
_SWEEP = 1500


def _pool() -> dict:
    return json.loads((_DATA / "webgl_gpu_pool.json").read_text(encoding="utf-8"))


# ── the pool itself ────────────────────────────────────────────────────────

def test_the_windows_pool_carries_no_software_renderer():
    """The `win` list is the only one anything reads - see the unreachability
    test below - so this is the one that is live."""
    bad = [
        (i, e["prefs"]["zoom.stealth.webgl.renderer"], e["prob"])
        for i, e in enumerate(_pool()["win"])
        if _SOFTWARE.search(e["prefs"]["zoom.stealth.webgl.renderer"]
                            + " " + e.get("vendor", ""))
    ]
    assert not bad, (
        f"software renderer(s) in the Windows persona pool: {bad}. Replace the "
        f"slot in place, keeping its prob - deleting it renormalises the "
        f"distribution and changes the GPU of unrelated seeds")


def test_the_pools_probabilities_still_sum_to_one():
    """The in-place repair is only safe if the mass is preserved. A slot
    replaced with a different prob would silently reweight everything."""
    for os_key in ("win", "mac", "lin"):
        total = sum(e["prob"] for e in _pool()[os_key])
        assert abs(total - 1.0) < 0.02, f"{os_key}: probabilities sum to {total}"


def test_only_the_windows_pool_is_reachable():
    """The `lin` list still carries two llvmpipe entries at a combined 9%, far
    worse than the Windows one ever was. They are unreachable today because the
    loader reads `raw.get("win", [])` and nothing else - we always claim
    Windows. This test is the tripwire: the day someone wires up per-OS pools,
    it goes red and points at those rows before they ship.
    """
    from invisible_core import _webgl_personas

    source = pathlib.Path(_webgl_personas.__file__).read_text(encoding="utf-8")
    assert 'raw.get("win"' in source, (
        "the persona loader no longer reads only the Windows pool. Before "
        "shipping per-OS personas, clean the `lin` list: it has two llvmpipe "
        "entries at a combined 9%, which is Mesa's software rasterizer and says "
        "'this machine has no GPU' just as loudly")


# ── and what a user actually gets ──────────────────────────────────────────

def test_no_seed_in_a_wide_sweep_draws_a_software_renderer():
    """Through the real entry point, not by reading the JSON.

    The pool being clean and the sampler returning something clean are two
    different claims, and only the second is what a user sees.
    """
    drawn = {}
    for seed in range(_SWEEP):
        renderer = get_default_stealth_prefs(seed=seed).get(
            "zoom.stealth.webgl.renderer", "")
        if _SOFTWARE.search(renderer):
            drawn[seed] = renderer
    assert not drawn, (
        f"{len(drawn)} of {_SWEEP} seeds draw a software renderer, e.g. "
        f"{sorted(drawn)[:5]}")


@pytest.mark.parametrize("seed", [42, 1, 0, 7, 123, 1234])
def test_the_seeds_that_appear_in_documentation_are_clean(seed):
    """42 is the one in the quickstart, and 42 was one of the 21. A pool
    constraint that only holds on average is no use to the reader who copies the
    example - they get the one draw everyone else can reproduce."""
    prefs = get_default_stealth_prefs(seed=seed)
    renderer = prefs.get("zoom.stealth.webgl.renderer", "")
    assert renderer, f"seed {seed} produced no renderer at all"
    assert not _SOFTWARE.search(renderer), (
        f"seed {seed}, which appears in documentation, draws {renderer!r}")


def test_every_seed_written_into_the_packages_own_source_is_clean():
    """Finds the example seeds instead of trusting the list above to stay
    current. A new `seed=99` in a docstring is exactly how this recurs."""
    root = pathlib.Path(__import__("invisible_core").__file__).parent
    seeds = set()
    for path in list(root.rglob("*.py")):
        if "__pycache__" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        seeds |= {int(m) for m in re.findall(r"\bseed\s*=\s*(\d{1,6})\b", text)}
    assert seeds, "no example seed found in the package source - has the pattern changed?"
    bad = {}
    for seed in sorted(seeds):
        renderer = get_default_stealth_prefs(seed=seed).get(
            "zoom.stealth.webgl.renderer", "")
        if _SOFTWARE.search(renderer):
            bad[seed] = renderer
    assert not bad, (
        f"seeds written into this package's own source draw a software "
        f"renderer: {bad}")
