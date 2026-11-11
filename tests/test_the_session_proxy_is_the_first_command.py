"""The session's proxy is the FIRST command sent to the engine, on the pipe, before anything else.

The engine's Juggler holds every proxied channel until it has been told, on the pipe, what the session's proxy is: either
``Browser.setBrowserProxy`` (with a proxy) or the explicit ``Browser.setNoBrowserProxy`` (no proxy). The core sends one of
the two before any other command. The credentials travel on the pipe only: never in the browser's environment (readable by
other processes of the user), never on its command line.
"""
from __future__ import annotations

import json

import pytest

from invisible_core.juggler import connection
from test_a_launch_has_one_deadline import PROXY, _Browser, _launch

pytestmark = pytest.mark.unit

_WITH_AUTH = {"type": "http", "host": "10.0.0.1", "port": 8080, "username": "u-secret", "password": "p-secret", "bypass": []}


def test_a_proxy_is_the_first_command(monkeypatch):
    browser = _Browser()
    c = _launch(monkeypatch, browser, ready_timeout=3.0, proxy=PROXY)
    try:
        c.startup_send("Browser.enable", {})
    finally:
        c.close()
    assert browser.methods[:2] == ["Browser.setBrowserProxy", "Browser.enable"], browser.methods


def test_no_proxy_is_said_explicitly_and_first(monkeypatch):
    browser = _Browser()
    c = _launch(monkeypatch, browser, ready_timeout=3.0)
    try:
        c.startup_send("Browser.enable", {})
    finally:
        c.close()
    assert browser.methods[:2] == ["Browser.setNoBrowserProxy", "Browser.enable"], browser.methods


def test_the_credentials_are_on_the_pipe_and_nowhere_else(monkeypatch):
    seen = {}
    browser = _Browser()
    real_spawn = browser.spawn

    def spawn(executable, argv, env):
        seen["argv"], seen["env"] = list(argv), dict(env)
        return real_spawn(executable, argv, env)

    monkeypatch.setattr(connection, "_spawn_posix", spawn)
    monkeypatch.setattr(connection, "_spawn_windows", spawn)
    c = connection.launch("firefox", "profile", ready_timeout=3.0, proxy=_WITH_AUTH)
    try:
        pass
    finally:
        c.close()
    assert not [a for a in seen["argv"] if "secret" in a], seen["argv"]
    assert not [k for k, v in seen["env"].items() if "secret" in v and k not in ("PYTEST_CURRENT_TEST",)], "an environment value carries a credential"
    assert not [k for k in seen["env"] if "PROXY" in k.upper() and "SESSION" in k.upper()], "a session-proxy variable exists"


def test_a_proxy_the_engine_refuses_closes_the_browser(monkeypatch):
    browser = _Browser(refuses={"Browser.setBrowserProxy"})
    with pytest.raises(connection.ProtocolError, match="refused the proxy"):
        _launch(monkeypatch, browser, ready_timeout=3.0, proxy=PROXY)
    assert browser.process.poll() is not None, "the browser was left running"
