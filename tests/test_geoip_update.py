"""Unit tests for the geoip mmdb auto-update in `download.py`.

daijro/geoip-all-in-one rebuilds weekly and keeps only the latest ~2 releases,
so `ensure_geoip_mmdb` never pins a tag: on every call it resolves the CURRENT
latest tag (from the `releases/latest/download` permalink, no GitHub API) and
downloads it only when it differs from the cache. These tests mock the cache
root, the tag resolver, and the per-tag download so nothing touches the network.
"""
# MOVED FROM invisible_playwright/tests/ ON 2026-07-27.
#
# Every test in this file exercises code in THIS package and reached it through
# a four-line back-compat shim in the wrapper. That is not where coverage for a
# module belongs, and it was not academic: measured on 2026-07-27, six realistic
# one-line breaks in core code SURVIVED the core's own suite and were caught
# only by the wrapper's - `cloak_prefs()` returning {}, SOCKS detection always
# False, the scheme never stripped from a proxy server, `_proxy_is_set` always
# True, the locale always en-US, `get_default_args()` injecting -headless. The
# core's pre-push gate and its publish gate were both green over all six.
#
# `test_no_test_reaches_the_core_through_a_shim` in the wrapper keeps them here.
import pytest

# The geoip half of `download` became `_geoip_db` on 2026-07-27: two subjects
# in one file, sharing nothing but "fetch a GitHub release asset". The two
# public names are still importable from `download` - that path is public
# through the wrapper's shim - but the code, and therefore what a test
# patches, lives in one place now.
import invisible_core._geoip_db as dl


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Point the cache at tmp_path and clear the env override."""
    monkeypatch.setattr(dl, "cache_root", lambda: tmp_path)
    monkeypatch.delenv("STEALTHFOX_GEOIP_MMDB", raising=False)
    return tmp_path


def _make_cached(root, tag, name=dl.GEOIP_MMDB_NAME):
    d = root / "geoip" / tag
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_bytes(b"FAKE-MMDB")
    return f


# ──────────────────────────────────────────────────────────────────────
#  env override
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_env_override_returns_file(tmp_path, monkeypatch):
    f = tmp_path / "mine.mmdb"
    f.write_bytes(b"X")
    monkeypatch.setenv("STEALTHFOX_GEOIP_MMDB", str(f))
    assert dl.ensure_geoip_mmdb() == f


@pytest.mark.unit
def test_env_override_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("STEALTHFOX_GEOIP_MMDB", str(tmp_path / "nope.mmdb"))
    with pytest.raises(RuntimeError):
        dl.ensure_geoip_mmdb()


# ──────────────────────────────────────────────────────────────────────
#  every-launch latest check
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_cache_is_latest_no_download(cache, monkeypatch):
    f = _make_cached(cache, "2026.06.17")
    monkeypatch.setattr(dl, "_resolve_latest_geoip_tag", lambda: "2026.06.17")
    monkeypatch.setattr(dl, "_download_file", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not download when cache already on the latest tag")))
    assert dl.ensure_geoip_mmdb() == f


@pytest.mark.unit
def test_new_tag_downloads_and_prunes(cache, monkeypatch):
    old = _make_cached(cache, "2026.06.10")
    monkeypatch.setattr(dl, "_resolve_latest_geoip_tag", lambda: "2026.06.17")

    def _fetch(tag, *, attempts):
        # ONE attempt, because the `except` below is holding a usable mmdb: a
        # retry would buy ten seconds at session start to reach the fallback it
        # was taking anyway. Pinned here rather than left to the stub, because
        # this is the caller that knows there is something to fall back on.
        assert attempts == 1, "a refresh with a cache to fall back on must not retry"
        return _make_cached(cache, tag)

    monkeypatch.setattr(dl, "_download_geoip_tag", _fetch)
    got = dl.ensure_geoip_mmdb()
    assert got.parent.name == "2026.06.17"
    assert not old.parent.exists()  # old tag pruned
    assert got.exists()


@pytest.mark.unit
def test_cold_cache_downloads_latest(cache, monkeypatch):
    monkeypatch.setattr(dl, "_resolve_latest_geoip_tag", lambda: "2026.06.17")

    def _fetch(tag, *, attempts):
        # The other half of the same rule: with nothing cached the failure is
        # terminal for the caller - it raises and `timezone="auto"` stops
        # working - so here the attempts are the whole point.
        assert attempts == dl.DOWNLOAD_ATTEMPTS, (
            "a cold cache has no fallback, so a transient failure must be retried")
        return _make_cached(cache, tag)

    monkeypatch.setattr(dl, "_download_geoip_tag", _fetch)
    got = dl.ensure_geoip_mmdb()
    assert got.parent.name == "2026.06.17"
    assert got.exists()


# ──────────────────────────────────────────────────────────────────────
#  offline resilience (no pinned-tag fallback - the pin rots and 404s)
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_offline_with_cache_uses_cache(cache, monkeypatch):
    f = _make_cached(cache, "2026.06.10")
    monkeypatch.setattr(dl, "_resolve_latest_geoip_tag", lambda: None)  # offline
    monkeypatch.setattr(dl, "_download_file", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("offline → must not attempt a download")))
    assert dl.ensure_geoip_mmdb() == f  # cache reused, no raise


@pytest.mark.unit
def test_cold_cache_offline_raises(cache, monkeypatch):
    monkeypatch.setattr(dl, "_resolve_latest_geoip_tag", lambda: None)  # offline
    with pytest.raises(RuntimeError):
        dl.ensure_geoip_mmdb()


@pytest.mark.unit
def test_download_failure_with_cache_falls_back(cache, monkeypatch):
    f = _make_cached(cache, "2026.06.10")
    monkeypatch.setattr(dl, "_resolve_latest_geoip_tag", lambda: "2026.06.17")

    def boom(tag, *, attempts):
        raise OSError("transient download failure")

    monkeypatch.setattr(dl, "_download_geoip_tag", boom)
    assert dl.ensure_geoip_mmdb() == f  # keeps the old cache rather than failing


# ──────────────────────────────────────────────────────────────────────
#  latest-tag resolution via the permalink 302 (no GitHub API)
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_resolve_tag_from_permalink_redirect(monkeypatch):
    class _Resp:
        headers = {"Location":
                   "https://github.com/daijro/geoip-all-in-one/releases/download/"
                   "2026.06.17/geoip-aio-all.mmdb.zip"}

    monkeypatch.setattr(dl.requests, "head", lambda *a, **k: _Resp())
    assert dl._resolve_latest_geoip_tag() == "2026.06.17"


@pytest.mark.unit
def test_resolve_tag_permalink_fails_falls_back_to_api(monkeypatch):
    def head_boom(*a, **k):
        raise OSError("no network for HEAD")

    monkeypatch.setattr(dl.requests, "head", head_boom)
    monkeypatch.setattr(dl, "_latest_geoip_tag_api", lambda: "2026.06.17")
    assert dl._resolve_latest_geoip_tag() == "2026.06.17"


@pytest.mark.unit
def test_resolve_tag_all_fail_returns_none(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(dl.requests, "head", boom)
    monkeypatch.setattr(dl, "_latest_geoip_tag_api", boom)
    assert dl._resolve_latest_geoip_tag() is None


@pytest.mark.unit
def test_the_tag_fetcher_hands_the_attempts_to_the_downloader(cache, monkeypatch, tmp_path):
    """The link the stubs above hide.

    `test_download_failure_with_cache_falls_back` replaces `_download_geoip_tag`
    wholesale, so it cannot see what that function does with what it is given -
    and that is exactly where the policy has to arrive. Without this, a version
    that accepted `attempts` and then called `_download_file` without it would
    pass every other test in this file.
    """
    seen = {}

    def _fake_download(url, dst, *a, **kw):
        seen["attempts"] = kw.get("attempts")
        raise OSError("no network in a unit test")

    monkeypatch.setattr(dl, "_download_file", _fake_download)
    with pytest.raises(OSError):
        dl._download_geoip_tag("2026.06.17", attempts=1)
    assert seen["attempts"] == 1

    with pytest.raises(OSError):
        dl._download_geoip_tag("2026.06.18", attempts=dl.DOWNLOAD_ATTEMPTS)
    assert seen["attempts"] == dl.DOWNLOAD_ATTEMPTS
