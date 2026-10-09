"""The Firefox source the PINNED engine was built from, read at its commit.

Three tests compare this package against the engine's source: the prefs the
core emits against the names the engine reads, the font manifest against the
file the build bundles, and the juggler provenance contract against the
release producer. They used to read a WORKING TREE (`STEALTH_FIREFOX_SRC`,
default `C:/ff/source`), which answers "what is checked out there right now" -
and on the workbench that tree is shared with other sessions and sat at a commit
from before firefox-38. On 2026-10-09 the core's pre-push hook refused the
38.34.0 push over six prefs that the engine it pins reads perfectly well, because
the tree the test read had not been moved.

The question these tests ask is about the engine the seal pins, and the seal
says which commit that engine was built from (`source_commit`). So the source
is read AT THAT COMMIT, with `git grep <commit>` and `git show <commit>:<path>`:
whatever the repository has checked out, or has modified, no longer matters.
`STEALTH_FIREFOX_SRC` still names the repository, any clone that has the commit.

When the repository is absent or does not have the commit, the tests SKIP and say
which commit to fetch. A declared skip is not a green; a red from a tree that is
not the pinned engine's was worse, because it is the one people learn to push
past.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Dict, Optional, Sequence, Tuple

DEFAULT_REPO = "C:/ff/source"


class EngineSource:
    """One Firefox repository, read at one commit. Results are memoised per
    instance: a `git grep` over the engine costs seconds, and two tests ask the
    same question."""

    def __init__(self, repo: pathlib.Path, commit: str):
        self.repo = pathlib.Path(repo)
        self.commit = commit
        self._grep: Dict[Tuple[str, Tuple[str, ...]], Optional[str]] = {}
        self._why: Optional[str] = None
        self._checked = False

    @classmethod
    def pinned(cls) -> "EngineSource":
        """The repository named by `STEALTH_FIREFOX_SRC`, at the seal's commit."""
        from invisible_core import seal

        repo = pathlib.Path(os.environ.get("STEALTH_FIREFOX_SRC", DEFAULT_REPO))
        return cls(repo, seal.active_seal().source_commit)

    def _git(self, *args: str, timeout: int = 600) -> Optional[subprocess.CompletedProcess]:
        try:
            return subprocess.run(["git", "-C", str(self.repo), *args],
                                  capture_output=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            return None

    def unavailable(self) -> Optional[str]:
        """None when the commit can be read; otherwise why not, as a skip reason."""
        if self._checked:
            return self._why
        self._checked = True
        if not self.commit:
            self._why = "the active seal records no source_commit"
        elif not self.repo.is_dir():
            self._why = (f"no Firefox repository at {self.repo}; set "
                         f"STEALTH_FIREFOX_SRC to a clone that has {self.commit}")
        else:
            probe = self._git("cat-file", "-e", self.commit + "^{commit}", timeout=60)
            if probe is None or probe.returncode != 0:
                self._why = (f"{self.repo} does not have {self.commit}, the commit "
                             f"the pinned engine was built from: git fetch it")
        return self._why

    def show(self, path: str) -> Optional[str]:
        """The file at the commit, or None if it is not there."""
        out = self._git("show", f"{self.commit}:{path}", timeout=60)
        if out is None or out.returncode != 0:
            return None
        return out.stdout.decode("utf-8", errors="replace")

    def grep(self, pattern: str, globs: Sequence[str]) -> Optional[str]:
        """Every match of `pattern` (extended regex) at the commit, one per
        line, without file names. None when git failed - never an empty string
        standing in for a failure."""
        key = (pattern, tuple(globs))
        if key not in self._grep:
            out = self._git("grep", "-hoIE", pattern, self.commit, "--", *globs)
            # git grep exits 1 for "no matches", which is an answer; anything
            # else is a failure to answer.
            if out is None or out.returncode not in (0, 1):
                self._grep[key] = None
            else:
                self._grep[key] = out.stdout.decode("utf-8", errors="replace")
        return self._grep[key]
