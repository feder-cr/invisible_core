"""The download helpers, still reachable through the wrapper's alias shim.

``invisible_core.download`` IS ``invisible_core.download`` (the shim
replaces the module object), so what is left here covers the helper surface and
the fact that the shim still exposes it. The ``ensure_binary`` tests that used
to live here went with the Release Seal, which moved the payload authority off
the release's own checksums.txt and the cache key off the bare tag:

  cold path / SHA verify / extract / missing entry / unsupported platform
      -> invisible_core/tests/test_seal_download.py (five legs, per-leg BuildID)
  cache hit without HTTP
      -> invisible_core/tests/test_seal_cache.py
         ::test_stamped_matching_tree_is_served_with_no_network, and
         ::test_half_extracted_tree_is_never_reused for the predicate that
         replaced Path.exists()
  refusing a tag this core is not sealed to (was: BROKEN_VERSIONS)
      -> invisible_core/tests/test_seal_cache.py
         ::test_pin_to_another_tag_is_refused_without_touching_the_network, and
         invisible_core/tests/test_seal_engine_guard.py
         ::test_tree_without_juggler_is_refused for the undrivable engine itself

The _parse_checksums cases below outlive their caller: ensure_binary no longer
reads checksums.txt at all, so they now guard the parser for the live-release
check in test_release_e2e.py and for anything that reads a published
checksums.txt by hand.
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
import hashlib
import io
import tarfile
from pathlib import Path

import pytest
import requests
import responses

from invisible_core.constants import RELEASE_URL_TEMPLATE
from invisible_core.download import (
    _download_file,
    _extract,
    _github_token,
    _parse_checksums,
    _parse_owner_repo,
    _resolve_asset_url,
    _sha256_file,
    cache_dir_for_version,
    cache_root,
)


def _make_targz(path: Path, inner_name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name=inner_name)
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    data = buf.getvalue()
    path.write_bytes(data)
    return data


# DL2: .tar.gz extraction works
@pytest.mark.unit
def test_extract_tar_gz(tmp_path):
    """_extract handles .tar.gz archives and unpacks the inner files."""
    archive = tmp_path / "bundle.tar.gz"
    _make_targz(archive, "firefox", b"ELF!")
    dst = tmp_path / "out"

    _extract(archive, dst)

    assert (dst / "firefox").exists()
    assert (dst / "firefox").read_bytes() == b"ELF!"


# DL3: checksum line with comment (#) is skipped
@pytest.mark.unit
def test_parse_checksums_skips_comments_and_blanks():
    text = (
        "# this is a comment\n"
        "\n"
        "   # indented comment\n"
        "abc123  file1.zip\n"
        "def456  file2.tar.gz\n"
    )
    out = _parse_checksums(text)
    assert out == {"file1.zip": "abc123", "file2.tar.gz": "def456"}


# DL3 sibling: malformed lines (fewer than 2 fields) are silently ignored
@pytest.mark.unit
def test_parse_checksums_ignores_single_field_lines():
    text = "loner\nabc123  file.zip\n"
    out = _parse_checksums(text)
    assert out == {"file.zip": "abc123"}


# DL3 sibling: last field is treated as filename (supports trailing whitespace tokens)
@pytest.mark.unit
def test_parse_checksums_uses_last_token_as_filename():
    text = "abc123  some/nested/file.zip\n"
    out = _parse_checksums(text)
    assert "some/nested/file.zip" in out


# DL3 regression - issue #15 (LostBoxArt).
# GNU coreutils `sha256sum` (and `shasum -b`) print filenames in BINARY MODE
# with a leading `*`: "hash *filename". The parser used parts[-1] verbatim
# so the key became "*filename" and lookups by bare filename returned None,
# raising `RuntimeError: no SHA256 for {asset}` on every first-time fetch.
@pytest.mark.unit
def test_parse_checksums_strips_star_prefix_binary_mode():
    """`sha256sum -b` format (default on Linux when reading actual files)."""
    text = "abc123 *firefox.tar.gz\n"
    out = _parse_checksums(text)
    assert out == {"firefox.tar.gz": "abc123"}, (
        "binary-mode '*' prefix must be stripped from the filename key"
    )


@pytest.mark.unit
def test_parse_checksums_handles_mixed_binary_and_text_mode():
    """A single checksums.txt with one binary-mode line and one text-mode line.
    Both keys must be normalized (no `*` prefix) so consumers can use the bare
    filename as the lookup key regardless of how each line was produced."""
    text = (
        "aaa111 *firefox-win.zip\n"
        "bbb222  firefox-linux.tar.gz\n"
    )
    out = _parse_checksums(text)
    assert out == {"firefox-win.zip": "aaa111", "firefox-linux.tar.gz": "bbb222"}


@pytest.mark.unit
def test_parse_checksums_handles_multiple_leading_stars():
    """`.lstrip("*")` strips any run of leading asterisks. Not a real sha256sum
    format but defensive - guarantees no `*` survives in any key."""
    text = "abc123 **doubled.zip\n"
    out = _parse_checksums(text)
    assert "doubled.zip" in out
    assert "**doubled.zip" not in out


@pytest.mark.unit
def test_parse_checksums_handles_crlf_line_endings():
    """sha256sum.exe on Windows writes CRLF. The .strip() on each line should
    consume the \\r so the key doesn't end up as 'firefox.zip\\r'."""
    text = "abc123 *firefox.zip\r\ndef456  other.tar.gz\r\n"
    out = _parse_checksums(text)
    assert out == {"firefox.zip": "abc123", "other.tar.gz": "def456"}


