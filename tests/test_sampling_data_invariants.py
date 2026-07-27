"""The sampling data decides who every user IS. Editing it has non-obvious reach.

Two facts about this data are load-bearing and neither was held by a test:

  1. **The ORDER of a weighted pool matters as much as the weights.**
     `select_persona` walks the array accumulating `weight` until it passes a
     seed-derived point, so POSITION decides the answer. Measured on this tree:
     reversing the 14 rows of `webgl_gpu_pool.json["win"]` - changing no entry
     and no probability - gives **1409 of 1500 seeds a different GPU (93.9%)**.
     That is a larger remap than the `CLEAN_RENDER_SEEDS` incident which
     produced all the existing documentation, and every existing test passes
     through it: the mass still sums to 1, no software renderer appears, and the
     pool is still reachable.

     The trigger does not have to be a person. `json.dumps(..., sort_keys=True)`,
     a prettifier that sorts arrays, a merge tool resolving a conflict, or a
     future generator emitting rows in a different order all do it. And because
     a remapped GPU is still a VALID GPU, nothing downstream looks wrong - the
     fleet's identities simply all move at once, which is discovered the way a
     fingerprint regression is discovered, which is late.

  2. **`CLEAN_RENDER_SEEDS` is indexed modulo its length**, so length AND order
     are both significant. The length was already tested, because a length bug
     had already happened; the order was not. Reversing the same nine values
     moves ~89% of seeds. The list is sorted ascending today and contains a
     deliberate duplicate `5` (the in-place replacement of the removed `0`), so
     the two most natural tidy-ups - "dedupe" and "re-sort after inserting" -
     are respectively caught by the length assertion and, until now, not caught
     at all.

The golden digests below are the cheap, total form of both claims: they cover
every seed and every field that feeds the draw. The handful of explicit pairs
exist so that a failure is readable - a digest alone tells you something moved
but not what.

If you are here because a digest failed: that is the gate working. Do not update
the constant to make it green. Work out WHY the mapping moved, decide whether
every existing identity moving is acceptable, and only then re-pin - the same
rule the release ledger applies to published bytes.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from invisible_core._fpforge import _sampler
from invisible_core._webgl_personas import (
    CLEAN_RENDER_SEEDS,
    render_noise_seed,
    select_persona,
)

pytestmark = pytest.mark.unit

_DATA = pathlib.Path(_sampler.__file__).with_name("data")

#: Wide enough that a per-class change cannot hide. The reordering finding moved
#: 94% of seeds, but a single-slot edit moves ~1%, and 1500 is what makes that
#: visible rather than plausible noise.
_SWEEP = 1500

#: sha256 over "seed:renderer\n" for seeds 0.._SWEEP. Pinned 2026-07-27, after
#: the software rasterizer was replaced in slot 5 (invisible-core 18.3.0).
_PERSONA_MAP_DIGEST = "2ee88103fef767ca06ee1ed56ecbab80b8e3eb7af4ed0c024f0a4f0398a893cb"

#: Same shape over `render_noise_seed`, which indexes CLEAN_RENDER_SEEDS mod len.
_RENDER_SEED_DIGEST = "04b3fabf65a60a434ae7a1ddd03cfd876b47681c917b2888891dbfdf01346d2c"

#: Readable anchors, so a failure names something a human recognises. 42 is the
#: quickstart's seed and until 18.3.0 it drew the software rasterizer.
_ANCHORS = {
    0: "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    1: "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    7: "ANGLE (NVIDIA, NVIDIA GeForce 8800 GTX Direct3D11 vs_4_0 ps_4_0, D3D11)",
    42: "ANGLE (Intel, Intel(R) HD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
    123: "ANGLE (Intel, Intel(R) HD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
    1234: "ANGLE (AMD, Radeon HD 3200 Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
}

#: The canonical class list. `_sampler._GPU_CLASSES` exists and was referenced by
#: nothing; using it here is what makes it a definition rather than a comment.
_CLASSES = set(_sampler._GPU_CLASSES)


def _digest(fn) -> str:
    h = hashlib.sha256()
    for seed in range(_SWEEP):
        h.update(f"{seed}:{fn(seed)}\n".encode())
    return h.hexdigest()


# ── the whole mapping, in one value ────────────────────────────────────────

def test_the_seed_to_gpu_mapping_has_not_moved():
    got = _digest(lambda s: select_persona(s)["renderer"])
    assert got == _PERSONA_MAP_DIGEST, (
        f"the seed -> GPU mapping changed for at least one of {_SWEEP} seeds.\n"
        f"  expected {_PERSONA_MAP_DIGEST}\n  got      {got}\n"
        f"Reordering webgl_gpu_pool.json['win'] does this without touching a "
        f"single entry or probability - measured at 1409 of 1500. So does "
        f"changing a weight. If the move is intended, say how many identities "
        f"move and why that is acceptable, then re-pin.")


def test_the_seed_to_render_noise_mapping_has_not_moved():
    got = _digest(render_noise_seed)
    assert got == _RENDER_SEED_DIGEST, (
        f"render_noise_seed changed.\n  expected {_RENDER_SEED_DIGEST}\n"
        f"  got      {got}\nIt indexes CLEAN_RENDER_SEEDS modulo the list's "
        f"LENGTH, so adding, removing OR reordering a value remaps most seeds.")


@pytest.mark.parametrize("seed,renderer", sorted(_ANCHORS.items()))
def test_a_named_seed_still_draws_the_gpu_it_drew(seed, renderer):
    """The digests above catch everything; these say WHAT, in the failure."""
    assert select_persona(seed)["renderer"] == renderer


def test_the_clean_render_seed_list_is_exact_including_its_order():
    """Length was already pinned because a length bug happened. Order was not,
    and it is equally load-bearing. The duplicate 5 is deliberate: it is the
    in-place replacement of the 0 that was removed, keeping the length at 9 so
    the modulo mapping did not move."""
    assert CLEAN_RENDER_SEEDS == [5, 5, 6, 9, 11, 16, 19, 20, 28], (
        "CLEAN_RENDER_SEEDS changed. Sorting it, de-duplicating it, or "
        "inserting into it all remap ~89% of identities. The duplicate 5 is "
        "not a mistake to clean up")


# ── the CPT files, which nothing validated at all ──────────────────────────

#: The probabilities are stored rounded to four decimals, so a row legitimately
#: lands at 0.9999 or 1.0001. Measured worst case across every distribution CPT
#: on this tree: 1e-4. A tolerance of 1e-6 fails on correct data, which is how a
#: gate gets switched off; 1e-3 still catches a dropped or doubled row.
_MASS_TOLERANCE = 1e-3

#: NOT a distribution. `cpt_browsing_given_class.json` is {site: P(visited)} -
#: fifty INDEPENDENT probabilities per class, summing to about 20 because the
#: pool is tuned to land 15-30 visited sites. Asserting it sums to 1 would be
#: asserting the wrong model, so it gets its own check below.
_INDEPENDENT_PROBABILITY_TABLES = {"cpt_browsing_given_class.json"}


def _tables():
    for path in sorted(_DATA.glob("cpt_*.json")):
        if path.name in _INDEPENDENT_PROBABILITY_TABLES:
            continue
        blob = json.loads(path.read_text(encoding="utf-8"))
        table = blob.get("table") or blob.get("entries")
        if isinstance(table, dict):
            yield path.name, table


def test_every_cpt_row_is_a_probability_distribution():
    """Nothing checked this. `_network.py` reacts to a MISSING parent key by
    concatenating every table and drawing uniformly over the union, with no
    warning - so a malformed or incomplete CPT produces a plausible-looking but
    incoherent identity, forever and silently."""
    bad = []
    for name, table in _tables():
        for key, rows in table.items():
            if not isinstance(rows, list) or not rows:
                bad.append(f"{name}[{key}]: empty"); continue
            total = sum(r.get("prob", 0) for r in rows)
            if abs(total - 1.0) > _MASS_TOLERANCE:
                bad.append(f"{name}[{key}]: sums to {total!r}")
    assert not bad, "CPT rows that are not distributions:\n  " + "\n  ".join(bad)


def test_the_browsing_table_stays_a_credible_history_length():
    """Its rows are independent probabilities, so the sum IS the expected number
    of visited sites. Drift here breaks nothing loudly - it just makes every
    profile's history implausibly short or long, which is the kind of tell that
    only gets noticed from outside."""
    blob = json.loads(
        (_DATA / "cpt_browsing_given_class.json").read_text(encoding="utf-8"))
    table = blob.get("table") or blob.get("entries")
    for cls, row in table.items():
        expected = sum(row.values())
        assert 12 <= expected <= 34, (
            f"{cls}: expected visited-site count is {expected:.1f}, well outside "
            f"the 15-30 the pool is tuned for")
        assert len(row) >= 40, f"{cls}: only {len(row)} sites in the pool"


def test_every_cpt_keyed_by_gpu_class_covers_every_class():
    """A class present in the pool but absent from a CPT falls into the silent
    uniform-union path above."""
    missing = []
    for name, table in _tables():
        keys = set(table)
        if not (keys & _CLASSES):
            continue                     # keyed by something else, e.g. (class, tier)
        gap = _CLASSES - keys
        if gap:
            missing.append(f"{name}: missing {sorted(gap)}")
    assert not missing, "CPTs that do not cover every GPU class:\n  " + "\n  ".join(missing)


def test_every_class_the_pool_can_draw_is_a_declared_class():
    """Ties the pool to `_GPU_CLASSES`, which existed as a declaration that
    nothing read."""
    from invisible_core._webgl_personas import _gpu_pool

    drawn = {e["gpu_class"] for e in _gpu_pool()}
    assert drawn <= _CLASSES, (
        f"the persona pool can draw classes the sampler does not declare: "
        f"{sorted(drawn - _CLASSES)}")
