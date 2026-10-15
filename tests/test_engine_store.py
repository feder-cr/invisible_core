"""The engine store and the machine's state are two roots.

An engine tree is a fact of a release, sealed and stamped when it is fetched;
geoip/ and fonts/ are this machine's state. One root held both until 47.41.0,
so a host mounting the browser read-only per release (invisible_dots) failed
its first launch on `Permission denied: .../cache/fonts`, `doctor` listed
fonts/ as an engine, and clear_cache(everything=True) removed it.
"""
from __future__ import annotations

import os
import stat
import sys

import pytest

from invisible_core import download, launch, _geoip_db

pytestmark = pytest.mark.unit


@pytest.fixture
def roots(tmp_path, monkeypatch):
    state, engines = tmp_path / "state", tmp_path / "engines"
    state.mkdir()
    engines.mkdir()
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_CACHE_DIR", str(state))
    monkeypatch.setenv(download.ENGINE_DIR_ENV, str(engines))
    return state, engines


def test_without_the_variable_the_engines_stay_where_they_were(tmp_path, monkeypatch):
    # Every warm cache in the field keeps working: nothing moves for anyone
    # who does not ask.
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv(download.ENGINE_DIR_ENV, raising=False)
    assert download.engine_root() == download.cache_root() == tmp_path


def test_engines_go_to_the_store_and_state_to_the_cache(roots):
    state, engines = roots
    assert download.cache_dir_for_seal().parent == engines
    assert download.state_dir("fonts") == state / "fonts"
    assert download.state_dir("geoip") == state / "geoip"
    assert _geoip_db._geoip_root() == state / "geoip"
    path = launch.cached_font_manifest_path("f|Arial.ttf|0\n")
    assert path.parent == state / "fonts"
    assert not any(engines.iterdir()), "a font manifest was written into the store"


def test_a_name_outside_the_state_list_is_refused():
    with pytest.raises(ValueError):
        download.state_dir("firefox-47_151.0_1")


def test_state_directories_are_never_engines_when_the_roots_coincide(tmp_path, monkeypatch):
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv(download.ENGINE_DIR_ENV, raising=False)
    for name in download.STATE_DIRS:
        (tmp_path / name).mkdir()
        (tmp_path / name / "keep").write_bytes(b"x")
    (tmp_path / "firefox-47_151.0_1").mkdir()
    listed = [d.name for d, _ in download.iter_cached_engines()]
    assert listed == ["firefox-47_151.0_1"]
    download.clear_cache(everything=True)
    for name in download.STATE_DIRS:
        assert (tmp_path / name / "keep").exists(), f"clear_cache removed {name}/"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions make a directory read-only")
def test_a_read_only_store_still_takes_the_state_writes(roots):
    state, engines = roots
    engines.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        path = launch.cached_font_manifest_path("f|Arial.ttf|0\n")
        assert path.is_file() and path.parent == state / "fonts"
    finally:
        engines.chmod(stat.S_IRWXU)
