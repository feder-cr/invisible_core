"""A persistent profile is one machine: it remembers the seed it began with (B314).

The seed decides cores, screen, GPU and every sampled value, and a profile
directory keeps cookies across launches. Measured 2026-10-15: one profile,
two launches without a seed, two different machines on the same cookies.
"""
import json

import pytest

from invisible_core import record_session_seed, resolve_session_seed
from invisible_core.session_seed import SEED_FILE

pytestmark = pytest.mark.unit


def test_without_a_profile_the_seed_is_the_callers_or_fresh():
    assert resolve_session_seed(42) == 42
    assert 0 <= resolve_session_seed() < 2**31


def test_resolving_never_touches_the_disk(tmp_path):
    # A client resolves while building, before the directory may exist.
    resolve_session_seed(None, tmp_path / "typo")
    assert not (tmp_path / "typo").exists()


def test_a_profile_keeps_the_seed_of_its_first_launch(tmp_path):
    p = tmp_path / "p"
    first = resolve_session_seed(None, p)
    record_session_seed(p, first)
    assert json.loads((p / SEED_FILE).read_bytes()) == {"seed": first}
    assert all(resolve_session_seed(None, p) == first for _ in range(5))


def test_an_explicit_seed_on_a_new_profile_is_the_one_recorded(tmp_path):
    p = tmp_path / "p"
    record_session_seed(p, resolve_session_seed(7, p))
    assert resolve_session_seed(None, p) == 7
    assert resolve_session_seed(7, p) == 7


def test_a_different_explicit_seed_is_refused_not_obeyed(tmp_path):
    p = tmp_path / "p"
    record_session_seed(p, 7)
    with pytest.raises(ValueError, match="belongs to seed 7"):
        resolve_session_seed(8, p)
    with pytest.raises(ValueError, match="belongs to seed 7"):
        record_session_seed(p, 8)
    assert json.loads((p / SEED_FILE).read_bytes()) == {"seed": 7}
