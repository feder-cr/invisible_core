"""The Firefox source the PINNED engine was built from, read at its commit.

Four tests compare this package against the engine's source: the prefs the
core emits against the names the engine reads, and against the TYPE it reads
them with (`test_pref_types.py`), the font manifest against the file the build
bundles, and the juggler provenance contract against the release producer. They used to read a WORKING TREE (`STEALTH_FIREFOX_SRC`,
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

`STEALTH_FIREFOX_COMMIT` overrides the commit: any git rev of that repository
(`HEAD` included), resolved to a full sha. The engine's own repository sets it to
run these tests against the tree it just built from its patch series, which is in
no seal yet - also when it moves to a new Firefox version.

When the repository is absent or does not have the commit, the tests SKIP and say
which commit to fetch. A declared skip is not a green; a red from a tree that is
not the pinned engine's was worse, because it is the one people learn to push
past.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_REPO = "C:/ff/source"
PORT_FILE = pathlib.Path(__file__).resolve().parents[1] / "engine_port.json"


def port_declaration() -> Optional[dict]:
    """The engine a port branch serves before its seal exists, or None."""
    if not PORT_FILE.is_file():
        return None
    import json

    return json.loads(PORT_FILE.read_text(encoding="utf-8"))


class EngineSource:
    """One Firefox repository, read at one commit. Results are memoised per
    instance: a `git grep` over the engine costs seconds, and two tests ask the
    same question."""

    def __init__(self, repo: pathlib.Path, commit: str, series: str = ""):
        self.repo = pathlib.Path(repo)
        self.commit = commit
        self.series = series
        self._grep: Dict[Tuple[str, Tuple[str, ...]], Optional[str]] = {}
        self._why: Optional[str] = None
        self._checked = False

    @classmethod
    def pinned(cls) -> "EngineSource":
        """The repository named by `STEALTH_FIREFOX_SRC`, at the commit of the
        engine this branch is for: the seal's, unless `engine_port.json` names
        the engine a port branch serves before its seal exists.

        A port branch still bundles the previous release's seal, and its prefs
        were checked against that engine: the 157 branch's first 157-only pref
        (windowsLaunchOnLogin.alreadyApplied, which 151 has no reader for) was
        refused as dead by the pre-push gate on 2026-10-28, while the engine the
        branch ships with reads it. `test_engine_port.py` refuses the file once
        the seal pins the same engine."""
        from invisible_core import seal

        repo = pathlib.Path(os.environ.get("STEALTH_FIREFOX_SRC", DEFAULT_REPO))
        rev = os.environ.get("STEALTH_FIREFOX_COMMIT")
        if rev:
            return cls(repo, cls._resolve(repo, rev))
        port = port_declaration()
        if port:
            return cls(repo, port["source_commit"])
        active = seal.active_seal()
        return cls(repo, active.source_commit, series=active.series_commit)

    @staticmethod
    def _resolve(repo: pathlib.Path, rev: str) -> str:
        """`rev` as a full sha, so a message names a real commit. A rev the
        repository cannot resolve stays as written: `unavailable` then skips
        and names it."""
        try:
            out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
                                  rev + "^{commit}"], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return rev
        sha = out.stdout.decode("utf-8", errors="replace").strip()
        return sha if out.returncode == 0 and sha else rev

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
            if (probe is None or probe.returncode != 0) and self.series:
                self._why = (f"{self.repo} does not have {self.commit}, the commit the pinned "
                             f"engine was built from. It is on no remote: rebuild it from the "
                             f"series, in a checkout of firefox_antidetect_patch at {self.series}: "
                             f"python tools/engine.py apply --source {self.repo} --head sealed")
            elif probe is None or probe.returncode != 0:
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

    def lines_with(self, literals: Sequence[str],
                   globs: Sequence[str]) -> Optional[List[Tuple[str, str]]]:
        """Every line at the commit holding any of `literals` (fixed strings,
        one search for all of them), as (path, line), or None when git failed -
        the same three outcomes as `grep`, which drops the path this one
        keeps. Fixed strings and not an alternation: a regex of a few dozen
        branches over the whole tree took minutes where this takes seconds."""
        args = ["grep", "-IF"]
        for lit in literals:
            args += ["-e", lit]
        out = self._git(*args, self.commit, "--", *globs)
        if out is None or out.returncode not in (0, 1):
            return None
        prefix = self.commit + ":"
        found = []
        for line in out.stdout.decode("utf-8", errors="replace").splitlines():
            if line.startswith(prefix):
                line = line[len(prefix):]
            path, _, text = line.partition(":")
            found.append((path, text))
        return found
