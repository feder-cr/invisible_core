"""The seal's digest is of its content, and the cache stamp survives concurrent writers (B269).

Measured on 2026-10-10: four sessions starting at once on one cached engine
rewrote its stamp ten times in five rounds and two of them died with
`PermissionError` on the rename. Two causes, each with its test here: the
digest was of the FILE's bytes, so a CRLF checkout and an LF wheel of the same
seal disagreed and each start that saw the other's stamp "adopted" the tree
again; and every writer used the one temporary file `.invisible-seal.tmp`.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

from invisible_core import seal as sealmod
from invisible_core.seal import (
    STAMP_NAME, load_seal, packaged_seal_path, read_stamp, seal_digest, write_stamp,
)

pytestmark = pytest.mark.unit


def _seal_data() -> dict:
    return json.loads(packaged_seal_path().read_text(encoding="utf-8"))


def _write(path: pathlib.Path, data: dict, *, newline: str, indent) -> pathlib.Path:
    text = json.dumps(data, indent=indent).replace("\n", newline)
    path.write_bytes(text.encode("utf-8"))
    return path


def test_the_digest_is_the_same_for_every_encoding_of_one_seal(tmp_path):
    """⛔ KNOWN-BAD: `sha256(file bytes)` gave the CRLF checkout of
    `seal.json` one digest and the LF wheel another, for the same commit."""
    data = _seal_data()
    lf = load_seal(_write(tmp_path / "lf.json", data, newline="\n", indent=2))
    crlf = load_seal(_write(tmp_path / "crlf.json", data, newline="\r\n", indent=2))
    dense = load_seal(_write(tmp_path / "dense.json", data, newline="\n", indent=None))
    reordered = load_seal(_write(tmp_path / "reordered.json",
                                 dict(reversed(list(data.items()))), newline="\n", indent=4))
    assert (lf.digest == crlf.digest == dense.digest == reordered.digest
            == seal_digest(data)), "one seal, several digests"
    assert (tmp_path / "crlf.json").read_bytes() != (tmp_path / "lf.json").read_bytes()


def test_a_different_seal_has_a_different_digest(tmp_path):
    data = _seal_data()
    changed = dict(data, upstream_version=data["upstream_version"] + ".1")
    a = load_seal(_write(tmp_path / "a.json", data, newline="\n", indent=2))
    b = load_seal(_write(tmp_path / "b.json", changed, newline="\n", indent=2))
    assert a.digest != b.digest


def _any_seal():
    return load_seal(packaged_seal_path())


def _asset_name(seal):
    return next(iter(seal.assets)) if seal.assets else "local"


def test_each_writer_has_its_own_temporary_file_and_leaves_none(tmp_path, monkeypatch):
    """The one shared `.invisible-seal.tmp` is what two writers collided on."""
    seal = _any_seal()
    seen = []
    real = os.replace

    def replace(src, dst):
        seen.append(pathlib.Path(src).name)
        real(src, dst)

    monkeypatch.setattr(sealmod.os, "replace", replace)
    write_stamp(tmp_path, seal, asset=_asset_name(seal), asset_sha256=None, adopted=True)
    assert seen == [f"{STAMP_NAME}.{os.getpid()}.tmp"]
    assert sorted(os.listdir(tmp_path)) == [STAMP_NAME]
    assert read_stamp(tmp_path)["seal_digest"] == seal.digest


def test_a_stamp_another_writer_just_wrote_for_the_same_seal_is_kept(tmp_path, monkeypatch):
    """Windows refuses the rename while the other process is on the file. If
    that process wrote this seal's stamp, ours is dropped and nothing is
    raised; the temporary file does not stay behind."""
    seal = _any_seal()
    other = {"seal_digest": seal.digest, "written_by": "the other writer"}
    (tmp_path / STAMP_NAME).write_text(json.dumps(other), encoding="utf-8")

    def refused(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(sealmod.os, "replace", refused)
    write_stamp(tmp_path, seal, asset=_asset_name(seal), asset_sha256=None, adopted=True)
    assert read_stamp(tmp_path) == other
    assert sorted(os.listdir(tmp_path)) == [STAMP_NAME]


def test_a_reader_that_lets_go_is_waited_for_and_one_that_does_not_is_an_error(tmp_path, monkeypatch):
    seal = _any_seal()
    monkeypatch.setattr(sealmod, "_STAMP_REPLACE_PAUSE", 0.0)
    real = os.replace
    refusals = {"left": 3}

    def busy_then_free(src, dst):
        if refusals["left"]:
            refusals["left"] -= 1
            raise PermissionError(32, "being used by another process")
        real(src, dst)

    monkeypatch.setattr(sealmod.os, "replace", busy_then_free)
    write_stamp(tmp_path, seal, asset=_asset_name(seal), asset_sha256=None, adopted=True)
    assert read_stamp(tmp_path)["seal_digest"] == seal.digest

    def never_free(src, dst):
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(sealmod, "_STAMP_REPLACE_ATTEMPTS", 3)
    monkeypatch.setattr(sealmod.os, "replace", never_free)
    (tmp_path / STAMP_NAME).unlink()
    with pytest.raises(PermissionError):
        write_stamp(tmp_path, seal, asset=_asset_name(seal), asset_sha256=None, adopted=True)
    assert os.listdir(tmp_path) == [], "the temporary file of a failed write stayed behind"


_WRITER = """
import sys
from invisible_core.seal import load_seal, packaged_seal_path, write_stamp
seal = load_seal(packaged_seal_path())
asset = next(iter(seal.assets)) if seal.assets else "local"
for _ in range(20):
    write_stamp(sys.argv[1], seal, asset=asset, asset_sha256=None, adopted=True)
print("ok")
"""


def test_concurrent_writers_all_succeed_and_leave_one_valid_stamp(tmp_path):
    """The measured race, with real processes: six writers, twenty stamps
    each, on one directory. ⛔ KNOWN-BAD: with the shared temporary file, two
    of twenty starters died here."""
    seal = _any_seal()
    writers = [subprocess.Popen([sys.executable, "-c", _WRITER, str(tmp_path)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
               for _ in range(6)]
    results = [w.communicate(timeout=120) for w in writers]
    failed = [(err.strip().splitlines()[-1] if err.strip() else "")
              for out, err in results if out.strip() != "ok"]
    assert not failed, f"{len(failed)} writer(s) died: {failed}"
    assert sorted(os.listdir(tmp_path)) == [STAMP_NAME]
    assert read_stamp(tmp_path)["seal_digest"] == seal.digest
