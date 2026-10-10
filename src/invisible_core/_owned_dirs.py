"""Directories a process makes for one session, named so that a later process
can tell whose they were.

A session removes its own directories when it ends; one whose owner was killed
never does (B268: measured, a SIGKILLed owner left its 23 MB profile and the
cookie directory of its display behind, and nothing ever looked at them again).
A dead process cannot clean up, so the next one does - and only for what it can
attribute with certainty, which is why the name carries the pid of the process
that made it: ``<prefix><pid>_<random>``.

One home for the naming and the sweep: the profile and the browser's temporary
directory (``juggler._profile.SessionFiles``) and the Xauthority directory of a
hidden display (``_headless``) are made and swept through here. The download's
``.tmp-<tag>-<pid>`` trees under the cache root keep their own, older form in
``download.sweep_orphaned_tmp``.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Iterable, Optional

import psutil


def owned_dir(prefix: str) -> str:
    """A fresh ``<prefix><pid>_<random>`` directory in the temporary directory,
    readable by its owner only (``mkdtemp``)."""
    return tempfile.mkdtemp(prefix=f"{prefix}{os.getpid()}_")


def owner_pid(name: str, prefix: str) -> Optional[int]:
    """The pid in an owned directory's name, or None for a name that does not
    carry one: a directory from before the pid was part of the name, or
    anything else. Those are never touched."""
    if not name.startswith(prefix):
        return None
    head = name[len(prefix):].split("_", 1)[0]
    return int(head) if head.isdigit() else None


def sweep_owned_dirs(prefixes: Iterable[str], root: Optional[str] = None,
                     alive=None) -> list:
    """Remove the directories under ``prefixes`` whose owning process is gone.

    A directory whose pid is alive is left alone, whoever that process is now:
    a reused pid keeps an orphan for one more round, which is the conservative
    side. This process's own are left alone too, and a name without a pid is
    never touched. Returns what was removed; never raises.
    """
    root = root or tempfile.gettempdir()
    alive = alive or psutil.pid_exists
    prefixes = tuple(prefixes)
    try:
        names = os.listdir(root)
    except OSError:
        return []
    removed = []
    for name in names:
        pid = next((p for p in (owner_pid(name, x) for x in prefixes) if p is not None), None)
        if pid is None or pid == os.getpid() or alive(pid):
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            removed.append(path)
    return removed
