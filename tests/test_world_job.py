"""`invisible_core.world_job`: a repository's default selection excludes e2e
and runs under the world plugin, judged on its own tree ([B345])."""
from __future__ import annotations

import pathlib

import pytest

from invisible_core import world_job

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_corpus_is_judged_as_it_says():
    for name, text in world_job.KNOWN_BAD_CONFTESTS:
        assert world_job.conftest_problems(text), name
    assert world_job.conftest_problems(None)
    for name, text in world_job.MUST_NOT_FIRE_CONFTESTS:
        assert world_job.conftest_problems(text) == [], name
    for name, text in world_job.KNOWN_BAD_PYPROJECTS:
        assert world_job.addopts_problems(text), name


def test_this_repository_runs_its_default_selection_under_the_plugin():
    assert world_job.judge(_ROOT) == []


def test_a_tree_whose_conftest_does_not_load_it_is_refused(tmp_path):
    """The shape of a client that pins this core and has not adopted it."""
    (tmp_path / "pyproject.toml").write_bytes((_ROOT / "pyproject.toml").read_bytes())
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text("import os\n", encoding="utf-8")
    [(where, what)] = world_job.judge(tmp_path)
    assert where == "tests/conftest.py" and world_job.PLUGIN in what


def test_the_command_says_refused_and_exits_1(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_bytes((_ROOT / "pyproject.toml").read_bytes())
    assert world_job.main(["--root", str(tmp_path)]) == 1
    assert "REFUSED" in capsys.readouterr().out
