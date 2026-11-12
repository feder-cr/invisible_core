"""The environment of the engine's process is born filtered, whoever built the environment handed to the launch.

`launch.build_launch_env` filters the host's variables for the callers that start the binary themselves, but the
served road does not go through it: the wrapper's server hands `dict(os.environ)`, or the environment a client
named, to `SessionFiles` and from there to `juggler.connection.launch`, which started the process with that, as it came.
So `MOZ_DISABLE_SOCKET_PROCESS` and `GDK_SCALE` of the host, or of a client of the shelf, reached the engine on that road.
The composition is at the lowest place (`_engine_env.compose`, called by `connection.launch` right before the spawn):
whatever `env` carries of the host's families does not reach the process, and the core's own is written over it.

The spawn is the seam: it is replaced by a function that records the environment it is given and stops the launch.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler import connection

pytestmark = pytest.mark.unit

HOST = {
    "MOZ_DISABLE_SOCKET_PROCESS": "1",
    "MOZ_FORCE_DISABLE_FISSION": "1",
    "GDK_SCALE": "2",
    "GTK_THEME": "Adwaita:dark",
    "MESA_GL_VERSION_OVERRIDE": "4.5",
    "TZ": "Asia/Tokyo",
    "LANG": "ja_JP.UTF-8",
    "LC_ALL": "ja_JP.UTF-8",
    "LANGUAGE": "ja:zh_CN:ko",
}


class _Stop(Exception):
    pass


@pytest.fixture
def spawned(monkeypatch):
    """The environment the process would have been born with."""
    seen = {}

    def spawn(executable, argv, env):
        seen["env"] = dict(env)
        raise _Stop()

    monkeypatch.setattr(connection, "_spawn_posix", spawn)
    monkeypatch.setattr(connection, "_spawn_windows", spawn)

    def run(**kwargs):
        with pytest.raises(_Stop):
            connection.launch("/bin/firefox", "/tmp/profile", **kwargs)
        return seen["env"]

    return run


def test_the_host_of_this_process_does_not_reach_the_engine(spawned, monkeypatch):
    for name, value in HOST.items():
        monkeypatch.setenv(name, value)
    env = spawned()                       # env=None: the process's own, as a bare launch uses it
    assert not [k for k in HOST if k in env and k not in ("LANG", "LC_ALL", "LANGUAGE")], sorted(env)
    assert "LANG" not in env and "LC_ALL" not in env and "LANGUAGE" not in env, "no language declared, none written"


def test_the_environment_a_client_names_does_not_reach_the_engine(spawned):
    env = spawned(env=dict(HOST, PATH="/usr/bin", DISPLAY=":99"))
    assert not [k for k in HOST if k in env], sorted(env)
    assert env["PATH"] == "/usr/bin" and env["DISPLAY"] == ":99", "only the declared families go"


def test_the_core_writes_its_own_over_the_clients(spawned):
    env = spawned(env={"GDK_BACKEND": "wayland", "MOZ_ENABLE_WAYLAND": "1", "MOZ_CRASHREPORTER_DISABLE": "0",
                       "LANG": "ja_JP.UTF-8"}, locale="it-IT")
    assert env["GDK_BACKEND"] == "x11"
    assert env["MOZ_ENABLE_WAYLAND"] == "0"
    assert env["MOZ_CRASHREPORTER_DISABLE"] == "1"
    assert (env["LANG"], env["LC_ALL"], env["LANGUAGE"]) == ("it_IT.UTF-8", "it_IT.UTF-8", "it")


def test_the_diagnostics_of_the_study_still_reach_the_engine(spawned):
    env = spawned(env={"MOZ_LOG": "nsHttp:5", "MOZ_LOG_FILE": "/tmp/x.log"})
    assert env["MOZ_LOG"] == "nsHttp:5" and env["MOZ_LOG_FILE"] == "/tmp/x.log"


def test_composing_twice_is_composing_once():
    """`build_launch_env` composes and `connection.launch` composes again on its output: the second changes nothing."""
    from invisible_core import _engine_env
    from invisible_core.launch import build_launch_env

    once = build_launch_env({"intl.locale.requested": "de-CH"}, base_env=dict(HOST, PATH="/usr/bin"))
    assert _engine_env.compose(once, "de-CH") == once
