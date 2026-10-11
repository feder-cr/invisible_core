"""`SessionFiles`: the directories one browser session writes, and their end.

The three clients made and removed the profile each in its own way, and all
three left it behind (B223); none gave the browser a temporary directory of its
own, so the system one filled up (B267).
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

import psutil
import pytest

from invisible_core._owned_dirs import sweep_owned_dirs
from invisible_core.juggler import SessionFiles, _profile
from invisible_core.juggler._profile import PROFILE_PREFIX, TMP_PREFIX
from invisible_core.process import TOKEN_VAR, SessionToken, terminate

pytestmark = pytest.mark.unit


def test_a_session_directory_carries_its_owner_s_pid():
    """The pid is what lets a later session tell a directory whose owner is
    gone from one in use; without it the 6,091 profiles of B223 could only be
    cleaned by hand."""
    files = SessionFiles(env={})
    try:
        for path, prefix in ((files.profile, PROFILE_PREFIX), (files.tmp, TMP_PREFIX)):
            assert pathlib.Path(path).name.startswith(f"{prefix}{os.getpid()}_"), path
    finally:
        files.remove()


def test_the_sweep_removes_only_what_a_gone_process_made(tmp_path):
    """Dead pid: removed. This process, a live pid (reused or not): kept. A
    name without a pid, from before the pid was part of it: never touched. A
    file is not a directory."""
    reused = 99_999_999
    alive = lambda pid: pid in (os.getpid(), reused)  # noqa: E731
    gone = tmp_path / f"{PROFILE_PREFIX}4242_dead"
    (gone / "saved-telemetry-pings").mkdir(parents=True)
    (gone / "saved-telemetry-pings" / "ping").write_bytes(b"{}")
    gone_tmp = tmp_path / f"{TMP_PREFIX}4242_dead"
    gone_tmp.mkdir()
    kept = [tmp_path / f"{PROFILE_PREFIX}{os.getpid()}_mine",
            tmp_path / f"{TMP_PREFIX}{reused}_theirs",
            tmp_path / f"{PROFILE_PREFIX}oldform",
            tmp_path / "unrelated_4242_x"]
    for k in kept:
        k.mkdir()
    afile = tmp_path / f"{PROFILE_PREFIX}4242_file"
    afile.write_bytes(b"x")

    removed = sweep_owned_dirs((PROFILE_PREFIX, TMP_PREFIX), str(tmp_path), alive=alive)

    assert sorted(removed) == sorted([str(gone), str(gone_tmp)])
    assert not gone.exists() and not gone_tmp.exists()
    assert all(k.is_dir() for k in kept), [k for k in kept if not k.is_dir()]
    assert afile.exists()


def test_a_new_session_sweeps_what_a_killed_owner_left(tmp_path, monkeypatch):
    """⛔ KNOWN-BAD until this: a SIGKILLed owner left its 23 MB profile and
    nothing ever looked at it again (B268). The owner here is a real process
    that has exited; what it "left" is a directory named with its pid."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    owner = subprocess.Popen([sys.executable, "-c", "pass"])
    owner.wait()
    left = tmp_path / f"{PROFILE_PREFIX}{owner.pid}_left"
    left.mkdir()
    (left / "places.sqlite").write_bytes(b"x")
    files = SessionFiles(env={})
    try:
        assert not left.exists(), "the profile of a gone owner was not swept"
        assert pathlib.Path(files.profile).parent == tmp_path
    finally:
        files.remove()
    assert os.listdir(tmp_path) == []


def test_the_sweep_never_raises(tmp_path):
    assert sweep_owned_dirs((PROFILE_PREFIX,), str(tmp_path / "missing")) == []


def test_a_session_makes_its_profile_and_a_temporary_directory_named_three_times():
    """The browser's temporary directory is the same under the three names the
    two platforms read, inside this process's own, and the rest of the given
    environment arrives untouched."""
    files = SessionFiles(env={"PATH": "x"})
    profile, tmp = pathlib.Path(files.profile), pathlib.Path(files.tmp)
    try:
        assert files.owns_profile
        assert profile.is_dir() and tmp.is_dir()
        here = pathlib.Path(tempfile.gettempdir())
        assert profile.parent == here and tmp.parent == here, (
            "the session's directories belong inside this process's, where the "
            "caller's own TMP put them")
        assert files.env == {"PATH": "x", "TMP": str(tmp), "TEMP": str(tmp),
                             "TMPDIR": str(tmp)}
    finally:
        files.remove()
    assert not profile.exists(), "the profile outlived the session"
    assert not tmp.exists(), "the browser's temporary files outlived the session"


def test_no_environment_means_this_process_s_with_the_temporary_directory():
    files = SessionFiles()
    try:
        assert files.env["PATH"] == os.environ["PATH"]
        assert files.env["TMP"] == files.tmp
    finally:
        files.remove()


