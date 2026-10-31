"""engine_port.json is a port branch's statement of the engine it serves before
its seal exists, and it must not outlive that (tests/_engine_source.py).

A declaration that names the engine the bundled seal already pins says nothing
the seal does not, and one that names an older version than the seal points
the engine-source tests backwards: both are refused, so the file goes the day
the branch's own seal lands.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _engine_source import PORT_FILE, port_declaration  # noqa: E402

pytestmark = pytest.mark.unit


def _bundled_seal() -> dict:
    import invisible_core

    return json.loads((Path(invisible_core.__file__).parent / "seal.json").read_text(encoding="utf-8"))


def _version(v: str):
    return tuple(int(x) for x in v.split("."))


def test_a_port_declaration_names_a_newer_engine_than_the_seal():
    port = port_declaration()
    if port is None:
        pytest.skip("no %s: this branch's engine is its seal's" % PORT_FILE.name)
    seal = _bundled_seal()
    assert port["source_commit"] != seal["source_commit"], (
        "%s names the engine the bundled seal already pins (%s): delete it"
        % (PORT_FILE.name, seal["source_commit"]))
    # The same Firefox is fine: an engine release on the seal's Firefox version
    # (new hooks, same upstream) is a newer engine too; its commit differs, which
    # the assert above already requires. Only an older Firefox points backwards.
    assert _version(port["upstream_version"]) >= _version(seal["upstream_version"]), (
        "%s names %s, older than the seal's %s: delete it or correct it"
        % (PORT_FILE.name, port["upstream_version"], seal["upstream_version"]))
    assert len(port["source_commit"]) == 40 and port.get("why"), (
        "a port declaration names a full commit and says why it exists")