@pytest.mark.unit
def test_parse_checksums_handles_utf8_bom_at_start():
    """Some Windows tools prepend a UTF-8 BOM. The first line shouldn't be lost."""
    text = "﻿abc123 *firefox.zip\n"
    out = _parse_checksums(text)
    # The BOM stays attached to the hash field as a non-fatal artifact;
    # what matters is that the FILENAME key is parsed and normalized.
    keys = list(out.keys())
    assert "firefox.zip" in keys, f"BOM caused first line to be lost: keys={keys}"


@pytest.mark.unit
def test_parse_checksums_handles_indented_lines():
    """Leading whitespace on a data line must not break parsing."""
    text = "   abc123 *indented.zip\n"
    out = _parse_checksums(text)
    assert out == {"indented.zip": "abc123"}


@pytest.mark.unit
def test_parse_checksums_handles_trailing_whitespace():
    """Trailing spaces on a line shouldn't end up in the key."""
    text = "abc123 *trailing.zip   \n"
    out = _parse_checksums(text)
    # After .strip() the trailing spaces are gone, so the key is clean
    assert out == {"trailing.zip": "abc123"}


@pytest.mark.unit
def test_parse_checksums_real_world_sha256sum_b_output(tmp_path):
    """End-to-end: invoke the actual `sha256sum` (or its Python equivalent)
    on a real file and verify the parser handles that output verbatim.

    We can't depend on sha256sum being on PATH on Windows, so we synthesize
    the exact byte sequence that GNU coreutils 9.x produces."""
    fake_archive = tmp_path / "release.tar.gz"
    fake_archive.write_bytes(b"some content")
    sha = hashlib.sha256(fake_archive.read_bytes()).hexdigest()
    # Exact format coreutils prints in binary mode (default for files):
    #   "<hash><SP>*<filename>\n"
    coreutils_output = f"{sha} *{fake_archive.name}\n"

    out = _parse_checksums(coreutils_output)
    assert out == {"release.tar.gz": sha}


@pytest.mark.unit
def test_parse_checksums_text_mode_two_space_separator():
    """`sha256sum --text` format uses two spaces. Must also parse cleanly
    and the key must be identical to the binary-mode case."""
    text = "abc123  textmode.zip\n"
    out = _parse_checksums(text)
    assert out == {"textmode.zip": "abc123"}


@pytest.mark.unit
def test_parse_checksums_empty_file_returns_empty_dict():
    assert _parse_checksums("") == {}
    assert _parse_checksums("\n\n\n") == {}
    assert _parse_checksums("   \n\t\n") == {}


@pytest.mark.unit
def test_parse_checksums_all_comment_file_returns_empty_dict():
    """A file with only comments shouldn't crash and shouldn't produce keys."""
    text = "# generated by release script\n# 2026-05-20\n"
    assert _parse_checksums(text) == {}


# DL3 regression - the #15 sentinel that ran the parser through ensure_binary
# retired with the checksums.txt contract: the fetcher no longer downloads or
# parses checksums.txt, so there is no integration form of this test left to
# write. The parser cases above still guard the format, and
# test_release_e2e.py::test_fetch_against_live_release still hits the live
# release. What is gone is the unit-level coupling of the two.


# DL4: unknown archive format (.rar) raises RuntimeError
@pytest.mark.unit
def test_extract_unknown_format_raises(tmp_path):
    archive = tmp_path / "thing.rar"
    archive.write_bytes(b"not-a-real-rar")
    dst = tmp_path / "out"

    with pytest.raises(RuntimeError, match="unknown archive format"):
        _extract(archive, dst)


