"""The browser never opens its crash reporter, whatever the caller's environment says.

The engine refuses a launch whose declarations are incomplete, and the refusal ends in an abort
on purpose. With the crash reporter on, Windows starts crashreporter.exe, which puts a "Crash
Reporter" window on the desktop of whoever runs the browser and waits there for a click: 313 of
them in one e2e run of the wrapper. The launch environment is composed in one place
(`build_launch_env`), so the variable that turns the reporter off is written there.

The unit tests read the environment the launcher builds. The e2e launches the real engine with a
declaration removed (the engine refuses it on its first use) and asks that no crash reporter
process lives below the test afterwards.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from invisible_core.launch import build_launch_env, build_launch_plan

NAME = "MOZ_CRASHREPORTER_DISABLE"


@pytest.mark.unit
def test_the_launch_environment_turns_the_crash_reporter_off():
    assert build_launch_env({}, base_env={})[NAME] == "1"


@pytest.mark.unit
@pytest.mark.parametrize("hosts", ["0", "", "no"])
def test_what_the_caller_s_environment_says_does_not_turn_it_on(hosts):
    assert build_launch_env({}, base_env={NAME: hosts})[NAME] == "1"


@pytest.mark.unit
def test_a_launch_plan_carries_it(tmp_path):
    plan = build_launch_plan(6023, profile_dir=str(tmp_path), binary_path=str(tmp_path / "firefox"),
                             timezone="UTC", locale="en-US")
    assert plan.env[NAME] == "1"


# ── the real engine ─────────────────────────────────────────────────────────

#: A declaration the engine's gate requires on every platform and every version of the series, and
#: that the core always writes: with it missing, the first page refuses the launch.
UNDECLARED = "zoom.stealth.hw_concurrency"


def _binary() -> str:
    path = os.environ.get("INVPW_BINARY_PATH") or os.environ.get("STEALTHFOX_E2E_BINARY")
    if not path or not Path(path).exists():
        pytest.skip("no engine binary given (INVPW_BINARY_PATH)")
    return path


def _reporters():
    import psutil
    found = []
    for p in psutil.process_iter(["name"]):
        try:
            if "crashreporter" in (p.info["name"] or "").lower():
                found.append(p)
        except psutil.Error:
            continue
    return found


@pytest.mark.e2e
def test_an_engine_that_refuses_a_launch_leaves_no_crash_reporter(monkeypatch):
    from invisible_core.juggler import connection as conn

    # Whatever the shell running the tests exports must not stand in for the launcher's own value.
    monkeypatch.delenv(NAME, raising=False)
    binary = _binary()
    before = {p.pid for p in _reporters()}
    profile = tempfile.mkdtemp(prefix="no_crash_reporter_")
    refusal = None
    try:
        plan = build_launch_plan(6023, profile_dir=profile, binary_path=binary,
                                 timezone="UTC", locale="en-US")
        user_js = Path(profile) / "user.js"
        kept, dropped = [], 0
        for line in user_js.read_text(encoding="utf-8").splitlines():
            m = re.match(r'user_pref\(("(?:[^"\\]|\\.)*"),', line)
            if m and json.loads(m.group(1)) == UNDECLARED:
                dropped += 1
            else:
                kept.append(line)
        assert dropped == 1, "%s is not declared by this core, so there is nothing to remove" % UNDECLARED
        user_js.write_text("\n".join(kept) + "\n", encoding="utf-8")

        c = conn.launch(binary, profile, headless=True, env=plan.env, ready_timeout=60.0)
        try:
            c.send("Browser.enable", {"attachToDefaultContext": True})
            c.send("Browser.newPage", {})
        except Exception as exc:                      # the refusal: the pipe closes on the abort
            refusal = str(exc)
        finally:
            c.close()
        assert refusal is not None, "the engine accepted a launch with %s removed" % UNDECLARED
        assert UNDECLARED in refusal and "is not declared" in refusal, refusal

        # The reporter, if it were going to start, starts within a second or two of the abort.
        deadline = time.time() + 6.0
        started = []
        while time.time() < deadline and not started:
            started = [p for p in _reporters() if p.pid not in before]
            time.sleep(0.25)
        for p in started:                             # never Quit or Restart: that sends a report
            try:
                p.kill()
            except Exception:
                pass
        assert not started, "the refusal opened %d crash reporter process(es)" % len(started)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
