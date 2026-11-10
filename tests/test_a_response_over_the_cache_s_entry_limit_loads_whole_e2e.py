"""A response bigger than the disk cache's limit for one entry is still delivered whole, twice, with the 100 MB capacity.

The core declares `browser.cache.disk.capacity = 102400` (KB, 100 MB) and smart size off. The engine caches no
entry bigger than 1/8 of that capacity: `CacheObserver::EntryIsTooBig` (netwerk/cache2/CacheObserver.cpp:164-182)
takes the smaller of `browser.cache.disk.max_entry_size` (50 MB, StaticPrefList.yaml:1197-1202) and
`DiskCacheCapacity() << (10 - 3)` (CacheObserver.cpp:178-180), which is 12.5 MiB (13 107 200 bytes) at 100 MB. A write
past it ends the cache entry with `NS_ERROR_FILE_TOO_BIG` (CacheFileOutputStream.cpp:110-116).

That must only mean "not cached". Here a 20 MiB response, cacheable by its headers, is fetched twice in one session by
the page: both times the whole body arrives (length and SHA-256 equal to the server's), and the server is asked
twice, because an entry over the limit is not cached. The 20 MiB is over the 12.5 MiB limit of this capacity and under the 50 MB entry limit:
the size that the capacity alone decides.

Needs the engine: INVPW_BINARY_PATH (skipped without it).
"""
from __future__ import annotations

import hashlib
import http.server
import os
import threading
from pathlib import Path

import pytest

from invisible_core import prefs as P
from invisible_core._fpforge import generate_profile
from invisible_core.juggler.instance import launch
from invisible_core.launch import build_launch_env

pytestmark = pytest.mark.e2e

SIZE = 20 * 1024 * 1024
#: the limit of one disk cache entry at this capacity: capacity (KB) << 7, in bytes
ENTRY_LIMIT = P.CACHE_DISK_CAPACITY_KB << 7
BODY = bytes((i * 131 + 7) % 251 for i in range(65536)) * (SIZE // 65536)
DIGEST = hashlib.sha256(BODY).hexdigest()

FETCH = """(async () => {
  const r = await fetch('/big.bin');
  const b = await r.arrayBuffer();
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', b));
  return {status: r.status, length: b.byteLength,
          sha256: Array.from(h, x => x.toString(16).padStart(2, '0')).join('')};
})()"""


class _Handler(http.server.BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        if self.path == "/big.bin":
            _Handler.hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(BODY)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("ETag", '"big-20mib"')
            self.end_headers()
            try:
                self.wfile.write(BODY)
            except OSError:
                pass
            return
        page = b"<!doctype html><title>big</title><p>big</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *args):
        pass


def _binary() -> str:
    path = os.environ.get("INVPW_BINARY_PATH") or os.environ.get("STEALTHFOX_E2E_BINARY")
    if not path or not Path(path).exists():
        pytest.skip("no engine binary given (INVPW_BINARY_PATH)")
    return path


def test_the_entry_is_over_the_limit_of_this_capacity_and_under_the_entry_pref():
    # the test means what it says: 20 MiB is over 12.5 MiB (capacity / 8) and under the 50 MB of max_entry_size
    assert ENTRY_LIMIT == 13_107_200
    assert ENTRY_LIMIT < SIZE < 50 * 1024 * 1024


def test_a_response_over_the_cache_s_entry_limit_arrives_whole_twice():
    _Handler.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    prefs = P.translate_profile_to_prefs(generate_profile(42), timezone="America/New_York")
    assert prefs["browser.cache.disk.capacity"] == P.CACHE_DISK_CAPACITY_KB
    browser = launch(_binary(), prefs=prefs, env=build_launch_env({}, base_env=dict(os.environ)), headless=True)
    try:
        page = browser.new_context().new_page()
        page.lifecycle.goto(base + "/", frame_id=page.main_frame_id)
        for attempt in (1, 2):
            got = page.injected.evaluate_in_caller(page.main_frame_id, FETCH, timeout=120)
            assert got == {"status": 200, "length": SIZE, "sha256": DIGEST}, \
                "load %d of the 20 MiB response: %r (server hits: %d)" % (attempt, got, len(_Handler.hits))
    finally:
        try:
            browser.close()
        finally:
            server.shutdown()
    # Over the limit of this capacity the second load cannot come from the cache: the server answered twice. (At 1 GB
    # the same entry is cached and the server answers once: measured on firefox-61, 2 and 1.) This is what makes the
    # test depend on the declared capacity and not only on the body arriving.
    assert len(_Handler.hits) == 2, "the server answered %d times; 2 means the entry was not cached" % len(_Handler.hits)
