"""A cached GeoIP database is complete or absent, never half-written [B320].

`_download_geoip_tag` used to extract the archive straight into the tag
directory, so while one process was still writing the mmdb another saw it
exist, opened half of it, and every timezone it resolved failed with "Is this a
valid MaxMind DB file?". Measured on the wrapper's main on 2026-10-16: a
four-worker e2e on a cold cache, 66 errors, all of them that one. These tests
use the real reader on a real database (`_tiny_mmdb`), because a fake reader is
blind to exactly what a truncated file does to it.
"""
from __future__ import annotations

import threading
import time
import zipfile
from pathlib import Path

import pytest

import invisible_core._geoip_db as dl
from _tiny_mmdb import tiny_mmdb

TAG = "2026.10.14"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("STEALTHFOX_GEOIP_MMDB", raising=False)
    return tmp_path


def _serve(monkeypatch, payload: bytes):
    """`_download_file` writes a zip holding `payload` as the mmdb."""
    def _download(url, dst, *a, **kw):
        with zipfile.ZipFile(dst, "w") as z:
            z.writestr(dl.GEOIP_MMDB_NAME, payload)
    monkeypatch.setattr(dl, "_download_file", _download)


@pytest.mark.unit
def test_a_good_download_lands_complete_and_readable(cache, monkeypatch):
    _serve(monkeypatch, tiny_mmdb())
    path = dl._download_geoip_tag(TAG, attempts=1)
    assert path == dl._geoip_root() / TAG / dl.GEOIP_MMDB_NAME
    assert path.read_bytes() == tiny_mmdb()
    assert dl._cached_geoip_mmdb() == path
    # Nothing staged is left behind, in the state root or beside the tags.
    assert sorted(p.name for p in cache.iterdir()) == ["geoip"]


@pytest.mark.unit
def test_a_database_the_reader_refuses_never_lands(cache, monkeypatch):
    _serve(monkeypatch, tiny_mmdb()[:40])  # truncated: what a half-written file is
    with pytest.raises(RuntimeError, match="cannot be read"):
        dl._download_geoip_tag(TAG, attempts=1)
    assert not (dl._geoip_root() / TAG).exists()
    assert dl._cached_geoip_mmdb() is None
    assert sorted(p.name for p in cache.iterdir()) == ["geoip"]


@pytest.mark.unit
def test_while_it_is_being_written_no_reader_can_see_it(cache, monkeypatch):
    """The race itself: a reader looking at the cache in the middle of the
    extraction finds nothing, not a file still being written."""
    _serve(monkeypatch, tiny_mmdb())
    seen = []
    real_extract = dl._extract

    def _extract(archive, dst):
        real_extract(archive, dst)
        seen.append((dl._cached_geoip_mmdb(), (dl._geoip_root() / TAG).exists()))
    monkeypatch.setattr(dl, "_extract", _extract)

    dl._download_geoip_tag(TAG, attempts=1)
    assert seen == [(None, False)], seen


@pytest.mark.unit
def test_two_processes_fetching_the_same_tag_end_on_one_complete_copy(cache, monkeypatch):
    """The loser of the final rename uses the winner's copy instead of failing,
    and neither ever hands out a file the reader cannot open."""
    _serve(monkeypatch, tiny_mmdb())
    real_extract = dl._extract
    both_extracting = threading.Barrier(2)

    def _extract(archive, dst):
        real_extract(archive, dst)
        both_extracting.wait(timeout=10)  # both are staged before either moves
    monkeypatch.setattr(dl, "_extract", _extract)

    results, errors = [], []

    def _fetch():
        try:
            results.append(dl._download_geoip_tag(TAG, attempts=1))
        except Exception as exc:  # collected, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=_fetch) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert len(results) == 2 and results[0] == results[1]
    assert results[0].read_bytes() == tiny_mmdb()
    assert sorted(p.name for p in cache.iterdir()) == ["geoip"]
    assert sorted(p.name for p in dl._geoip_root().iterdir()) == [TAG]
