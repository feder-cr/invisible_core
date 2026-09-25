"""A real, minimal MaxMind database, written by hand for the tests.

The suite's other geo tests replace `maxminddb` with a fake, which is right for
them and blind to anything the LIBRARY does - and [B227] lives in the library:
its C extension cannot open a non-ASCII path on Windows. Catching that needs
the real reader on a real file, and a real database is too large to ship.
This one is under 300 bytes: one search-tree node for IPv4, whose left record
(every address with the first bit 0, 8.8.8.8 included) points at one record
`{"location": {"time_zone": ..., "latitude": ..., "longitude": ...}}`, and the
metadata the reader requires. Format: https://maxmind.github.io/MaxMind-DB/
"""
from __future__ import annotations

import struct


def _string(s: str) -> bytes:
    raw = s.encode("utf-8")
    assert len(raw) < 29
    return bytes([0x40 | len(raw)]) + raw


def _uint(type_code: int, value: int) -> bytes:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big") if value else b""
    if type_code <= 7:
        return bytes([(type_code << 5) | len(raw)]) + raw
    return bytes([len(raw), type_code - 7]) + raw


def _double(value: float) -> bytes:
    return bytes([0x68]) + struct.pack(">d", value)


def _map(items: dict) -> bytes:
    assert len(items) < 29
    out = bytes([0xE0 | len(items)])
    for k, v in items.items():
        out += _string(k) + v
    return out


def _array(values: list) -> bytes:
    out = bytes([len(values), 11 - 7])
    for v in values:
        out += v
    return out


def tiny_mmdb(time_zone: str = "Europe/Rome", latitude: float = 41.9,
              longitude: float = 12.5) -> bytes:
    node_count = 1
    data = _map({"location": _map({
        "time_zone": _string(time_zone),
        "latitude": _double(latitude),
        "longitude": _double(longitude),
    })})
    left = node_count + 16 + 0        # a data pointer, to offset 0
    right = node_count                # "not found"
    tree = left.to_bytes(3, "big") + right.to_bytes(3, "big")
    metadata = _map({
        "node_count": _uint(6, node_count),
        "record_size": _uint(5, 24),
        "ip_version": _uint(5, 4),
        "database_type": _string("tiny-test"),
        "languages": _array([_string("en")]),
        "binary_format_major_version": _uint(5, 2),
        "binary_format_minor_version": _uint(5, 0),
        "build_epoch": _uint(9, 1_700_000_000),
        "description": _map({"en": _string("tests")}),
    })
    return tree + b"\x00" * 16 + data + b"\xab\xcd\xefMaxMind.com" + metadata
