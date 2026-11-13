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
DIAGNOSTICS = ("MOZ_LOG", "MOZ_LOG_FILE", "MOZ_LOG_MODULES", "MOZ_UPLOAD_DIR", "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS")

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
    env = build_launch_env({}, base_env={}, manifest_path="/m.json",
                           display_env={"DISPLAY": ":9", "WAYLAND_DISPLAY": None})
    written = {k for k in env if k.upper().startswith("MOZ_")}
    assert written == set(SET_BY_THE_CORE), sorted(written)


def test_the_registry_declares_exactly_these_lists():
    assert environment.MOZ_SET_BY_THE_CORE == SET_BY_THE_CORE
    assert environment.MOZ_DIAGNOSTICS == DIAGNOSTICS
    assert environment.TOOLKIT_SET_BY_THE_CORE == ("GDK_BACKEND",)


# --- the GL and toolkit families of Linux: the same rule ------------------------------------------

#: What drives the GL and the toolkit on a Linux host, and a page can read through them: the scale
#: (devicePixelRatio), the theme (system colors), the GL stack the toolkit maps at startup.
HOST_TOOLKIT = {
    "MESA_GL_VERSION_OVERRIDE": "4.5",
    "MESA_EXTENSION_OVERRIDE": "-GL_EXT_texture_filter_anisotropic",
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "LIBGL_DRIVERS_PATH": "/opt/host-mesa",
    "GALLIUM_DRIVER": "zink",
    "LP_NUM_THREADS": "1",
    "__GL_THREADED_OPTIMIZATIONS": "1",
    "__EGL_VENDOR_LIBRARY_FILENAMES": "/opt/host/10_nvidia.json",
    "EGL_PLATFORM": "wayland",
    "DRI_PRIME": "1",
    "GDK_SCALE": "2",
    "GDK_DPI_SCALE": "1.5",
    "GTK_THEME": "Adwaita:dark",
    "GTK_USE_PORTAL": "1",
    "GTK_IM_MODULE": "ibus",
}


@pytest.mark.parametrize("name", sorted(HOST_TOOLKIT))
def test_a_gl_or_toolkit_variable_of_the_host_does_not_reach_the_engine(name, monkeypatch):
    monkeypatch.setenv(name, HOST_TOOLKIT[name])
    env = build_launch_env({})
    assert name not in env, "%s=%s of the host reached the engine" % (name, HOST_TOOLKIT[name])


def test_the_toolkit_families_are_dropped_from_a_callers_environment_too():
    env = build_launch_env({}, base_env={"PATH": "/usr/bin", "HOME": "/home/x", **HOST_TOOLKIT})
    assert not [k for k in env if k in HOST_TOOLKIT], sorted(k for k in env if k in HOST_TOOLKIT)
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/x", "only the declared families are filtered"


def test_the_one_toolkit_variable_the_core_writes_is_written_over_the_hosts():
    env = build_launch_env({}, base_env={"GDK_BACKEND": "wayland"})
    assert env["GDK_BACKEND"] == "x11"


def test_the_display_of_a_session_is_not_a_family_variable():
    """DISPLAY and XAUTHORITY are the session's surface (`display_env`), not the host's."""
    env = build_launch_env({}, base_env={"DISPLAY": ":0"}, display_env={"DISPLAY": ":99", "XAUTHORITY": "/tmp/x"})
    assert env["DISPLAY"] == ":99" and env["XAUTHORITY"] == "/tmp/x"


# --- the three a page reads, by name ---------------------------------------------------------------

def test_the_scale_of_the_host_does_not_reach_the_engine():
    """GDK_SCALE and GDK_DPI_SCALE set the scale GTK reports, so `devicePixelRatio` and every CSS length."""
    env = build_launch_env({}, base_env={"GDK_SCALE": "2", "GDK_DPI_SCALE": "1.5"})
    assert "GDK_SCALE" not in env and "GDK_DPI_SCALE" not in env, env


def test_the_theme_of_the_host_does_not_reach_the_engine():
    """GTK_THEME decides the system colors (`CSS system colors`, `prefers-color-scheme`) of a GTK engine."""
    env = build_launch_env({}, base_env={"GTK_THEME": "Adwaita:dark"})
    assert "GTK_THEME" not in env, env


# --- the zone and the language of the host: declared by the core, never inherited -------------------

HOST_LOCALE = {"TZ": "Asia/Tokyo", "LANG": "ja_JP.UTF-8", "LC_ALL": "ja_JP.UTF-8", "LC_CTYPE": "ja_JP.UTF-8",
               "LC_TIME": "ja_JP.UTF-8", "LANGUAGE": "ja:zh_CN:ko"}


@pytest.mark.parametrize("name", sorted(HOST_LOCALE))
def test_the_zone_and_the_language_of_the_host_do_not_reach_the_engine(name, monkeypatch):
    monkeypatch.setenv(name, HOST_LOCALE[name])
    env = build_launch_env({})
    assert name not in env, "%s=%s of the host reached the engine" % (name, HOST_LOCALE[name])


def test_the_core_writes_the_language_of_the_session_over_the_hosts():
    env = build_launch_env({"intl.locale.requested": "it-IT"}, base_env=dict(HOST_LOCALE))
    assert env["LANG"] == env["LC_ALL"] == "it_IT.UTF-8"
    assert env["LANGUAGE"] == "it"
    assert "TZ" not in env and "LC_CTYPE" not in env and "LC_TIME" not in env


@pytest.mark.parametrize("tag, posix, language", [
    ("en-US", "en_US.UTF-8", "en"),
    ("ja-JP", "ja_JP.UTF-8", "ja"),
    ("zh-TW", "zh_TW.UTF-8", "zh"),
    ("pt_BR", "pt_BR.UTF-8", "pt"),
    ("es-419", "es_419.UTF-8", "es"),
    ("de", "de.UTF-8", "de"),
    ("sr-Latn-RS", "sr_RS.UTF-8", "sr"),
])
def test_the_language_of_the_session_as_the_machine_declares_it(tag, posix, language):
    env = build_launch_env({"intl.locale.requested": tag}, base_env={})
    assert (env["LANG"], env["LC_ALL"], env["LANGUAGE"]) == (posix, posix, language)


def test_a_session_that_declares_no_language_writes_none_and_inherits_none():
    env = build_launch_env({}, base_env=dict(HOST_LOCALE, PATH="/usr/bin"))
    assert not [k for k in env if k.upper() in HOST_LOCALE or k.upper() in ("LANG", "LC_ALL", "LANGUAGE")], env
    assert env["PATH"] == "/usr/bin"


def test_a_name_that_only_starts_like_the_zone_is_not_the_zone():
    env = build_launch_env({}, base_env={"TZDIR": "/usr/share/zoneinfo", "LANGEXT": "x"})
    assert env["TZDIR"] == "/usr/share/zoneinfo" and env["LANGEXT"] == "x"
