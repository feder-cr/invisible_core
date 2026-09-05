"""Orphaned `.tmp-<tag>-<pid>` trees are swept, and only those.

A download extracts into a directory named after its pid and, when it starts
again, removes only that one. A process killed during `verifying` or
`extracting` therefore left up to the whole tree behind, and nothing ever
looked at it again. The MCP server's prefetch made the case ordinary: a client
that closes the server during the first minute of its first session kills a
download in flight.

The known-bad half matters as much as the sweep: this process's own tree, a
live process's tree, and anything not named after a pid must all survive.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from invisible_core.download import sweep_orphaned_tmp


def _tree(root: Path, name: str) -> Path:
    d = root / name
    (d / "firefox").mkdir(parents=True)
    (d / "firefox" / "omni.ja").write_bytes(b"half-extracted")
    return d


@pytest.mark.unit
def test_a_dead_pids_tree_is_removed(tmp_path):
    orphan = _tree(tmp_path, ".tmp-firefox-27-999999")

    removed = sweep_orphaned_tmp(tmp_path, alive=lambda pid: False)

    assert removed == [orphan]
    assert not orphan.exists()


@pytest.mark.unit
def test_this_processs_own_tree_is_kept_whatever_the_liveness_check_says(tmp_path):
    mine = _tree(tmp_path, ".tmp-firefox-27-%d" % os.getpid())

    assert sweep_orphaned_tmp(tmp_path, alive=lambda pid: False) == []
    assert mine.exists()


@pytest.mark.unit
def test_a_live_pids_tree_is_a_download_in_flight_and_is_kept(tmp_path):
    theirs = _tree(tmp_path, ".tmp-firefox-27-4242")

    assert sweep_orphaned_tmp(tmp_path, alive=lambda pid: pid == 4242) == []
    assert theirs.exists()


@pytest.mark.unit
def test_nothing_that_is_not_a_pid_named_tmp_tree_is_touched(tmp_path):
    engine = _tree(tmp_path, "firefox-27_151.0_20260904160921")
    odd = _tree(tmp_path, ".tmp-notapid")
    stray_file = tmp_path / ".tmp-firefox-27-777"
    stray_file.write_bytes(b"a file, not a tree")

    assert sweep_orphaned_tmp(tmp_path, alive=lambda pid: False) == []
    assert engine.exists() and odd.exists() and stray_file.exists()


@pytest.mark.unit
def test_a_missing_root_is_not_an_error(tmp_path):
    assert sweep_orphaned_tmp(tmp_path / "nowhere") == []


@pytest.mark.unit
def test_the_default_liveness_check_keeps_the_parent_process(tmp_path):
    """The default `alive` is psutil's. The parent of this test process is
    alive by construction, so its tree must survive a real check."""
    parent = _tree(tmp_path, ".tmp-firefox-27-%d" % os.getppid())

    assert sweep_orphaned_tmp(tmp_path) == []
    assert parent.exists()