def test_a_profile_the_caller_named_survives_and_the_temporary_directory_does_not(tmp_path):
    """A directory we invented is ours to remove; a profile the caller named
    is theirs and must survive the session."""
    (tmp_path / "places.sqlite").write_bytes(b"x")
    files = SessionFiles(profile_dir=str(tmp_path), env={})
    assert not files.owns_profile
    files.remove()
    assert (tmp_path / "places.sqlite").exists(), "the CALLER's profile was deleted"
    assert not pathlib.Path(files.tmp).exists()


def test_removing_twice_is_harmless_and_without_a_token_no_process_is_looked_for():
    files = SessionFiles(env={})
    assert not files.token, "no token in the environment, so nothing is ours"
    files.remove()
    files.remove()
    assert not pathlib.Path(files.profile).exists()


def test_the_ping_sender_is_let_finish_before_the_profile_goes(tmp_path):
    """⛔ KNOWN-BAD, measured on both platforms (B266): `remove()` ended every
    survivor at once, and the survivor is the shutdown-ping sender, so its
    POST was lost or cut in half. A real child stands in for it: it carries the
    session's token, holds the ping, and after a second deletes it and writes a
    marker OUTSIDE the profile, which it reaches only by running to the end.

    The marker, not the exit code: on POSIX psutil reaps the child while it
    waits for it, and `Popen.wait()` then answers 0 for a process that was
    ended as well."""
    token = SessionToken.mint()
    files = SessionFiles(env=dict(os.environ, **{TOKEN_VAR: token.value}))
    ping = pathlib.Path(files.profile, "saved-telemetry-pings", "ping")
    ping.parent.mkdir()
    ping.write_bytes(b"{}")
    done = tmp_path / "sent"
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import os, sys, time\n"
         "print('sending', flush=True)\n"
         "time.sleep(1)\n"
         "os.remove(sys.argv[1])\n"
         "open(sys.argv[2], 'w').close()\n", str(ping), str(done)],
        env=files.env, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "sending"
        files.remove()
        assert done.exists(), "the sender was ended instead of being let finish"
        assert not pathlib.Path(files.profile).exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        files.remove()


def test_terminate_does_not_count_what_exits_within_the_grace(tmp_path):
    """The quick one writes its marker at the end of its own run; the slow one
    would write it only after 60 s. Markers, not exit codes, for the reason in
    the test above."""
    quick_done, slow_done = tmp_path / "quick", tmp_path / "slow"
    code = "import sys, time; time.sleep(float(sys.argv[1])); open(sys.argv[2], 'w').close()"
    quick = subprocess.Popen([sys.executable, "-c", code, "0.5", str(quick_done)])
    slow = subprocess.Popen([sys.executable, "-c", code, "60", str(slow_done)])
    try:
        procs = [psutil.Process(quick.pid), psutil.Process(slow.pid)]
        assert terminate(procs, grace=5.0, timeout=5.0) == 1, (
            "only the one still running after the grace is sent anything")
        assert quick_done.exists(), "the quick one was ended, not waited for"
        slow.wait(timeout=5)
        assert not slow_done.exists(), (
            "the slow one outlived the grace and was not ended")
    finally:
        for p in (quick, slow):
            if p.poll() is None:
                p.kill()
                p.wait()


def test_a_child_that_outlives_the_browser_is_ended_before_the_profile_goes(monkeypatch):
    """⛔ KNOWN-BAD, measured on Windows: at exit the browser hands its shutdown
    ping to `pingsender.exe`, a child that outlives it with the ping file inside
    the profile open. The removal failed on that file, the child then deleted
    it, and the profile stayed with an empty `saved-telemetry-pings`. Here the
    child is real and carries the session's token, as the browser's children
    do; on Windows its open file is what makes the removal fail, on Linux
    deleting an open file succeeds and the test still asks that the child was
    ended. The grace a sender gets (B266) is shortened: this child never
    finishes, and what is asked here is that it is ended after it."""
    monkeypatch.setattr(_profile, "SENDER_GRACE", 0.5)
    token = SessionToken.mint()
    files = SessionFiles(env=dict(os.environ, **{TOKEN_VAR: token.value}))
    assert files.token == token
    ping = pathlib.Path(files.profile, "saved-telemetry-pings", "ping")
    ping.parent.mkdir()
    ping.write_bytes(b"{}")
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time\n"
         "f = open(sys.argv[1], 'rb')\n"
         "print('holding', flush=True)\n"
         "time.sleep(60)\n", str(ping)],
        env=files.env, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "holding"
        files.remove()
        child.wait(timeout=10)
        assert not pathlib.Path(files.profile).exists(), (
            "the profile stayed behind a child of the browser that held a file")
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        files.remove()
