"""A persistent profile is one machine: it remembers the seed it began with (B314).

The seed decides cores, screen, GPU and every sampled value, and a profile
directory keeps cookies across launches. Measured 2026-10-15: one profile,
two launches without a seed, two different machines on the same cookies.
"""
import json

import pytest

from invisible_core import resolve_session_seed
from invisible_core.session_seed import SEED_FILE

pytestmark = pytest.mark.unit


def test_without_a_profile_the_seed_is_the_callers_or_fresh():
    assert resolve_session_seed(42) == 42
    a, b = resolve_session_seed(), resolve_session_seed()
    assert 0 <= a < 2**31 and 0 <= b < 2**31


def test_a_profile_keeps_the_seed_of_its_first_launch(tmp_path):
    first = resolve_session_seed(None, tmp_path / "p")
    assert json.loads((tmp_path / "p" / SEED_FILE).read_bytes()) == {"seed": first}
    assert all(resolve_session_seed(None, tmp_path / "p") == first for _ in range(5))


def test_an_explicit_seed_on_a_new_profile_is_the_one_recorded(tmp_path):
    assert resolve_session_seed(7, tmp_path / "p") == 7
    assert resolve_session_seed(None, tmp_path / "p") == 7
    assert resolve_session_seed(7, tmp_path / "p") == 7


def test_a_different_explicit_seed_is_refused_not_obeyed(tmp_path):
    resolve_session_seed(7, tmp_path / "p")
    with pytest.raises(ValueError, match="belongs to seed 7"):
        resolve_session_seed(8, tmp_path / "p")
    assert json.loads((tmp_path / "p" / SEED_FILE).read_bytes()) == {"seed": 7}
