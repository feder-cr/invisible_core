r"""A launch never registers its binary to start when Windows signs in.

Firefox 157 added DefaultLaunchOnLogin: on the first run of an official build
with a new profile it waits for Nimbus and, where a rollout asks for it, writes
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Mozilla-Firefox-<hash>`
with `"<path>\firefox.exe" -os-autostart`. Every launch here is a first run on
a new profile, so every binary that ever ran registered itself, and the next
sign-in started all of them. A browser driven by a program does not enrol
itself in the user's start-up; a page cannot see it either, so the product's
defect is the machine's, not the page's.

The core declares `browser.startup.windowsLaunchOnLogin.alreadyApplied`, the
record that the first-run decision was taken, which is the first thing the
module reads (before it waits for Nimbus). The unit halves are
`test_prefs.test_a_launch_never_registers_its_binary_to_start_at_login` and
`test_a_launch_keeps_the_first_run_record_in_the_profile`; this is the half
that looks at the registry after a real launch, on a first run: the same binary
from a path no Firefox has run from, which is what a new build is.

Measured on 159 (2026-11-11), so that nobody reads more into this test than it
holds: a launch through `juggler.instance.launch` did not write the key even
with the record removed and the rollout's switch on, while a plain
`firefox -headless -profile <dir>` of a new install did. This test is the
guard on the observable outcome; the pref is what keeps the plain launch, the
one that does not go through the core, from writing it.

Needs a Windows host, a hidden desktop and a firefox binary:
`INVPW_BINARY_PATH=<firefox.exe>`.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(
    sys.platform != "win32", reason="the Run key is a Windows registry key")]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
#: The module runs once the browser is idle and, with the pref off, waits for
#: Nimbus's first fetch.
SETTLE_S = 45.0


def _binary() -> str:
    path = os.environ.get("INVPW_BINARY_PATH", "")
    if not path or not os.path.isfile(path):
        pytest.skip("INVPW_BINARY_PATH names no firefox binary")
    return path


def _run_values() -> dict:
    import winreg
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                out[name] = value
                i += 1
    except OSError:
        pass
    return out


def _delete_run_value(name: str) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.DeleteValue(key, name)


def _registered_by(binary: str, before: dict) -> dict:
    """The Mozilla-Firefox-* values that appeared since `before` and point at
    `binary`."""
    wanted = os.path.normcase(os.path.abspath(binary))
    wanted_real = os.path.normcase(os.path.realpath(binary))
    found = {}
    for name, value in _run_values().items():
        if name in before or not name.startswith("Mozilla-Firefox-"):
            continue
        if any(w in os.path.normcase(str(value)) for w in (wanted, wanted_real)):
            found[name] = value
    return found


def _link_or_copy(src: str, dst: str) -> None:
    try:
        os.link(src, dst)
    except OSError:  # another volume: a copy is the same file for this test
        shutil.copy2(src, dst)


def _a_new_install(binary: str, tmp_path) -> str:
    """The same binary under a path no Firefox has run from.

    The module acts on a FIRST RUN, and Firefox is on its first run when the
    install (the hash of the directory the binary runs from) is not yet in
    `installs.ini`. A binary that already ran on this machine is not, and the
    check would then pass for a reason that is not the pref. A directory of
    hard links is a new install at no cost (a junction is not: Firefox sees
    through it to the install it points at, and the run is not a first one)."""
    root = tmp_path / "install"
    shutil.copytree(Path(binary).parent, root, copy_function=_link_or_copy)
    return str(root / Path(binary).name)


def _launch_and_watch(binary: str, tmp_path) -> dict:
    """Launches `binary` the way the core does, on a first run, and returns
    what it registered."""
    from invisible_core import build_launch_env
    from invisible_core._fpforge import generate_profile
    from invisible_core.juggler.instance import launch
    from invisible_core.prefs import compose_session_prefs

    binary = _a_new_install(binary, tmp_path)
    before = _run_values()
    profile = generate_profile(seed=20261111)
    prefs = dict(compose_session_prefs(
        profile, locale="en-US", timezone="America/New_York").prefs)
    # `-headless`: no window, so nothing on anyone's desktop.
    browser = launch(binary, prefs=prefs, profile_dir=str(tmp_path / "p"),
                     env=build_launch_env(prefs), headless=True,
                     ready_timeout=120)
    try:
        deadline = time.monotonic() + SETTLE_S
        while time.monotonic() < deadline:
            if _registered_by(binary, before):
                break
            time.sleep(1.0)
    finally:
        browser.close()
    # The browser is gone: whatever it wrote is final.
    found = _registered_by(binary, before)
    for name in found:
        _delete_run_value(name)
    shutil.rmtree(tmp_path / "install", ignore_errors=True)
    return found


def test_a_launch_registers_nothing_to_start_at_sign_in(tmp_path):
    binary = _binary()
    found = _launch_and_watch(binary, tmp_path)
    assert not found, (
        "a launch wrote a start-at-sign-in entry for its binary (removed): %r"
        % found)
