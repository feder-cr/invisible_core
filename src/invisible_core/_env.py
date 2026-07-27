"""Is this distribution an EDITABLE install, and how sure are we?

WHY IT IS ITS OWN MODULE. This answer existed twice, in two strengths, and the
strong one was in the place that never acts on it:

  * `__main__.py` had a three-valued detector over four independent signals,
    refusing on `unknown` - and `__main__` is the CLI, which is forbidden from
    running pip at all (`INSTALL_RUNNER = _refuse_to_install`).
  * `_pin.py` had `editable_core_path()`: one file, `direct_url.json`, two
    values, `None` on any gap - and `_pin` is the module that actually runs
    `pip install --force-reinstall`, from the first line of a consumer's
    `__init__`.

So the careful check guarded a command that never runs and the careless one
guarded the command that overwrites a developer's working tree. That is not
hypothetical: on 2026-07-27 it fired THREE times in one session, replacing an
editable checkout with the published wheel, and twice it corrupted a
measurement rather than just being annoying - three tests went red claiming
fixes that were present on disk were missing, and five mutation checks reported
SURVIVED against a gate that was sound. The third time it blocked a push in the
middle of a release.

THE RULE
--------
`safe_to_reinstall` is `state == NOT_EDITABLE`, never `state != EDITABLE`. The
three ways the old check was defeated all produced an ABSENCE of evidence, and
an absence of evidence must not read as a licence to overwrite somebody's work.

Stdlib only. `_pin` imports this and `_pin` runs at the first line of a
consumer's import, so anything heavy here is paid by every user on every start.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from importlib import metadata


@dataclass(frozen=True)
class DistFacts:
    """Everything the doctor reads about one distribution. One seam, so the
    tests never touch the real environment.

    direct_url_raw is the FILE, kept separately from the parsed object on
    purpose: "the file is not there" and "the file is there and is garbage" are
    different findings, and collapsing them into `direct_url is None` is how the
    editable check used to conclude "not editable" from a truncated file.

    requires is the raw Requires-Dist list. The doctor does NOT parse it here:
    the `invisible-core==` question is answered by invisible_core._pin, so the
    doctor and the consumers' import-time check cannot disagree about the same
    metadata. It is kept because it is what the environment declares, and the
    tests feed the shared parser from this same field.
    """
    name: str
    present: bool
    version: str = ""
    requires: Tuple[str, ...] = ()
    direct_url: Optional[dict] = None       # parsed PEP 610 record, if it parsed
    direct_url_raw: Optional[str] = None    # the raw file; None means ABSENT
    metadata_dir: str = ""                  # the .dist-info / .egg-info directory
    module_path: str = ""                   # where the importable files resolve
    legacy_editable: Tuple[str, ...] = ()   # setup.py develop residue
    editable_finder: Tuple[str, ...] = ()   # __editable__*.pth / finder shims
    git_worktree: str = ""                  # a git checkout above module_path


def _under_site_packages(p: Path) -> bool:
    return any(part in ("site-packages", "dist-packages") for part in p.parts)


def _git_worktree_above(p: Path) -> str:
    """The nearest ancestor holding a .git entry, or "".

    Only ever consulted for files that are NOT under site-packages. A virtualenv
    created inside the project (.venv/, the common layout) puts every ordinary
    index install inside a git working tree, so on its own this signal would
    call the whole environment editable. Paired with the site-packages test it
    is what distinguishes `pip install -e .` from `pip install invisible-core`.
    """
    try:
        for d in [p, *p.parents]:
            if (d / ".git").exists():
                return str(d)
    except OSError:
        pass
    return ""


def _locate_module(d, mod: str, metadata_dir: Path) -> str:
    """Where do the files that would actually be imported live?"""
    try:
        p = Path(d.locate_file(mod))
        if p.exists():
            return str(p)
    except Exception:
        pass
    # An editable install's files are not beside the metadata: the .pth or the
    # finder shim points somewhere else, and only the import system knows where.
    try:
        import importlib.util
        spec = importlib.util.find_spec(mod)
        locs = list(getattr(spec, "submodule_search_locations", None) or [])
        if locs:
            return str(Path(locs[0]))
        if spec is not None and spec.origin:
            return str(Path(spec.origin).parent)
    except Exception:
        pass
    return ""


def _dist_facts(name: str) -> DistFacts:
    try:
        d = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return DistFacts(name, False)
    except Exception:
        return DistFacts(name, False)

    raw = None
    try:
        raw = d.read_text("direct_url.json")  # PEP 610; None when absent
    except Exception:
        raw = None
    du = None
    if raw is not None:
        try:
            parsed = json.loads(raw)
            du = parsed if isinstance(parsed, dict) else None
        except ValueError:
            du = None

    mod = re.sub(r"[-.]+", "_", name)
    meta_dir = Path(str(getattr(d, "_path", "") or ""))
    site_dir = meta_dir.parent if str(meta_dir) else None
    module_path = _locate_module(d, mod, meta_dir)

    legacy: List[str] = []
    finder: List[str] = []
    if str(meta_dir) and meta_dir.name.endswith((".egg-info", ".egg")) \
            and not _under_site_packages(meta_dir):
        # `setup.py develop` writes its .egg-info INTO the source tree and adds
        # that tree to easy-install.pth. Predates PEP 610, so it has no
        # direct_url.json at all - the exact hole this signal closes.
        legacy.append(f"legacy {meta_dir.name} outside site-packages at {meta_dir.parent}")
    if site_dir is not None:
        try:
            for link in site_dir.glob("*.egg-link"):
                if mod.lower() in link.stem.lower().replace("-", "_"):
                    legacy.append(f"{link.name} (a develop-mode link)")
            for shim in site_dir.glob("__editable__*"):
                if mod.lower() in shim.name.lower().replace("-", "_"):
                    finder.append(shim.name)
        except OSError:
            pass

    worktree = ""
    if module_path and not _under_site_packages(Path(module_path)):
        worktree = _git_worktree_above(Path(module_path))

    return DistFacts(name, True, d.version or "", tuple(d.requires or ()), du,
                     direct_url_raw=raw, metadata_dir=str(meta_dir),
                     module_path=module_path, legacy_editable=tuple(legacy),
                     editable_finder=tuple(finder), git_worktree=worktree)


EDITABLE, NOT_EDITABLE, UNKNOWN = "editable", "not-editable", "unknown"


@dataclass(frozen=True)
class Editability:
    """Three answers, not two. `unknown` is the one that used to be missing."""
    state: str
    path: str = ""
    why: str = ""
    evidence: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def safe_to_reinstall(self) -> bool:
        """Only a POSITIVE not-editable finding unlocks an install command.

        Not `state != EDITABLE`. The three ways this check was defeated all
        produced an absence of evidence, and an absence of evidence must not
        read as a licence to overwrite somebody's working tree.
        """
        return self.state == NOT_EDITABLE


def _pep610_verdict(facts: DistFacts) -> Tuple[str, str, str]:
    """(state, url, why) from PEP 610 alone. Every gap answers UNKNOWN."""
    if facts.direct_url_raw is None:
        return (UNKNOWN, "", "no direct_url.json (an index install has none, but so "
                             "does a `setup.py develop` editable)")
    if facts.direct_url is None:
        head = facts.direct_url_raw.strip()[:60].replace("\n", " ")
        return (UNKNOWN, "", f"direct_url.json is present but does not parse as a JSON "
                             f"object: {head!r}")
    du = facts.direct_url
    url = du.get("url") or ""
    if not isinstance(url, str) or not url:
        return (UNKNOWN, "", "direct_url.json declares no 'url'")
    dir_info = du.get("dir_info")
    if dir_info is None:
        # A VCS or archive install legitimately has no dir_info; anything else
        # is a record with the one field this question turns on missing.
        if "vcs_info" in du or "archive_info" in du:
            return (NOT_EDITABLE, url, "installed from a VCS or archive URL, not a "
                                       "local directory")
        return (UNKNOWN, url, "direct_url.json has no 'dir_info', and no 'vcs_info' or "
                              "'archive_info' either - the record is incomplete")
    if not isinstance(dir_info, dict):
        return (UNKNOWN, url, f"direct_url.json 'dir_info' is a "
                              f"{type(dir_info).__name__}, not an object")
    flag = dir_info.get("editable")
    if flag is True:
        return (EDITABLE, url, "direct_url.json says dir_info.editable")
    if flag in (False, None):
        return (NOT_EDITABLE, url, "installed from a local directory, dir_info.editable "
                                   "is not set")
    return (UNKNOWN, url, f"direct_url.json 'dir_info.editable' is {flag!r}")


def _editable_of(facts: DistFacts) -> Editability:
    """editable / not-editable / unknown, from four independent signals.

    Ordering is deliberate: any positive signal wins over a negative PEP 610
    record, because the ways this goes wrong in practice are all "the record
    does not know about the editable install", never "the record invented one".
    """
    if not facts.present:
        return Editability(NOT_EDITABLE, "", "no install record for this distribution")

    positive: List[str] = list(facts.legacy_editable) + [
        f"{n} in site-packages (an editable finder shim)" for n in facts.editable_finder]
    if facts.module_path and not _under_site_packages(Path(facts.module_path)):
        note = f"the importable files are outside site-packages, at {facts.module_path}"
        if facts.git_worktree:
            note += f", inside the git working tree {facts.git_worktree}"
        positive.append(note)

    state, url, why = _pep610_verdict(facts)
    path = _display_path(url) or facts.module_path

    if state == EDITABLE:
        return Editability(EDITABLE, path, why, tuple([why] + positive))
    if positive:
        return Editability(EDITABLE, path or facts.module_path,
                           "; ".join(positive), tuple(positive))
    if state == UNKNOWN:
        if facts.direct_url_raw is None and facts.module_path \
                and _under_site_packages(Path(facts.module_path)):
            # The one confident negative: no PEP 610 record AND the files that
            # would be imported are sitting in site-packages, which is what an
            # ordinary index install looks like and what no editable install
            # looks like.
            return Editability(NOT_EDITABLE, facts.module_path,
                               "no direct_url.json and the files live in site-packages")
        return Editability(UNKNOWN, path, why)
    return Editability(NOT_EDITABLE, path, why)


def _display_path(url: str) -> str:
    if not url:
        return ""
    if url.startswith("file:"):
        from urllib.parse import urlparse
        from urllib.request import url2pathname
        try:
            return url2pathname(urlparse(url).path)
        except Exception:
            return url
    return url


