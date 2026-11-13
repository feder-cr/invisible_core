"""GitHub is asked which GeoIP database is the latest once per interval and at most once per process, not on every call.

⛔ THE KNOWN-BAD, measured in the root legs of the realness suite (run 43211837926) and reproduced in a container: with `STEALTHFOX_GEOIP_MMDB` unset,
`ensure_geoip_mmdb` resolved the latest tag on EVERY call (a HEAD on github.com and, when that gave nothing, the API on api.github.com), and a launch calls
it about three times (the country of the exit, the zone, the coordinates): 12 names asked of the resolver per test, which a resolver trap counted as the
browser's. These tests count the requests themselves, not the helper, so a check moved elsewhere is still counted.
"""
import json
import time

import pytest

import invisible_core._geoip_db as dl

pytestmark = pytest.mark.unit
TAG = "2026.11.04"
NEWER = "2026.11.11"


class _Head:
    def __init__(self, tag):
        self.headers = {"Location": "https://github.com/x/y/releases/download/%s/geoip-aio-all.mmdb.gz" % tag}


class _Net:
    """The network the module reaches through `requests`: it counts what is asked and answers a tag, or nothing at all."""

    def __init__(self, monkeypatch, tag=TAG, online=True):
        self.heads = 0
        self.apis = 0
        self.tag = tag
        self.online = online
        monkeypatch.setattr(dl.requests, "head", self._head)
        monkeypatch.setattr(dl, "_latest_geoip_tag_api", self._api)

    def _head(self, *a, **k):
        self.heads += 1
        if not self.online:
            raise OSError("offline")
        return _Head(self.tag)

    def _api(self):
        self.apis += 1
        if not self.online:
            raise OSError("offline")
        return self.tag

    @property
    def asked(self):
        return self.heads + self.apis


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("STEALTHFOX_GEOIP_MMDB", raising=False)
    return tmp_path


def _cached(root, tag=TAG):
    d = root / "geoip" / tag
    d.mkdir(parents=True, exist_ok=True)
    f = d / dl.GEOIP_MMDB_NAME
    f.write_bytes(b"FAKE-MMDB")
    return f


def _new_process(monkeypatch):
    """What a fresh process knows: nothing of what an earlier one asked."""
    monkeypatch.setattr(dl, "_CHECKED_THIS_PROCESS", False, raising=False)


def _fake_download(monkeypatch, root):
    def fetch(tag, *, attempts):
        return _cached(root, tag)
    monkeypatch.setattr(dl, "_download_geoip_tag", fetch)


def test_three_calls_of_a_launch_ask_github_once(cache, monkeypatch):
    f = _cached(cache)
    net = _Net(monkeypatch)
    _new_process(monkeypatch)
    for _ in range(3):
        assert dl.ensure_geoip_mmdb() == f
    assert net.asked == 1, "the country, the zone and the coordinates asked %d times" % net.asked


def test_a_process_after_a_good_check_inside_the_interval_asks_nothing(cache, monkeypatch):
    f = _cached(cache)
    net = _Net(monkeypatch)
    _new_process(monkeypatch)
    dl.ensure_geoip_mmdb()
    assert net.asked == 1
    _new_process(monkeypatch)
    assert dl.ensure_geoip_mmdb() == f
    assert net.asked == 1, "a second process within the interval asked again"


def test_a_process_after_the_interval_asks_once_more(cache, monkeypatch):
    f = _cached(cache)
    net = _Net(monkeypatch)
    _new_process(monkeypatch)
    dl.ensure_geoip_mmdb()
    state = cache / "geoip" / dl._CHECK_STATE_NAME
    state.write_text(json.dumps({"checked_at": time.time() - dl.GEOIP_CHECK_INTERVAL_S - 5}))
    _new_process(monkeypatch)
    assert dl.ensure_geoip_mmdb() == f
    assert net.asked == 2


@pytest.mark.parametrize("content", ["not json", "{}", '{"checked_at": "soon"}', '{"checked_at": 99999999999}', '{"checked_at": true}',
                                     '{"checked_at": 1' + "0" * 400 + "}", '{"checked_at": 1e999}', '{"checked_at": null}', "[]", "42"])
def test_a_state_that_cannot_be_trusted_is_a_check_that_is_due(cache, monkeypatch, content):
    _cached(cache)
    (cache / "geoip" / dl._CHECK_STATE_NAME).write_text(content)
    net = _Net(monkeypatch)
    _new_process(monkeypatch)
    dl.ensure_geoip_mmdb()
    assert net.asked == 1


