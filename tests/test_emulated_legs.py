"""A Windows ARM64 host gets the x86_64 engine, by declaration.

The runtime half, that the engine really starts under emulation, is the
`arm64-host` job of ci.yml on a `windows-11-arm` runner. This half pins what
the declaration does and does not do, on every runner.
"""
from __future__ import annotations

import pytest

from dataclasses import replace

from invisible_core.seal import EMULATED_LEGS, RETIRED_LEGS, SUPPORTED_LEGS, Asset, active_seal


def test_a_windows_arm64_host_gets_the_x86_64_asset():
    """Refused with NotImplementedError until 2026-09-25, with no remedy."""
    seal = active_seal()
    if seal.is_local:
        pytest.skip("packaged seal is a LOCAL seal with no assets")
    asset = seal.asset_for("win32", "ARM64")
    assert (asset.platform, asset.arch) == ("win32", "x86_64"), asset


def _seal_with(*legs):
    """A seal with exactly these (platform, arch) assets, the rest of it the packaged one's."""
    assets = {
        f"firefox-x-{p}-{a}": Asset(name=f"firefox-x-{p}-{a}", platform=p, arch=a, sha256="0" * 64,
                                    size=1, entry_rel="firefox", omni_sha256="")
        for p, a in legs}
    return replace(active_seal(), tag="firefox-56", assets=assets)


def test_a_native_asset_wins_over_the_emulated_one():
    """The declaration is for hosts WITHOUT an asset, not a preference for x86_64:
    a native one, if a seal carries it, wins."""
    seal = _seal_with(("win32", "x86_64"), ("win32", "arm64"))
    asset = seal.asset_for("win32", "ARM64")
    assert (asset.platform, asset.arch) == ("win32", "arm64"), asset


def test_an_old_seal_with_a_linux_arm64_asset_still_gives_it():
    """linux-arm64 left on 2026-11-04 (firefox-56): the seals up to firefox-55 carry
    it, and a host that runs one of them keeps getting it."""
    seal = _seal_with(("linux", "x86_64"), ("linux", "arm64"), ("win32", "x86_64"))
    asset = seal.asset_for("linux", "aarch64")
    assert (asset.platform, asset.arch) == ("linux", "arm64"), asset


def test_a_linux_arm64_host_on_a_seal_without_it_is_told_where_to_stay():
    """From firefox-56 no Linux arm64 engine is published. Such a host is not sent
    the x86_64 one (Linux does not run it), and is told the last release with its
    engine and the command that keeps it, not just "no asset"."""
    assert SUPPORTED_LEGS == (("linux", "x86_64"), ("win32", "x86_64"))
    assert RETIRED_LEGS == {("linux", "arm64"): "firefox-55"}
    seal = _seal_with(("linux", "x86_64"), ("win32", "x86_64"))
    with pytest.raises(NotImplementedError) as info:
        seal.asset_for("linux", "aarch64")
    said = str(info.value)
    assert "firefox-55" in said and "invisible-core<56" in said, said


def test_the_declaration_does_not_invent_a_leg_nobody_measured():
    assert EMULATED_LEGS == {("win32", "arm64"): "x86_64"}
    with pytest.raises(NotImplementedError):
        active_seal().asset_for("darwin", "arm64")
