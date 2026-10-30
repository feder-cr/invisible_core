"""Read Firefox's omni.ja, in either of the layouts Firefox ships it in.

A plain zip, and Mozilla's OPTIMIZED jar: the packager of a PGO build (the
retail's, and ours since 2026-10-27) writes the central directory and the
preloaded members first, behind a 4-byte preload size, and the end record's
directory offset is 4. Python's zipfile refuses that layout ("Bad magic number
for central directory"), so a check that opened omni.ja with zipfile saw no
Juggler in a build that had one. The format is the one
python/mozbuild/mozpack/mozjar.py (JarReader) reads; this is the part of it
the core needs, without depending on Mozilla's tree.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Dict

_EOCD = b"PK\x05\x06"
_CDIR = b"PK\x01\x02"
_LOCAL = b"PK\x03\x04"


class JarError(Exception):
    pass


class Jar:
    """The members of a jar, by name; `read(name)` gives a member's bytes."""

    def __init__(self, data: bytes, where: str = "jar"):
        self._data = data
        self._where = where
        end = data.rfind(_EOCD, max(0, len(data) - 0xFFFF - 22))
        if end < 0 or len(data) - end < 22:
            raise JarError("%s is not a zip: no end of central directory record" % where)
        count, _size, offset = struct.unpack_from("<HII", data, end + 10)
        self._entries: Dict[str, tuple] = {}
        for _ in range(count):
            if data[offset:offset + 4] != _CDIR:
                raise JarError("%s: broken central directory at %d" % (where, offset))
            (method, csize, usize, nlen, xlen, clen, local) = struct.unpack_from(
                "<10xH8xIIHHH8xI", data, offset)
            name = data[offset + 46:offset + 46 + nlen].decode("utf-8")
            if not name.endswith("/"):
                self._entries[name] = (method, csize, usize, local)
            offset += 46 + nlen + xlen + clen

    def names(self) -> set:
        return set(self._entries)

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def read(self, name: str) -> bytes:
        method, csize, usize, local = self._entries[name]
        if self._data[local:local + 4] != _LOCAL:
            raise JarError("%s: no local header for %s" % (self._where, name))
        nlen, xlen = struct.unpack_from("<HH", self._data, local + 26)
        start = local + 30 + nlen + xlen
        raw = self._data[start:start + csize]
        if method == 0:
            return raw
        if method == 8:
            out = zlib.decompress(raw, -15)
            if len(out) != usize:
                raise JarError("%s: %s inflates to %d bytes, not %d"
                               % (self._where, name, len(out), usize))
            return out
        raise JarError("%s: %s uses compression method %d" % (self._where, name, method))


def open_jar(path: Path) -> Jar:
    return Jar(Path(path).read_bytes(), str(path))
