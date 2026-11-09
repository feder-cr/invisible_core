"""The arithmetic class a persona declares is the one of its GPU's family.

`zoom.stealth.webgl.alu_class` tells the engine which arithmetic the declared GPU
has: 0 for every GPU that is not NVIDIA, 1 for NVIDIA Maxwell and Pascal, 2 for
NVIDIA Volta and later (Turing, Ampere, Ada, Blackwell). The measurement behind
it: sin and cos return other bits from Volta on, nothing else changes between
families, and no driver or model inside a family moves a bit
(C:/ff/invisible_D3D11Core/notes/what-changes-the-bits.md).

The class follows the real device the persona declares (`win_gfx_devices.json`),
not the renderer bucket: the "GTX 980" bucket stands for a GTX 980 (Maxwell) and
for every later GeForce. So the check is per device, and each device's expected
class is written here by its GPU, independently of the family the data carries.
"""
from __future__ import annotations

import json
import re
import sys

import pytest

from invisible_core import _webgl_personas as W
from invisible_core._fpforge import generate_profile
from invisible_core.prefs import translate_profile_to_prefs

pytestmark = pytest.mark.unit

_DEVICES = json.loads(W._DEVICES_PATH.read_text(encoding="utf-8"))["by_renderer"]
_ALL = [d for devs in _DEVICES.values() for d in devs]

#: every NVIDIA device of the pool, by the GPU it is (chip in the comment).
_NVIDIA = {
    "gtx980_58278": 1,       # GM204, Maxwell
    "gtx1060_58278": 1,      # GP106, Pascal
    "gtx1050ti_58278": 1,    # GP107, Pascal
    "gtx1070_58278": 1,      # GP104, Pascal
    "gtx1650_62101": 2,      # TU117, Turing
    "rtx2060_62101": 2,      # TU106, Turing
    "gtx1660s_62101": 2,     # TU116, Turing
    "gtx1660ti_62101": 2,    # TU116, Turing
    "rtx3060_62101": 2,      # GA106, Ampere
    "rtx3050_62101": 2,      # GA106, Ampere
    "rtx3060ti_62101": 2,    # GA104, Ampere
    "rtx3070_62101": 2,      # GA104, Ampere
    "rtx3060l_62101": 2,     # GA106M, Ampere
    "rtx3080_62101": 2,      # GA102, Ampere
    "rtx4060_62101": 2,      # AD107, Ada
    "rtx4060l_62101": 2,     # AD107M, Ada
    "rtx4060ti_62101": 2,    # AD106, Ada
    "rtx4050l_62101": 2,     # AD107M, Ada
    "rtx4070_62101": 2,      # AD104, Ada
    "rtx4070s_62101": 2,     # AD104, Ada
    "rtx5060_62101": 2,      # Blackwell
    "rtx5060ti_62101": 2,    # Blackwell
    "rtx5060l_62101": 2,     # Blackwell
    "rtx5070_62101": 2,      # Blackwell
    "rtx5070ti_62101": 2,    # Blackwell
    "rtx5080_62101": 2,      # Blackwell
    "rtx5080l_61742": 2,     # Blackwell
}

#: the families of the pool study and their class, as the study gives them.
_FAMILY_CLASS = {"nv_maxwell": 1, "nv_pascal": 1, "nv_volta": 2, "nv_turing": 2,
                 "nv_ampere": 2, "nv_ada": 2, "nv_blackwell": 2}


def _expected(device):
    if device["vendor_id"] != "0x10de":
        return 0
    # a new NVIDIA device is a decision to take here, not a default
    assert device["label"] in _NVIDIA, "an NVIDIA device with no expected class: " + device["label"]
    return _NVIDIA[device["label"]]


def test_the_table_is_the_studys():
    assert W._ALU_CLASS_BY_FAMILY == _FAMILY_CLASS


@pytest.mark.parametrize("device", _ALL, ids=lambda d: d["label"])
def test_every_pool_device_declares_the_class_of_its_family(device):
    assert W.alu_class_for(device) == _expected(device)


def test_no_class_exists_without_nvidia_having_maxwell_and_later_ones():
    classes = {W.alu_class_for(d) for d in _ALL}
    assert classes == {0, 1, 2}


def test_an_nvidia_device_with_no_family_is_refused_not_guessed():
    nv = dict(next(d for d in _ALL if d["vendor_id"] == "0x10de"))
    del nv["alu_family"]
    with pytest.raises(ValueError, match="no known GPU family"):
        W.alu_class_for(nv)
    nv["alu_family"] = "nv_unheard_of"
    with pytest.raises(ValueError, match="no known GPU family"):
        W.alu_class_for(nv)


def test_a_device_that_is_not_nvidia_is_class_zero_whatever_it_claims():
    amd = dict(next(d for d in _ALL if d["vendor_id"] == "0x1002"))
    amd["alu_family"] = "nv_ampere"
    assert W.alu_class_for(amd) == 0


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_the_pref_a_session_emits_is_its_device_s_class(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    seen = set()
    for seed in range(0, 600, 3):
        prof = generate_profile(seed=seed)
        prefs = translate_profile_to_prefs(prof, timezone="America/New_York")
        dev = W.gfx_device_for(prof.gpu.renderer, prof.gpu.device)
        assert type(prefs["zoom.stealth.webgl.alu_class"]) is int
        assert prefs["zoom.stealth.webgl.alu_class"] == _expected(dev), (seed, dev["label"])
        seen.add(prefs["zoom.stealth.webgl.alu_class"])
    assert seen == {0, 1, 2}


def test_the_class_is_declared_in_one_place():
    # one literal assignment in the sources: two would let the load order decide
    import pathlib
    root = pathlib.Path(W.__file__).parent
    hits = [p.name for p in root.rglob("*.py")
            for m in re.finditer(r"""["']zoom\.stealth\.webgl\.alu_class["']""",
                                 p.read_text(encoding="utf-8"))]
    assert hits == ["prefs.py"], hits
