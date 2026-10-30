"""STEALTH_FIREFOX_COMMIT names the commit the engine-source tests read.

The engine's own repository runs those tests against the tree it just built from
its patch series, whose commit is in no seal and in no engine_port.json. Unset,
the commit is the port declaration's or the seal's, as before.
"""
from __future__ import annotations

import subprocess

import pytest

from _engine_source import EngineSource

pytestmark = pytest.mark.unit


def _repo(path):
    def git(*args):
        subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        *args], check=True, capture_output=True)

    path.mkdir()
    git("init", "-q")
    (path / "pref.txt").write_text("first\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "first")
    (path / "pref.txt").write_text("second\n", encoding="utf-8")
    git("commit", "-q", "-am", "second")
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD~1"], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_the_commit_env_var_is_the_commit_the_engine_is_read_at(tmp_path, monkeypatch):
    first = _repo(tmp_path / "engine")
    monkeypatch.setenv("STEALTH_FIREFOX_SRC", str(tmp_path / "engine"))
    monkeypatch.setenv("STEALTH_FIREFOX_COMMIT", "HEAD~1")
    source = EngineSource.pinned()
    assert source.commit == first, "a rev is resolved to the full sha"
    assert source.unavailable() is None
    assert source.show("pref.txt") == "first\n"


def test_head_is_read_as_the_commit_checked_out(tmp_path, monkeypatch):
    _repo(tmp_path / "engine")
    monkeypatch.setenv("STEALTH_FIREFOX_SRC", str(tmp_path / "engine"))
    monkeypatch.setenv("STEALTH_FIREFOX_COMMIT", "HEAD")
    assert EngineSource.pinned().show("pref.txt") == "second\n"


def test_a_rev_the_repository_lacks_skips_and_names_it(tmp_path, monkeypatch):
    _repo(tmp_path / "engine")
    monkeypatch.setenv("STEALTH_FIREFOX_SRC", str(tmp_path / "engine"))
    monkeypatch.setenv("STEALTH_FIREFOX_COMMIT", "no-such-rev")
    assert "no-such-rev" in EngineSource.pinned().unavailable()
