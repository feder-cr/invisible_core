"""omni.ja is read in both layouts Firefox ships it in: a plain zip, and
Mozilla's optimized jar, which a PGO build packs (the retail's, and ours since
2026-10-27) and Python's zipfile refuses.

**Why this file exists.** The seal's Juggler check opened omni.ja with zipfile.
On an optimized omni.ja that raises "Bad magic number for central directory",
the check caught it as "unreadable", and a build that carried the Juggler was
reported without one. invisible_core/jar.py reads the format mozjar.py reads.

Known-bad input: the optimized fixture below, which zipfile refuses (asserted
here, so the fixture keeps being the layout it claims to be).
"""
from __future__ import annotations

import io
import struct
import zipfile
import zlib

import pytest

from invisible_core.jar import Jar, JarError
from invisible_core.seal import JUGGLER_ENTRIES, _read_juggler_provenance

MEMBERS = {e: b"// zoom.stealth marker\n" + bytes(range(256)) * 4 for e in JUGGLER_ENTRIES}


def optimized_jar(members: dict) -> bytes:
    """Mozilla's optimized layout: a 4-byte preload size, the central
    directory, the members, and an end record whose directory offset is 4
    (mozjar.py, JarWriter.finish)."""
    entries = []
    for name, body in members.items():
        comp = zlib.compressobj(9, zlib.DEFLATED, -15)
        raw = comp.compress(body) + comp.flush()
        entries.append((name.encode(), body, raw, zlib.crc32(body)))
    cdir_size = sum(46 + len(n) for n, *_ in entries)
    offset = 4 + cdir_size
    cdir, locals_ = b"", b""
    for name, body, raw, crc in entries:
        local = offset + len(locals_)
        cdir += struct.pack("<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 20, 20, 0, 8, 0, 0, crc,
                            len(raw), len(body), len(name), 0, 0, 0, 0, 0, local) + name
        locals_ += struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 20, 0, 8, 0, 0, crc,
                               len(raw), len(body), len(name), 0) + name + raw
    end = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, len(entries), len(entries),
                      cdir_size, 4, 0)
    return struct.pack("<I", 4 + cdir_size) + cdir + locals_ + end


def plain_zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in members.items():
            z.writestr(name, body)
    return buf.getvalue()


def test_the_optimized_fixture_is_one_zipfile_refuses():
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(io.BytesIO(optimized_jar(MEMBERS)))


@pytest.mark.parametrize("build", [optimized_jar, plain_zip])
def test_both_layouts_give_every_member_byte_for_byte(build):
    jar = Jar(build(MEMBERS), build.__name__)
    assert jar.names() == set(MEMBERS)
    for name, body in MEMBERS.items():
        assert jar.read(name) == body


def test_the_seal_finds_the_juggler_in_an_optimized_omni_ja(tmp_path):
    (tmp_path / "omni.ja").write_bytes(optimized_jar(MEMBERS))
    layout, present, marked, notes = _read_juggler_provenance(tmp_path)
    assert (layout, present, marked) == ("omni.ja", True, len(JUGGLER_ENTRIES)), notes


def test_what_is_not_a_zip_is_refused():
    with pytest.raises(JarError):
        Jar(b"not a jar at all, just bytes" * 3, "junk")
