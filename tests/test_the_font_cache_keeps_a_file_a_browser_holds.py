"""The cached font manifest survives a second session that starts while the first one's browser holds the file (no browser).

Windows e2e of 13/11 (oss-invisible/playwright run 43373058301, e2e windows part 4): 16 launches refused with
PermissionError [WinError 5] at os.replace in launch.cached_font_manifest_path. Two sessions start together on a fresh cache;
the first writes bundle-fonts-<hash>.list and its browser opens it; the second one's rename over it is refused, because
Windows does not replace a file another process holds open without FILE_SHARE_DELETE. The file is addressed by the hash of its
own bytes, so the one there is the same and is kept. Known-bad: the old function raises the PermissionError.
"""
from __future__ import annotations

import pytest

from invisible_core import launch

pytestmark = pytest.mark.unit

MANIFEST = "f|Arial.ttf|0\n"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_CACHE_DIR", str(tmp_path / "state"))
    return tmp_path


def _held_by_another_session(content: bytes):
    """os.replace as Windows answers when another session wrote `content` first and its browser holds the file."""
    def replace(src, dst):
        with open(dst, "wb") as fh:
            fh.write(content)
        raise PermissionError(13, "Access is denied", str(src), 5, str(dst))
    return replace


def test_the_same_file_held_by_another_session_is_kept(cache, monkeypatch):
    monkeypatch.setattr(launch.os, "replace", _held_by_another_session(MANIFEST.encode("ascii")))
    out = launch.cached_font_manifest_path(MANIFEST)
    assert out.read_bytes() == MANIFEST.encode("ascii")
    assert not list(out.parent.glob("*.tmp")), "the temporary file of the refused rename is left behind"


def test_a_different_file_in_the_way_is_still_an_error(cache, monkeypatch):
    monkeypatch.setattr(launch.os, "replace", _held_by_another_session(b"something else\n"))
    with pytest.raises(PermissionError):
        launch.cached_font_manifest_path(MANIFEST)
