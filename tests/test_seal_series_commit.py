"""A seal built from the patch series names the series commit beside the engine commit.

From the series on, source_commit is the commit tools/engine.py apply builds from the
Mozilla tag and the series: the same on every machine, but on no remote, so
`git fetch` cannot bring it. series_commit is what rebuilds it, and the engine-source
tests say how when the commit is missing, instead of telling the reader to fetch
something that does not exist. A seal from before the series has no such field.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from invisible_core.seal import load_seal

from _engine_source import EngineSource

pytestmark = pytest.mark.unit

ENGINE = "549acd448d0027da63e72d485c0f4cb75b1e0c14"
SERIES = "2dbfa70" + "0" * 33


def _seal(path, **extra):
    path.write_text(json.dumps({
        "schema": 2, "tag": "firefox-54", "upstream_version": "157.0.1", "build_id": "20261005135250",
        "source_commit": ENGINE, "playwright": {"min": "1.55.0", "max": "1.61.0"}, "assets": {}, **extra,
    }, sort_keys=True), encoding="utf-8")
    return load_seal(path)


def test_a_series_seal_names_its_series_commit(tmp_path):
    assert _seal(tmp_path / "s.json", series_commit=SERIES).series_commit == SERIES


def test_a_seal_from_before_the_series_has_none(tmp_path):
    assert _seal(tmp_path / "s.json").series_commit == ""


def test_a_missing_engine_commit_is_explained_by_the_series_that_rebuilds_it(tmp_path):
    repo = tmp_path / "firefox"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    why = EngineSource(repo, ENGINE, series=SERIES).unavailable()
    assert why and "engine.py apply" in why and SERIES in why, why
    old = EngineSource(repo, ENGINE).unavailable()
    assert old and "git fetch" in old, "a seal without a series keeps the old advice"
