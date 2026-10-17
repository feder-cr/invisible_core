"""The install command is spelled by the tool that built the environment.

`invisible_core.installer` reads the INSTALLER record and every remedy the core
prints, and the pin repair it runs, take their command from it. Measured on
2026-10-17 in a project `uv init` + `uv add` made: no pip in the venv, INSTALLER
`uv`, and a pin repair that died on `No module named pip` and then told the user
to run pip (`installer.py` has the numbers).

Both directions are asserted, because the fix must not trade one broken
population for another: an environment uv built gets uv, and one pip built still
gets pip, which is what every user of 0.37.0 and before has.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from invisible_core import installer
from invisible_core import pin as _pin
from invisible_core.installer import UV, install, installer_of, runnable

CORE = "invisible-core"


def _dist(site: Path, name: str, record: "str | None") -> None:
    """A dist-info importlib.metadata reads, with the INSTALLER record a real
    installer writes (or none at all)."""
    d = site / f"{name.replace('-', '_')}-1.0.0.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n", encoding="utf-8")
    if record is not None:
        (d / "INSTALLER").write_text(record, encoding="utf-8")


# ── the record decides ──────────────────────────────────────────────────────

@pytest.mark.parametrize("record, tool", [
    ("uv\n", UV),
    ("pip\n", "pip"),
    ("UV", UV),                  # what decides is the word, not its case
    ("  uv  \n", UV),
    ("poetry\n", "pip"),         # another installer: pip, as it always was
    (None, "pip"),               # no record at all: what it always got
])
def test_the_installer_record_decides_the_tool(tmp_path, monkeypatch, record, tool):
    _dist(tmp_path, "probe-dist", record)
    monkeypatch.syspath_prepend(str(tmp_path))
    assert installer_of("probe-dist") == tool


def test_a_distribution_that_is_not_installed_gets_the_pip_form():
    """A source tree on the path has no record. It gets the command it got
    before 49.45.0, never an exception from inside an error message."""
    assert installer_of("no-such-distribution-anywhere") == "pip"


def test_uv_installs_into_this_interpreter_through_uv(monkeypatch):
    monkeypatch.setattr(installer, "installer_of", lambda dist: UV)
    assert install(CORE, "--force-reinstall", f"{CORE}==1.0.0") == [
        UV, "pip", "install", "--python", sys.executable,
        "--force-reinstall", f"{CORE}==1.0.0"]


def test_pip_installs_into_this_interpreter_through_its_own_pip(monkeypatch):
    """`<this interpreter> -m pip`, never a bare `pip`: a bare one is whatever a
    shell finds first, which is not necessarily the environment that broke."""
    monkeypatch.setattr(installer, "installer_of", lambda dist: "pip")
    assert install(CORE, "-e", "/src/core") == [
        sys.executable, "-m", "pip", "install", "-e", "/src/core"]


# ── running uv needs its executable ─────────────────────────────────────────

def test_a_pip_command_is_run_as_it_is():
    cmd = [sys.executable, "-m", "pip", "install", "x"]
    assert runnable(cmd) == cmd


def test_uv_is_found_through_UV_first(monkeypatch):
    """`uv run` puts the path of the uv that is running in $UV."""
    monkeypatch.setenv("UV", "/opt/uv/bin/uv")
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/elsewhere/uv")
    assert runnable([UV, "pip", "install", "x"]) == [
        "/opt/uv/bin/uv", "pip", "install", "x"]


def test_uv_is_found_on_PATH_without_UV(monkeypatch):
    """A shell with the venv activated: uv's installer put it on PATH."""
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/home/u/.local/bin/uv")
    assert runnable([UV, "pip", "install", "x"]) == [
        "/home/u/.local/bin/uv", "pip", "install", "x"]


def test_no_uv_anywhere_is_no_command_rather_than_a_pip_one(monkeypatch):
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    assert runnable([UV, "pip", "install", "x"]) is None


def test_the_seam_runs_uv_through_the_executable_it_found(monkeypatch):
    """A real process, started through the resolved executable: $UV names this
    interpreter, so `uv -c ...` runs Python, and the outcome speaks of uv."""
    monkeypatch.setenv("UV", sys.executable)
    outcome = _pin._run_installer([UV, "-c", "raise SystemExit(0)"], execute=True)
    assert outcome.ok is True, outcome
    assert outcome.detail == "uv exited 0", outcome
    failed = _pin._run_installer([UV, "-c", "raise SystemExit(4)"], execute=True)
    assert failed.ok is False and failed.detail.startswith("uv exited 4"), failed


def test_the_seam_without_uv_fails_with_the_reason_and_runs_nothing(monkeypatch):
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    started = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: started.append(a))
    outcome = _pin._run_installer([UV, "pip", "install", "x"], execute=True)
    assert outcome.ok is False
    assert "$UV" in outcome.detail and "PATH" in outcome.detail, outcome.detail
    assert started == [], "a process was started without a uv to start"


# ── every remedy the core prints comes from the installer ───────────────────

def _remedies() -> dict:
    """Every message of the core that ends in an install command, as the user
    would read it."""
    from invisible_core import seal
    from invisible_core._geo import _remedy
    from invisible_core.download import _upgrade_commands

    return {
        "pin: plain mismatch": _pin.pin_problem(
            dist_name="invisible-playwright", want="18.0.0", have="19.0.0",
            recorded="19.0.0"),
        "pin: stale record": _pin.pin_problem(
            dist_name="invisible-playwright", want="18.0.0", have="19.0.0",
            recorded="18.0.0"),
        "pin: editable": _pin.pin_problem(
            dist_name="invisible-playwright", want="18.0.0", have="19.0.0",
            editable_path="/src/core"),
        "seal: upgrade": seal.core_install_hint(),
        "seal: reinstall": seal.core_reinstall_hint(),
        "geo: PySocks": _remedy("socks_missing", True),
        "download: retired engine": "\n".join(_upgrade_commands()),
    }


def test_in_an_environment_uv_built_every_remedy_is_uv(monkeypatch):
    monkeypatch.setattr(installer, "installer_of", lambda dist: UV)
    for name, text in _remedies().items():
        assert f"uv pip install --python" in text, f"{name}: {text}"
        assert "-m pip install" not in text, f"{name}: {text}"


def test_in_an_environment_pip_built_every_remedy_is_still_pip(monkeypatch):
    monkeypatch.setattr(installer, "installer_of", lambda dist: "pip")
    for name, text in _remedies().items():
        assert "-m pip install" in text, f"{name}: {text}"
        assert "uv pip" not in text, f"{name}: {text}"


def test_the_remedy_table_keeps_no_unfilled_placeholder():
    from invisible_core import _geo

    for kind in _geo._REMEDY:
        for proxied in (True, False):
            assert "{install}" not in _geo._remedy(kind, proxied), kind
