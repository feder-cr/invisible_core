"""No `MOZ_*` variable of the host reaches the engine, except the declared lists.

The known-bad input: `MOZ_DISABLE_SOCKET_PROCESS=1` in the shell that starts the library. The prefs
of a session are locked, so a pref cannot move ICE out of the socket process, but the environment of
the engine was the host's whole environment (`launch.build_launch_env` started from `os.environ`), and
the engine reads that variable at startup. A value that changes what a page can read came from the host.

The environment of the engine is composed in ONE place (`build_launch_env`), and the filter is there,
before the core writes its own variables. What may pass of the `MOZ_` family is two tuples in
`invisible_core.environment`: what the core sets itself, and the engine's diagnostics (the log). The
same rule holds for `base_env`, the environment a caller hands in, and for a name spelled in another
case (Windows environments are case-insensitive).
"""
from __future__ import annotations

import pytest

from invisible_core import environment
from invisible_core.launch import build_launch_env

pytestmark = pytest.mark.unit

#: The declared lists, spelled here too: the test states the contract, the registry must agree with it.
SET_BY_THE_CORE = ("MOZ_ENABLE_WAYLAND", "MOZ_CRASHREPORTER_DISABLE")
DIAGNOSTICS = ("MOZ_LOG", "MOZ_LOG_FILE", "MOZ_LOG_MODULES", "MOZ_UPLOAD_DIR")

#: Variables of the engine that change what a page reads, or how the browser is built around it.
HOST_MOZ = {
    "MOZ_DISABLE_SOCKET_PROCESS": "1",
    "MOZ_FORCE_DISABLE_E10S": "1",
    "MOZ_FORCE_DISABLE_FISSION": "1",
    "MOZ_DISABLE_CONTENT_SANDBOX": "1",
    "MOZ_ENABLE_WIN32K": "1",
    "MOZ_WINDOW_OCCLUSION": "0",
    "MOZ_GL_PROVIDER": "GLX",
    "MOZ_X11_EGL": "1",
    "MOZ_HEADLESS": "1",
    "MOZ_DISABLE_NONLOCAL_CONNECTIONS": "1",
    "MOZ_FORCE_QUIC_ON": "example.test",
    "MOZ_GFX_SPOOF_GL_VENDOR": "Host Vendor",
}


@pytest.mark.parametrize("name", sorted(HOST_MOZ))
def test_a_moz_variable_of_the_host_does_not_reach_the_engine(name, monkeypatch):
    monkeypatch.setenv(name, HOST_MOZ[name])
    env = build_launch_env({})                  # starts from os.environ, as every launch does
    assert name not in env, "%s=%s of the host reached the engine" % (name, HOST_MOZ[name])


def test_the_same_holds_for_the_environment_a_caller_hands_in():
    env = build_launch_env({}, base_env={"PATH": "/usr/bin", **HOST_MOZ})
    assert not [k for k in env if k in HOST_MOZ], sorted(k for k in env if k in HOST_MOZ)
    assert env["PATH"] == "/usr/bin", "only the MOZ_ family is filtered here"


def test_a_name_in_another_case_is_the_same_name():
    env = build_launch_env({}, base_env={"moz_disable_socket_process": "1", "Moz_Force_Disable_E10s": "1"})
    assert not [k for k in env if k.upper().startswith("MOZ_") and k.upper() not in SET_BY_THE_CORE + DIAGNOSTICS], env


@pytest.mark.parametrize("name", DIAGNOSTICS)
def test_the_diagnostics_pass(name):
    env = build_launch_env({}, base_env={name: "x"})
    assert env[name] == "x"


def test_what_the_core_sets_is_set_over_the_hosts_value():
    env = build_launch_env({}, base_env={"MOZ_ENABLE_WAYLAND": "1", "MOZ_CRASHREPORTER_DISABLE": "0"})
    assert env["MOZ_ENABLE_WAYLAND"] == "0"
    assert env["MOZ_CRASHREPORTER_DISABLE"] == "1"


def test_every_moz_variable_the_core_writes_is_in_the_declared_list():
    """The list of what the core sets is what the core really sets: nothing else of the family leaves it."""
    env = build_launch_env({}, base_env={}, srflx_declared="203.0.113.9", manifest_path="/m.json",
                           display_env={"DISPLAY": ":9", "WAYLAND_DISPLAY": None})
    written = {k for k in env if k.upper().startswith("MOZ_")}
    assert written == set(SET_BY_THE_CORE), sorted(written)


def test_the_registry_declares_exactly_these_lists():
    assert environment.MOZ_SET_BY_THE_CORE == SET_BY_THE_CORE
    assert environment.MOZ_DIAGNOSTICS == DIAGNOSTICS