def test_github_not_answering_uses_the_database_there_without_an_error_and_asks_once_per_process(cache, monkeypatch):
    f = _cached(cache)
    net = _Net(monkeypatch, online=False)
    _new_process(monkeypatch)
    for _ in range(3):
        assert dl.ensure_geoip_mmdb() == f
    assert (net.heads, net.apis) == (1, 1), "one check (the permalink, then the API) for the whole process"
    assert not (cache / "geoip" / dl._CHECK_STATE_NAME).exists(), "a check that reached nobody is not kept"
    _new_process(monkeypatch)
    dl.ensure_geoip_mmdb()
    assert net.heads == 2, "the next process asks again"


def test_a_newer_tag_is_downloaded_once_and_then_nothing_is_asked(cache, monkeypatch):
    _cached(cache)
    net = _Net(monkeypatch, tag=NEWER)
    _fake_download(monkeypatch, cache)
    _new_process(monkeypatch)
    got = dl.ensure_geoip_mmdb()
    assert got.parent.name == NEWER
    assert not (cache / "geoip" / TAG).exists(), "the older tag is pruned"
    assert (cache / "geoip" / dl._CHECK_STATE_NAME).is_file(), "the state file is not a tag directory: the prune leaves it"
    _new_process(monkeypatch)
    assert dl.ensure_geoip_mmdb() == got
    assert net.asked == 1


def test_a_failed_download_with_a_cache_is_not_retried_in_the_process_nor_recorded(cache, monkeypatch):
    f = _cached(cache)
    net = _Net(monkeypatch, tag=NEWER)
    calls = []

    def boom(tag, *, attempts):
        calls.append(attempts)
        raise OSError("down")

    monkeypatch.setattr(dl, "_download_geoip_tag", boom)
    _new_process(monkeypatch)
    for _ in range(3):
        assert dl.ensure_geoip_mmdb() == f
    assert calls == [1] and net.asked == 1
    assert not (cache / "geoip" / dl._CHECK_STATE_NAME).exists()


def test_a_cold_cache_is_never_skipped(cache, monkeypatch):
    net = _Net(monkeypatch, online=False)
    _new_process(monkeypatch)
    with pytest.raises(RuntimeError):
        dl.ensure_geoip_mmdb()
    net.online = True
    _fake_download(monkeypatch, cache)
    got = dl.ensure_geoip_mmdb()  # same process, GitHub is back: nothing to fall back on, so it asks and downloads
    assert got.is_file() and got.parent.name == TAG


def test_the_state_file_is_not_a_database_and_not_a_tag(cache, monkeypatch):
    f = _cached(cache)
    _Net(monkeypatch)
    _new_process(monkeypatch)
    dl.ensure_geoip_mmdb()
    assert dl.geoip_mmdb_path() == f
    dl._prune_old_geoip_tags(TAG)
    assert (cache / "geoip" / dl._CHECK_STATE_NAME).is_file()


def _pruned_during_the_check(monkeypatch, root, keep_tag=None):
    """What another process does while this one asks GitHub: it prunes the tag directory this one listed (and, for `keep_tag`, leaves a newer one)."""
    import shutil

    def resolve():
        shutil.rmtree(root / "geoip" / TAG, ignore_errors=True)
        if keep_tag:
            _cached(root, keep_tag)
        return TAG

    monkeypatch.setattr(dl, "_resolve_latest_geoip_tag", resolve)


def test_a_cache_pruned_by_another_process_during_the_check_is_not_returned(cache, monkeypatch):
    _cached(cache)
    _pruned_during_the_check(monkeypatch, cache, keep_tag=NEWER)
    _new_process(monkeypatch)
    got = dl.ensure_geoip_mmdb()
    assert got.is_file() and got.parent.name == NEWER, "the path listed before the check had been pruned: %s" % got


def test_a_failed_download_after_a_prune_uses_what_is_there_now(cache, monkeypatch):
    _cached(cache)
    _pruned_during_the_check(monkeypatch, cache, keep_tag=NEWER)
    monkeypatch.setattr(dl, "_resolve_latest_geoip_tag", lambda: "2026.12.01")  # a tag nobody has: the download is tried
    import shutil

    def boom(tag, *, attempts):
        shutil.rmtree(cache / "geoip" / TAG, ignore_errors=True)
        _cached(cache, NEWER)
        raise OSError("down")

    monkeypatch.setattr(dl, "_download_geoip_tag", boom)
    _new_process(monkeypatch)
    got = dl.ensure_geoip_mmdb()
    assert got.is_file() and got.parent.name == NEWER


def test_a_cache_pruned_and_not_replaced_is_the_cold_cache_and_not_a_vanished_path(cache, monkeypatch):
    _cached(cache)
    _pruned_during_the_check(monkeypatch, cache)
    _new_process(monkeypatch)
    with pytest.raises(RuntimeError):
        dl.ensure_geoip_mmdb()
