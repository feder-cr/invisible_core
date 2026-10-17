"""`python -m invisible_core.way_in`: an install instruction that is not uv is
refused, in the documents a person copies and in the messages the code prints.

The corpus is the module's own `--selftest`, which the clients' CI runs before
the gate; these tests run it here too, and pin the two perimeters on real
repositories built in a temp dir, because a perimeter that silently reads
nothing is a clean pass that checked nothing.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from invisible_core import way_in

ROOT = Path(__file__).resolve().parents[1]


def _repo(tmp_path: Path, files: dict) -> Path:
    """A git repository holding `files`, tracked: the gate reads `git ls-files`."""
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def test_the_known_bad_corpus_is_refused_and_this_repository_is_clean(capsys):
    assert way_in.selftest() == 0, capsys.readouterr().out


def test_this_repository_passes_its_own_gate_on_both_perimeters():
    findings, documents, sources = way_in.judge(ROOT)
    assert findings == [], findings
    assert documents >= 1, "no document was read, so clean says nothing"
    assert sources >= 10, f"only {sources} source files read"


def test_the_gate_skips_its_own_corpus_and_nothing_else():
    """The corpus of refused text has to contain refused text, so this module
    is the one source the second perimeter leaves out - by its own path, not
    by a pattern that could leave out more."""
    _, sources = way_in.perimeter(ROOT)
    assert "src/invisible_core/way_in.py" not in sources
    assert "src/invisible_core/installer.py" in sources
    assert "src/invisible_core/pin.py" in sources


def test_a_readme_teaching_pip_is_refused_with_its_line(tmp_path):
    root = _repo(tmp_path, {"README.md": "# x\n\n```bash\npip install x\n```\n"})
    findings, documents, _ = way_in.judge(root)
    assert findings == [("README.md", 4, "pip install x")], findings
    assert documents == 1
    assert way_in.main(["--root", str(root)]) == 1


def test_the_changelog_is_history_and_is_not_read(tmp_path):
    """What a release shipped stays written as it was."""
    root = _repo(tmp_path, {
        "README.md": "```bash\nuv add x\n```\n",
        "CHANGELOG.md": "## 0.1.0\n\nInstall with `pip install x`.\n",
    })
    findings, documents, _ = way_in.judge(root)
    assert findings == [] and documents == 1, (findings, documents)
    assert way_in.main(["--root", str(root)]) == 0


def test_an_issue_template_that_suggests_pip_is_refused(tmp_path):
    root = _repo(tmp_path, {
        "README.md": "uv add x\n",
        ".github/ISSUE_TEMPLATE/bug.yml": "placeholder: pip install x\n",
    })
    findings, _, _ = way_in.judge(root)
    assert [f[0] for f in findings] == [".github/ISSUE_TEMPLATE/bug.yml"], findings


def test_a_message_in_the_sources_is_read_and_a_docstring_is_not(tmp_path):
    root = _repo(tmp_path, {
        "README.md": "uv add x\n",
        "src/pkg/__init__.py": '"""`pip install --no-deps` leaves things out."""\n',
        "src/pkg/errors.py": 'MSG = "Run: pip install --force-reinstall x"\n',
        "tests/test_x.py": 'CMD = "pip install x"\n',
    })
    findings, _, sources = way_in.judge(root)
    assert [(f[0], f[1]) for f in findings] == [("src/pkg/errors.py", 1)], findings
    assert sources == 2, "src/ is read, tests/ is not"


def test_an_empty_perimeter_is_refused_rather_than_clean(tmp_path):
    root = _repo(tmp_path, {"src/pkg/__init__.py": "x = 1\n"})
    assert way_in.main(["--root", str(root)]) == 1
