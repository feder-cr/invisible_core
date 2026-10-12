"""A context's locale reaches the engine as the language LIST it stands for.

`Browser.setLocaleOverride` becomes the BrowsingContext's LanguageOverride on
an engine that applies a context's locale: the field navigator.languages is
split from, the realm's Intl locale is taken from and the Accept-Language
header is prepared from. The list is the core's
`decide_session_locale(tag).accept_languages`, so a context asking for de-DE
reports the four entries a German Firefox reports, not `["de-DE"]`.

These lived twice until 41.37.0, once in each client's copy of the browser
module; the module and its tests are here now, once.
"""
from __future__ import annotations

import pytest

from invisible_core import decide_session_locale
from invisible_core.juggler.instance import Browser
from invisible_core.juggler.connection import EventListeners

pytestmark = pytest.mark.unit


class _Connection(EventListeners):
    def __init__(self):
        super().__init__()
        self.sent = []

    def startup_send(self, method, params=None):
        return self.send(method, params, timeout=None)

    def send(self, method, params=None, session=None, timeout=30):
        self.sent.append((method, params))
        return {}


def _locale_commands(options):
    conn = _Connection()
    Browser(conn, "151.0").apply_context_options("CTX", options)
    return [p for m, p in conn.sent if m == "Browser.setLocaleOverride"]


@pytest.mark.parametrize("locale", ["de-DE", "it-IT", "en-US", "en-GB", "pt_BR"])
def test_the_locale_goes_out_as_the_core_list(locale):
    assert _locale_commands({"locale": locale}) == [
        {"browserContextId": "CTX",
         "locale": decide_session_locale(locale).accept_languages}]


def test_de_de_is_the_list_a_german_firefox_reports():
    # Written out once, so a change in the core's table shows up here as a
    # decision rather than passing through silently.
    assert _locale_commands({"locale": "de-DE"})[0]["locale"] == (
        "de-DE, de, en-US, en")


def test_the_default_context_gets_the_list_too():
    """The session's launch locale arrives here, addressed by absence."""
    conn = _Connection()
    Browser(conn, "151.0").apply_context_options(None, {"locale": "it-IT"})
    assert [p for m, p in conn.sent if m == "Browser.setLocaleOverride"] == [
        {"locale": "it-IT, it, en-US, en"}]


def test_no_locale_sends_nothing():
    """The context then keeps the session's list, seeded by the engine."""
    assert _locale_commands({}) == []


def test_a_context_locale_of_auto_is_refused_without_the_network(monkeypatch):
    """"auto" is decided once, at launch, from the egress. This side does not
    know the proxy, so deciding it here would discover the HOST's address and
    pair the home country's language with the proxy's timezone. Known-bad:
    the browser module calling the core's launch decision instead of
    SessionLocale.of."""
    from invisible_core import _geo

    def _no_network(*_a, **_k):
        raise AssertionError("a context locale must not discover anything")
    monkeypatch.setattr(_geo, "discover_egress_ip", _no_network)
    with pytest.raises(ValueError, match="auto"):
        _locale_commands({"locale": "auto"})


@pytest.mark.parametrize("requested", [
    "en-AU", "en-ZA", "en-CA", "fr-BE", "de-CH", "he-IL", "ja-JP", "zh-CN",
    "zh-TW", "nb-NO", "ro-RO", "pt-BR", "es-MX", "sl-SI"])
def test_the_default_context_gets_back_the_list_the_profile_declared(requested):
    """primary -> the engine browser -> the engine must land on the launch list."""
    session = decide_session_locale(requested)
    assert _locale_commands({"locale": session.primary}) == [
        {"browserContextId": "CTX", "locale": session.accept_languages}]
