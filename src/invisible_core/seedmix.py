"""The one way a seed becomes independent streams: FNV-1a, tagged.

Every random choice the package makes from a session seed - the persona's
cookies, the pointer persona, the paths, the typing rhythm - draws from a
stream of its own, named by a tag. Two tags give two streams that advance
independently, so adding a planner later cannot shift the numbers an existing
planner produces for the same seed.

⛔ ONE COPY SINCE 38.34.0. There were three: `_cookies._sub_seed` (the
authoritative one), a byte-identical `_sub_seed` in the pointer planner, and
`_mix` in the motion generator, which masked the seed to 64 bits instead of 32
and reduced the result to int31. They lived in three packages until the
Juggler client moved here (decision D85), and the Playwright wrapper kept a
test that counted them and checked they agreed. In one package they are one
function, and the census is in `tests/test_one_seed_mixer.py`.

THE MOTION REDUCTION NEEDS NO SEED MASK OF ITS OWN, which is why `_mix` could
go without moving a single path: the low 31 bits of an FNV-1a product depend
only on the low 31 bits of its operands, so `fnv1a(seed, tag) & 0x7FFFFFFF`
is what `_mix` returned for every seed, above 2**32 and negative included. The
digest of all three over a corpus, taken before the merge, is pinned in that
test.

⛔ THESE OUTPUTS ARE SHIPPED: they are baked into the cookies and the motion
every existing seed reproduces. Changing the arithmetic, the seed mask or the
tag encoding moves every persona at once.
"""
from __future__ import annotations

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_MASK64 = 0xFFFFFFFFFFFFFFFF


def fnv1a(seed: int, tag: str) -> int:
    """The raw 64-bit FNV-1a of `tag`, started from the low 32 bits of `seed`.

    Raw, so it can be zero; `sub_seed` is the form for seeding a stream. The
    motion generator reduces it to int31 with `& 0x7FFFFFFF`.
    """
    h = _FNV_OFFSET ^ (int(seed) & 0xFFFFFFFF)
    for c in tag.encode("utf-8"):
        h ^= c
        h = (h * _FNV_PRIME) & _MASK64
    return h


def sub_seed(seed: int, tag: str) -> int:
    """The seed of the stream named `tag`: never zero, so no two tags share
    the degenerate stream a zero seed would give."""
    return fnv1a(seed, tag) or 0xDEADBEEF
