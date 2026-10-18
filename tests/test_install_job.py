"""`invisible_core.install_job`: the user-install job runs on the ledger pull
request, and waits there for the index, bounded ([B347]).

The gate reads the workflow's `on:` block as text, because the core does not
depend on a YAML parser; this file holds that reading to a real parser on
every workflow the corpus carries, so the two cannot disagree in silence.
"""
from __future__ import annotations

import pathlib

import pytest

from invisible_core import install_job

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_corpus_is_judged_as_it_says():
    """Every known-bad workflow and file refused, every good one silent."""
    for name, text in install_job.KNOWN_BAD:
        assert install_job.problems(text), name
    for name, text in install_job.MUST_NOT_FIRE:
        assert install_job.problems(text) == [], (name, install_job.problems(text))
    for name, text in install_job.KNOWN_BAD_FILES:
        assert install_job.leak_problems(text), name
    for name, text in install_job.MUST_NOT_FIRE_FILES:
        assert install_job.leak_problems(text) == [], name


def test_this_repository_runs_its_install_job_on_the_ledger_pull_request():
    """The trigger, the wait, and every test file it runs covering `LEAKS`."""
    assert install_job.judge(_ROOT) == []
    assert install_job.install_files(
        _ROOT, (_ROOT / install_job.WORKFLOW).read_text(encoding="utf-8"))


def test_a_file_that_leaves_one_variable_is_named_with_it():
    """[B349]: the wrapper's repair test ran with the caller's
    INVISIBLE_CORE_AUTOFIX=off and measured a repair that was switched off."""
    short = [name for name in install_job.LEAKS if name != "INVISIBLE_CORE_AUTOFIX"]
    [problem] = install_job.leak_problems("_LEAKS = %r\n" % (tuple(short),))
    assert "INVISIBLE_CORE_AUTOFIX" in problem and "PYTHONPATH" not in problem


def test_the_reading_of_the_triggers_agrees_with_a_yaml_parser():
    yaml = pytest.importorskip("yaml")
    workflows = [t for _, t in install_job.KNOWN_BAD + install_job.MUST_NOT_FIRE]
    workflows.append((_ROOT / install_job.WORKFLOW).read_text(encoding="utf-8"))
    for text in workflows:
        parsed = yaml.safe_load(text)
        on = parsed.get("on", parsed.get(True))   # YAML 1.1 reads a bare `on` as True
        events = set(on) if isinstance(on, (dict, list)) else {on}
        assert set(install_job.triggers(text)) == events, text
        pr = on.get("pull_request") if isinstance(on, dict) else None
        if isinstance(pr, dict):
            assert install_job._paths(install_job.triggers(text)["pull_request"]) == list(
                pr.get("paths") or []), text


def test_the_command_refuses_a_repository_without_the_job(tmp_path, capsys):
    assert install_job.main(["--root", str(tmp_path)]) == 1
    assert "REFUSED" in capsys.readouterr().out
    workflow = tmp_path / install_job.WORKFLOW
    workflow.parent.mkdir(parents=True)
    runs = "\n      - run: python -m pytest tests/test_inner_e2e.py\n"
    (tmp_path / "tests").mkdir()
    inner = tmp_path / "tests" / "test_inner_e2e.py"
    inner.write_text("_LEAKS = %r\n" % (install_job.LEAKS,), encoding="utf-8")
    workflow.write_text(install_job.KNOWN_BAD[0][1] + runs, encoding="utf-8")
    assert install_job.main(["--root", str(tmp_path)]) == 1
    workflow.write_text(install_job.MUST_NOT_FIRE[0][1] + runs, encoding="utf-8")
    assert install_job.main(["--root", str(tmp_path)]) == 0
    inner.write_text('_LEAKS = ("PYTHONPATH",)\n', encoding="utf-8")
    assert install_job.main(["--root", str(tmp_path)]) == 1
    assert "tests/test_inner_e2e.py" in capsys.readouterr().out
