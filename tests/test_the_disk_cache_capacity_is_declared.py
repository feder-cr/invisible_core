"""The capacity of the HTTP disk cache is declared, not read from the host's free disk space.

Without a declaration the engine sizes the disk cache with "smart size" (netwerk/cache2/CacheFileIOManager.cpp,
SmartCacheSize): a function of the free space of the disk the profile lives on, 1 GB above 25 GB free, 30% of the free
space (at least 50 MB) under 500 MB, and recomputed every 60 seconds. What the cache holds, and so how long a page's
resource is served from it, then depends on the machine: a host value the page cannot read directly, but that decides
what stays in the cache, and the same seed on two machines does not behave the same.

The core declares both prefs: smart size off, and the capacity as ONE constant (`CACHE_DISK_CAPACITY_KB`, in KB: the
unit of browser.cache.disk.capacity). Each is declared once, in the baseline (a user value is a user value: a second
declaration somewhere else would only make the first one a decision nobody can find). The type of both is checked
against the engine by tests/test_pref_types.py.
"""
from __future__ import annotations

import pytest

from invisible_core import prefs as P
from invisible_core._fpforge import generate_profile

pytestmark = pytest.mark.unit

SMART = "browser.cache.disk.smart_size.enabled"
CAPACITY = "browser.cache.disk.capacity"


def _prefs(seed=42):
    return P.translate_profile_to_prefs(generate_profile(seed), timezone="America/New_York")


def test_smart_size_is_off_in_every_session():
    for seed in (1, 42, 970411):
        assert _prefs(seed)[SMART] is False, "seed %d: the engine would size the cache from the host's free disk" % seed


def test_the_capacity_is_the_one_constant():
    for seed in (1, 42, 970411):
        value = _prefs(seed)[CAPACITY]
        assert value == P.CACHE_DISK_CAPACITY_KB
        assert type(value) is int


def test_the_constant_is_a_capacity_in_kb_that_retail_can_have():
    # 1 GB is what retail's smart size gives with more than 25 GB free; the other candidate is 100 MB.
    assert P.CACHE_DISK_CAPACITY_KB in (1048576, 102400)


def test_the_capacity_does_not_change_with_the_persona():
    assert len({_prefs(seed)[CAPACITY] for seed in range(1, 40)}) == 1


def test_both_are_declared_once_in_the_baseline():
    assert SMART in P._BASELINE and CAPACITY in P._BASELINE
    import inspect
    source = inspect.getsource(P)
    assert source.count('"%s"' % SMART) == 1, "smart size is declared in more than one place"
    assert source.count('"%s"' % CAPACITY) == 1, "the capacity is declared in more than one place"
