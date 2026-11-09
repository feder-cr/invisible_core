"""The Windows GPU pool is made of real devices at the real frequency of their family.

A persona is one plausible Windows machine, and the share of GPUs among personas is the
share among real users. The weights are those of the families of the pool study
(Firefox Public Data Report week 2026-11-02 for the vendor split and the Intel
codenames, Steam survey October 2026 for NVIDIA and the rest of AMD;
C:/ff/invisible_D3D11Core/pool/weights_20261109/pool_weights_20261109.json, sha256 of
the file in `win_gpu_family_weights.json`). They are written here a second time on
purpose: the data may not move without this table moving with it.

What a page sees of a pool slot is a SanitizeRenderer bucket ("GeForce GTX 980"
stands for every GeForce from the 900 series up); the real device behind it is drawn
inside the bucket by weight. So the checks are per device and per family, and the slot
probabilities follow from the devices.

A family with no device is listed in the data with the reason; it takes no weight.
Nothing in the pool may be a GPU outside the table, or a device without its source.
"""
from __future__ import annotations

import collections
import json
import re

import pytest

from invisible_core import _webgl_personas as W

pytestmark = pytest.mark.unit

_FAMILY_WEIGHTS = json.loads(
    (W._POOL_PATH.with_name("win_gpu_family_weights.json")).read_text(encoding="utf-8"))
_DATA = json.loads(W._DEVICES_PATH.read_text(encoding="utf-8"))["by_renderer"]
_DEVICES = [d for devs in _DATA.values() for d in devs]
_POOL = json.loads(W._POOL_PATH.read_text(encoding="utf-8"))["win"]

#: the table of the study, percent of Windows personas
_TABLE = {
    "intel_gen9": 24.15, "intel_gen12": 23.03, "intel_other_older_or_small": 11.74,
    "amd_vega_igpu": 7.0, "intel_gen7.5": 4.47, "nv_ada": 3.77, "nv_ampere": 3.71,
    "intel_gen7": 3.57, "nv_blackwell": 3.44, "nv_turing": 2.08, "amd_rdna2": 1.91,
    "intel_gen6": 1.67, "amd_rdna4": 1.56, "amd_igpu_graphics": 1.54, "intel_gen11": 1.42,
    "amd_rdna3": 1.38, "amd_polaris": 1.17, "nv_pascal": 0.92, "amd_rdna1": 0.54,
    "amd_other": 0.5, "nv_other": 0.25, "nv_maxwell": 0.12, "nv_kepler_maxwell": 0.05,
    "nv_older": 0.02, "nv_other_pro": 0.01,
}
#: the chip prefix of an NVIDIA device id and the generation it is
_CHIP_FAMILY = {"GM": "nv_maxwell", "GP": "nv_pascal", "TU": "nv_turing", "GA": "nv_ampere",
                "AD": "nv_ada", "GB": "nv_blackwell"}
_ANGLE = re.compile(
    r"ANGLE \((?P<v>NVIDIA|Intel|AMD), (?P<name>[^,]+) Direct3D11 vs_5_0 ps_5_0, D3D11\)")


def test_the_family_weights_are_the_studys_table():
    assert _FAMILY_WEIGHTS["weights_pct"] == _TABLE
    assert abs(sum(_TABLE.values()) - 100.0) < 0.05


def test_the_weights_of_a_family_add_up_to_the_table():
    got = collections.defaultdict(float)
    for d in _DEVICES:
        got[d["pool_family"]] += d["weight"]
    for family, w in got.items():
        assert abs(w - _TABLE[family]) <= 0.01, (family, w, _TABLE[family])


def test_every_family_has_devices_or_a_reason():
    have = {d["pool_family"] for d in _DEVICES}
    uncovered = _FAMILY_WEIGHTS["_meta"]["uncovered"]
    assert have | set(uncovered) == set(_TABLE)
    assert not have & set(uncovered)
    assert all(len(r) > 20 for r in uncovered.values())


def test_no_device_is_outside_the_table():
    assert not [d["label"] for d in _DEVICES if d["pool_family"] not in _TABLE]


def test_every_device_has_its_sources():
    for d in _DEVICES:
        assert "public PCI ID registry" in d["source"] and "driver:" in d["source"], d["label"]
        assert d["share_source"], d["label"]
        assert re.fullmatch(r"0x[0-9a-f]{4}", d["device_id"]), d["label"]
        assert re.fullmatch(r"(\d+\.){3}\d+", d["driver_version"]), d["label"]
        assert d["share"] > 0, d["label"]


def test_a_device_id_names_one_device():
    ids = [(d["vendor_id"], d["device_id"], d["driver_version"], d["windows_name"]) for d in _DEVICES]
    assert len(set(ids)) == len(ids)
    assert len({d["label"] for d in _DEVICES}) == len(_DEVICES)


def test_nvidia_family_is_the_one_of_the_chip():
    nv = [d for d in _DEVICES if d["vendor_id"] == "0x10de"]
    assert nv
    for d in nv:
        assert _CHIP_FAMILY[d["chip"][:2]] == d["alu_family"] == d["pool_family"], d["label"]
    assert all("alu_family" not in d for d in _DEVICES if d["vendor_id"] != "0x10de")


def test_every_slot_is_an_angle_d3d11_string_of_its_vendor():
    for e in _POOL:
        renderer = e["prefs"]["zoom.stealth.webgl.renderer"]
        m = _ANGLE.fullmatch(renderer)
        assert m, renderer
        assert e["prefs"]["zoom.stealth.webgl.vendor"] == "Google Inc. (%s)" % m["v"], renderer
        assert renderer in _DATA


def test_the_gtx_980_bucket_is_not_half_of_the_personas():
    # the slot of the "GeForce GTX 980" bucket used to weigh 47%: it is every NVIDIA family
    nv = next(e for e in _POOL if e["prefs"]["zoom.stealth.webgl.renderer"].startswith("ANGLE (NVIDIA"))
    covered = sum(w for f, w in _TABLE.items() if f in {d["pool_family"] for d in _DEVICES})
    nv_weight = sum(w for f, w in _TABLE.items()
                    if f.startswith("nv_") and f in {d["pool_family"] for d in _DEVICES})
    assert abs(nv["prob"] - nv_weight / covered) < 0.001
    assert nv["prob"] < 0.25


def test_the_old_devices_are_gone():
    labels = {d["label"] for d in _DEVICES}
    names = {d["windows_name"] for d in _DEVICES}
    # a real Radeon HD 3200 has no Windows 11 driver: the bucket of that name is the one the
    # Radeon iGPUs of the table fall in, and no device in the pool is that card
    assert "hd3200_9001" not in labels
    assert not [n for n in names if "HD 3200" in n]
    # the GTX 980 is there only at the weight of its family
    gtx980 = next(d for d in _DEVICES if d["label"] == "gtx980_58278")
    assert gtx980["pool_family"] == "nv_maxwell" and abs(gtx980["weight"] - 0.12) <= 0.01


def test_the_draw_follows_the_weights_per_family():
    covered = sum(d["weight"] for d in _DEVICES)
    fam = {d["label"]: d["pool_family"] for d in _DEVICES}
    want = collections.defaultdict(float)
    for d in _DEVICES:
        want[d["pool_family"]] += d["weight"] / covered
    n = 30000
    got = collections.Counter()
    for seed in range(n):
        p = W.select_persona(seed)
        got[fam[W.choose_gfx_device(p, seed)["label"]]] += 1
    for family, w in want.items():
        assert abs(got[family] / n - w) < 0.012, (family, got[family] / n, w)
