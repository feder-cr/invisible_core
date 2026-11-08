"""A page made by the core's own `launch` types on the persona's keyboards.

Until 58.55.0 only the Playwright wrapper handed its pages the persona's
keyboard (a server-only pref its server read). A client that drives the core
directly - a CDP server, the Selenium and Puppeteer adapters - got pages from
`instance.Page`, whose hands were built with no layout: every persona typed on
the US keyboard whatever its language, and a Russian persona never pressed the
switch to English before a Latin letter. Now the core declares the keyboards
once, with the language and country (`prefs._apply_locale`), and `launch`
takes them out of the prefs like the seed and gives them to every page.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from invisible_core._locale import SessionLocale
from invisible_core.juggler import instance as module
from invisible_core.juggler.connection import EventListeners
from invisible_core.juggler.instance import launch
from invisible_core.juggler.keyboard import KEYBOARD_LAYOUT_PREF
from invisible_core.prefs import _apply_locale

pytestmark = pytest.mark.unit


class _Conn(EventListeners):
    def __init__(self):
        super().__init__()
        self.keys = []

    def startup_send(self, method, params=None):
        return {}

    def send(self, method, params=None, session=None, timeout=30):
        if method == "Page.dispatchKeyEvent":
            self.keys.append((params["type"], params["code"]))
        return {}

    def close(self):
        pass


@pytest.fixture
def russian_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    conn = _Conn()
    monkeypatch.setattr(module.connection, "launch", lambda *a, **kw: conn)
    prefs = {}
    _apply_locale(prefs, SessionLocale(("ru-RU", "ru", "en-US", "en"), "RU"))
    browser = launch("x", prefs=prefs, env={})
    yield browser, conn
    browser.close()


def test_the_core_declares_the_keyboards_with_the_language_and_country():
    prefs = {}
    _apply_locale(prefs, SessionLocale(("de", "en-US", "en"), "CH"))
    assert prefs[KEYBOARD_LAYOUT_PREF] == "00000807,0000100C"


def test_launch_takes_the_keyboards_out_of_the_profile(russian_browser):
    browser, _ = russian_browser
    assert browser.layout == "00000419,00000409"
    user_js = pathlib.Path(browser._files.profile, "user.js").read_text(encoding="utf-8")
    assert KEYBOARD_LAYOUT_PREF not in user_js


def test_a_page_of_a_russian_persona_types_x_after_the_switch(russian_browser, monkeypatch):
    browser, conn = russian_browser

    class _Lifecycle:
        def __init__(self, *a, **kw):
            pass

        def wait_for_main_frame(self, timeout=None):
            return "main"

    class _Injected:
        def __init__(self, *a, **kw):
            pass

        def install(self):
            pass

    monkeypatch.setattr(module, "Lifecycle", _Lifecycle)
    monkeypatch.setattr(module, "InjectedScript", _Injected)
    context = module.Context.__new__(module.Context)
    context.browser = browser  # its `conn` is the browser's
    page = module.Page(context, "S1", "T1")
    page.actions.keyboard.press("x")
    assert conn.keys == [("keydown", "AltLeft"), ("keydown", "ShiftLeft"),
                         ("keyup", "ShiftLeft"), ("keyup", "AltLeft"),
                         ("keydown", "KeyX"), ("keyup", "KeyX")]
