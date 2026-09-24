"""The identity gate: every address a push carries must be a GitHub noreply one.

Real repositories in tmp_path, real commits with the identities set through the
environment, so what is asserted is what git records - not a mock of it.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from invisible_core import hooks

pytestmark = pytest.mark.integration

NOREPLY = "1+someone@users.noreply.github.com"
PRIVATE = "person@example.com"
ZERO = "0" * 40


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(extra)
    return env


def git(repo: Path, *args, author=NOREPLY, committer=NOREPLY) -> str:
    env = _clean_env(GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL=author,
                     GIT_COMMITTER_NAME="c", GIT_COMMITTER_EMAIL=committer)
    return subprocess.run(["git", "-C", str(repo), *args], env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str, **who) -> str:
    (repo / name).write_bytes(name.encode())
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name, **who)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "pyproject.toml").write_bytes(
        b'[project]\nname = "pkg"\nversion = "1.0"\n\n'
        b'[tool.invisible.hooks]\npytest = false\npin = false\nenglish = false\n')
    git(root, "add", "pyproject.toml")
    git(root, "commit", "-q", "-m", "base")
    return root


def refs_line(local_sha, remote_ref="refs/heads/main", remote_sha=ZERO):
    return f"refs/heads/main {local_sha} {remote_ref} {remote_sha}\n"


def run(root, refs, **env):
    return hooks.main(root=root, push_refs=refs, run=lambda *a, **k: 0,
                      env={"INVISIBLE_NAME_CHECK": "skip",
                           "INVISIBLE_DISCLOSURE_CHECK": "skip", **env},
                      python="PY")


def test_a_noreply_push_goes_through(repo):
    base = git(repo, "rev-parse", "HEAD")
    tip = commit(repo, "a.txt")
    assert hooks.foreign_identities(refs_line(tip, remote_sha=base), repo) == []
    assert run(repo, refs_line(tip, remote_sha=base)) == 0


def test_a_private_COMMITTER_is_refused_even_with_a_noreply_author(repo, capsys):
    """The case that reached a public main on 2026-09-03."""
    base = git(repo, "rev-parse", "HEAD")
    tip = commit(repo, "a.txt", committer=PRIVATE)
    found = hooks.foreign_identities(refs_line(tip, remote_sha=base), repo)
    assert [(role, masked) for _sha, role, masked in found] == [
        ("committer", "***@example.com")]
    assert run(repo, refs_line(tip, remote_sha=base)) == 1
    err = capsys.readouterr().err
    assert "committer" in err and "person@" not in err  # the log is pasteable


def test_a_merge_committed_by_github_itself_goes_through(repo):
    """A squash merge made through the web flow has committer
    `noreply@github.com`: 77 of the commits on this repository's own main.
    A rebase on such a main puts them in the next push's range."""
    base = git(repo, "rev-parse", "HEAD")
    tip = commit(repo, "a.txt", committer="noreply@github.com")
    assert hooks.foreign_identities(refs_line(tip, remote_sha=base), repo) == []
    assert run(repo, refs_line(tip, remote_sha=base)) == 0


def test_a_private_author_is_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    tip = commit(repo, "a.txt", author=PRIVATE)
    assert run(repo, refs_line(tip, remote_sha=base)) == 1


def test_an_offending_commit_under_a_clean_tip_is_found(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "a.txt", committer=PRIVATE)
    tip = commit(repo, "b.txt")
    found = hooks.foreign_identities(refs_line(tip, remote_sha=base), repo)
    assert len(found) == 1


def test_every_pushed_ref_is_checked_not_only_the_first(repo):
    base = git(repo, "rev-parse", "HEAD")
    clean = commit(repo, "a.txt")
    git(repo, "checkout", "-q", "-b", "side", base)
    dirty = commit(repo, "b.txt", committer=PRIVATE)
    refs = (refs_line(clean, remote_sha=base)
            + f"refs/heads/side {dirty} refs/heads/side {base}\n")
    assert run(repo, refs) == 1


def test_an_annotated_tag_with_a_private_tagger_is_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "-a", "v1", "-m", "v1", committer=PRIVATE)
    tag = git(repo, "rev-parse", "v1")
    refs = f"refs/tags/v1 {tag} refs/tags/v1 {ZERO}\n"
    found = hooks.foreign_identities(refs, repo)
    assert [role for _s, role, _m in found] == ["tagger of refs/tags/v1"]
    assert base


def test_skipping_says_so_and_identity_false_disables(repo, capsys):
    base = git(repo, "rev-parse", "HEAD")
    tip = commit(repo, "a.txt", committer=PRIVATE)
    assert run(repo, refs_line(tip, remote_sha=base),
               INVISIBLE_IDENTITY_CHECK="skip") == 0
    assert "INVISIBLE_IDENTITY_CHECK=skip" in capsys.readouterr().out
    (repo / "pyproject.toml").write_bytes(
        b'[project]\nname = "pkg"\nversion = "1.0"\n\n[tool.invisible.hooks]\n'
        b'pytest = false\npin = false\nenglish = false\nidentity = false\n')
    assert run(repo, refs_line(tip, remote_sha=base)) == 0


def test_no_refs_on_stdin_is_stated_not_passed_silently(repo, capsys):
    assert run(repo, "") == 0
    assert "identities were not checked" in capsys.readouterr().out