# Pure helper: _parse_owner_repo
@pytest.mark.unit
def test_parse_owner_repo_valid():
    owner, repo = _parse_owner_repo(
        "https://github.com/feder-cr/invisible_core/releases/download/x/y"
    )
    assert owner == "feder-cr"
    assert repo == "invisible_core"


@pytest.mark.unit
def test_parse_owner_repo_invalid_raises():
    with pytest.raises(RuntimeError, match="cannot parse owner/repo"):
        _parse_owner_repo("not-a-github-url")


# Pure helper: _sha256_file matches hashlib output
@pytest.mark.unit
def test_sha256_file_matches_hashlib(tmp_path):
    payload = b"hello world"
    f = tmp_path / "file.bin"
    f.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert _sha256_file(f) == expected


# _github_token precedence: STEALTHFOX_GITHUB_TOKEN beats GITHUB_TOKEN
@pytest.mark.unit
def test_github_token_stealthfox_wins(monkeypatch):
    monkeypatch.setenv("STEALTHFOX_GITHUB_TOKEN", "stealth")
    monkeypatch.setenv("GITHUB_TOKEN", "generic")
    assert _github_token() == "stealth"


@pytest.mark.unit
def test_github_token_falls_back_to_github_token(monkeypatch):
    monkeypatch.delenv("STEALTHFOX_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "generic")
    assert _github_token() == "generic"


@pytest.mark.unit
def test_github_token_none_when_unset(monkeypatch):
    monkeypatch.delenv("STEALTHFOX_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert _github_token() is None


# ========================================================================== #
# _resolve_asset_url - public-repo direct URL vs private-repo API resolution
# ========================================================================== #
# This function chooses between two code paths based on whether a GitHub
# token is set. Both paths produce a downloadable URL but via different
# mechanisms, and a regression here would surface as 404 / 403 / wrong
# binary downloaded.

@pytest.mark.unit
def test_resolve_asset_url_public_returns_direct_url(monkeypatch):
    """No token → return the direct releases/download URL verbatim."""
    monkeypatch.delenv("STEALTHFOX_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    url = _resolve_asset_url("firefox-4", "thing.zip")
    assert url == RELEASE_URL_TEMPLATE.format(tag="firefox-4", asset="thing.zip")
    assert "api.github.com" not in url  # public path must skip the API


@pytest.mark.unit
def test_resolve_asset_url_public_url_format_is_stable(monkeypatch):
    """The exact URL shape is what GitHub clients have learned to cache.
    Changing it without bumping BINARY_VERSION would 404 on first fetch
    for every existing user - guard against accidental drift."""
    monkeypatch.delenv("STEALTHFOX_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    url = _resolve_asset_url("firefox-4", "abc.tar.gz")
    assert url == (
        "https://github.com/feder-cr/firefox_antidetect_patch/releases/"
        "download/firefox-4/abc.tar.gz"
    )


@pytest.mark.unit
@responses.activate
def test_resolve_asset_url_private_uses_api_with_token(monkeypatch):
    """Token set → hit the API and return the asset.url (which 302s with
    Accept: application/octet-stream). The direct release URL would 404
    for a private repo even with the token in headers."""
    monkeypatch.setenv("STEALTHFOX_GITHUB_TOKEN", "ghp_fake")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    api_url = (
        "https://api.github.com/repos/feder-cr/firefox_antidetect_patch"
        "/releases/tags/firefox-4"
    )
    responses.add(
        responses.GET, api_url,
        json={"assets": [
            {"name": "other.zip", "url": "https://api.github.com/.../1"},
            {"name": "wanted.zip", "url": "https://api.github.com/.../2"},
        ]},
        status=200,
    )
    url = _resolve_asset_url("firefox-4", "wanted.zip")
    assert url == "https://api.github.com/.../2"


@pytest.mark.unit
@responses.activate
def test_resolve_asset_url_private_raises_when_asset_missing(monkeypatch):
    """If the asset name isn't on the release, raise - better to fail fast
    with the asset name in the message than to download something else."""
    monkeypatch.setenv("STEALTHFOX_GITHUB_TOKEN", "ghp_fake")
    api_url = (
        "https://api.github.com/repos/feder-cr/firefox_antidetect_patch"
        "/releases/tags/firefox-4"
    )
    responses.add(
        responses.GET, api_url,
        json={"assets": [{"name": "other.zip", "url": "x"}]},
        status=200,
    )
    with pytest.raises(RuntimeError, match="not-here.zip"):
        _resolve_asset_url("firefox-4", "not-here.zip")


@pytest.mark.unit
@responses.activate
def test_resolve_asset_url_private_propagates_api_4xx(monkeypatch):
    """If the API returns 404 (release doesn't exist) or 401 (bad token),
    don't swallow it silently - raise so the user sees the real reason."""
    monkeypatch.setenv("STEALTHFOX_GITHUB_TOKEN", "ghp_fake")
    api_url = (
        "https://api.github.com/repos/feder-cr/firefox_antidetect_patch"
        "/releases/tags/firefox-99"
    )
    responses.add(responses.GET, api_url, status=404)
    with pytest.raises(requests.HTTPError):
        _resolve_asset_url("firefox-99", "thing.zip")


@pytest.mark.unit
@responses.activate
def test_resolve_asset_url_private_sends_auth_header(monkeypatch):
    """The API call MUST include `Authorization: token <ghp_...>`, otherwise
    a private repo returns 404 and the user thinks the release is missing."""
    monkeypatch.setenv("STEALTHFOX_GITHUB_TOKEN", "ghp_secret")
    api_url = (
        "https://api.github.com/repos/feder-cr/firefox_antidetect_patch"
        "/releases/tags/firefox-4"
    )

    captured = {}
    def callback(request):
        captured["auth"] = request.headers.get("Authorization")
        return (200, {}, '{"assets":[{"name":"x.zip","url":"https://x/y"}]}')
    responses.add_callback(responses.GET, api_url, callback=callback,
                           content_type="application/json")
    _resolve_asset_url("firefox-4", "x.zip")
    assert captured["auth"] == "token ghp_secret"


# ========================================================================== #
# _download_file - file streaming + error propagation
# ========================================================================== #

@pytest.mark.unit
@responses.activate
def test_download_file_writes_full_payload_to_disk(tmp_path):
    """A 200 OK returns the full body; the file on disk matches byte-for-byte."""
    url = "https://example.com/some-large.bin"
    payload = bytes(range(256)) * 1024  # 256 KB, varied bytes
    responses.add(responses.GET, url, body=payload, status=200)

    dst = tmp_path / "downloaded.bin"
    _download_file(url, dst)
    assert dst.exists()
    assert dst.read_bytes() == payload


@pytest.mark.unit
@responses.activate
def test_download_file_creates_parent_directories(tmp_path):
    """The dst's parent may not exist yet - _download_file is expected to
    mkdir -p before writing. Without this, the first fetch on a clean
    machine raises FileNotFoundError because the cache dir doesn't exist."""
    url = "https://example.com/x.bin"
    responses.add(responses.GET, url, body=b"data", status=200)

    deep = tmp_path / "a" / "b" / "c" / "x.bin"
    _download_file(url, deep)
    assert deep.exists()
    assert deep.read_bytes() == b"data"


@pytest.mark.unit
@responses.activate
def test_download_file_propagates_http_404(tmp_path):
    """404s from the CDN must raise - silent 404 → empty file → SHA mismatch
    is a much worse failure mode."""
    url = "https://example.com/missing.bin"
    responses.add(responses.GET, url, status=404)
    with pytest.raises(requests.HTTPError):
        _download_file(url, tmp_path / "out.bin")


@pytest.mark.unit
@responses.activate
def test_download_file_propagates_http_500(tmp_path):
    """Server errors must surface, not be swallowed as 'empty download'."""
    url = "https://example.com/broken.bin"
    responses.add(responses.GET, url, status=500)
    with pytest.raises(requests.HTTPError):
        _download_file(url, tmp_path / "out.bin")


@pytest.mark.unit
@responses.activate
def test_download_file_adds_auth_for_api_urls(monkeypatch, tmp_path):
    """When downloading from api.github.com (private-repo flow), the
    request MUST include `Authorization: token <...>` and
    `Accept: application/octet-stream` - otherwise the API returns the
    asset JSON instead of the binary."""
    monkeypatch.setenv("STEALTHFOX_GITHUB_TOKEN", "ghp_secret")
    url = "https://api.github.com/repos/x/y/releases/assets/123"

    captured = {}
    def callback(request):
        captured["auth"] = request.headers.get("Authorization")
        captured["accept"] = request.headers.get("Accept")
        return (200, {}, b"BIN!")
    responses.add_callback(responses.GET, url, callback=callback)

    _download_file(url, tmp_path / "out.bin")
    assert captured["auth"] == "token ghp_secret"
    assert captured["accept"] == "application/octet-stream"


@pytest.mark.unit
@responses.activate
def test_download_file_does_not_send_auth_for_non_api_urls(monkeypatch, tmp_path):
    """Public-repo flow hits github.com/.../releases/download/... directly.
    Sending an auth header to that URL is unnecessary and would leak the
    token in CDN access logs."""
    monkeypatch.setenv("STEALTHFOX_GITHUB_TOKEN", "ghp_secret")
    url = "https://github.com/feder-cr/invisible_core/releases/download/firefox-4/x.zip"

    captured = {}
    def callback(request):
        captured["auth"] = request.headers.get("Authorization")
        return (200, {}, b"BIN!")
    responses.add_callback(responses.GET, url, callback=callback)

    _download_file(url, tmp_path / "out.bin")
    assert captured["auth"] is None, (
        "Auth header leaked to a public CDN URL - would expose the token "
        "in GitHub's access logs."
    )


# ========================================================================== #
# cache_root + cache_dir_for_version - path resolution
# ========================================================================== #

@pytest.mark.unit
def test_cache_root_returns_path():
    """Must return a Path, not a string - downstream code uses .mkdir() etc."""
    p = cache_root()
    assert isinstance(p, Path)


@pytest.mark.unit
def test_cache_root_contains_package_name():
    """The cache dir should be identifiable as ours so users can `rm -rf`
    it without nuking other tools' caches."""
    p = cache_root()
    assert "invisible-playwright" in str(p).lower()


@pytest.mark.unit
def test_cache_dir_for_version_appends_version_segment():
    """Each binary version gets its own subdir so multiple versions can
    coexist (useful for downgrade / A-B testing)."""
    p = cache_dir_for_version("firefox-99")
    assert p.name == "firefox-99"
    assert p.parent == cache_root()


# The no-arg call no longer names cache_root()/<BINARY_VERSION>: the cache key is
# content-addressed (tag + base version + the BuildID of this host's leg), so the
# assertion moved with it, to
# invisible_core/tests/test_seal_download.py
# ::test_cache_dir_for_version_no_arg_is_the_sealed_content_key.


@pytest.mark.unit
def test_cache_dir_isolation_between_versions():
    """firefox-3 and firefox-4 must NEVER share a directory - extraction
    would clobber one with the other and break downgrade."""
    a = cache_dir_for_version("firefox-3")
    b = cache_dir_for_version("firefox-4")
    assert a != b
    assert a.parent == b.parent  # but they share the same root


# ========================================================================== #
# _parse_owner_repo - more edge cases
# ========================================================================== #

@pytest.mark.unit
def test_parse_owner_repo_extracts_from_canonical_template():
    """Must work against the exact template stored in constants.py."""
    owner, repo = _parse_owner_repo(RELEASE_URL_TEMPLATE)
    assert owner and repo  # something extracted
    assert "/" not in owner and "/" not in repo  # no slashes in either segment


@pytest.mark.unit
@pytest.mark.parametrize("bad_template", [
    "http://github.com/x/y/releases/",          # http, not https
    "https://gitlab.com/x/y/releases/",         # wrong host
    "https://github.com/onlyone/releases/",     # missing repo segment
    "",                                         # empty
    "github.com/x/y/releases/",                 # missing scheme
])
def test_parse_owner_repo_rejects_malformed_urls(bad_template):
    """Any URL that doesn't match the canonical shape must raise - silent
    None/empty extraction would build broken API URLs and confuse the user."""
    with pytest.raises(RuntimeError, match="cannot parse"):
        _parse_owner_repo(bad_template)


@pytest.mark.unit
def test_parse_owner_repo_handles_repos_with_dashes_and_underscores():
    """Repo names with -, _, . are valid on GitHub; the regex must accept them."""
    owner, repo = _parse_owner_repo(
        "https://github.com/my-org/my_cool.repo/releases/download/x/y.zip"
    )
    assert owner == "my-org"
    assert repo == "my_cool.repo"


# BROKEN_VERSIONS is gone: a superseded build is now a build no seal points at,
# and ensure_binary refuses ANY tag but the sealed one (SealMismatch), so there
# is no list to keep. The refusal itself is covered in
# invisible_core/tests/test_seal_cache.py
# ::test_pin_to_another_tag_is_refused_without_touching_the_network.
# What the blacklist said and the seal does not: WHY that particular tag is
# undrivable. For firefox-8 (published without the juggler) the message now
# suggests installing "the invisible-core sealed to it", which for that tag does
# not and cannot exist. The engine itself is still refused on every launch route,
# with a better message, by
# invisible_core/tests/test_seal_engine_guard.py::test_tree_without_juggler_is_refused.
