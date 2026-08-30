"""The same seed must produce the same prefs whichever entry point built them.

Three entry points compose a session's prefs on top of the same fingerprint, and
until 2026-08-01 each added its own layers in its own order:

    build_launch_plan          proxy, crash prefs        (direct launch)
    get_default_stealth_prefs  humanize                  (public API)
    _session.build_prefs       cloak, humanize           (invisible-playwright)

Nothing compared the results. The measured consequence was that a caller using
`get_default_stealth_prefs` with a SOCKS proxy got a prefs dict containing no
`network.proxy.*` pref at all - the auth prefs the patched binary reads were
simply absent, and the session went out on the host's own address.

This file is what makes a fourth divergence fail rather than ship. Two of the
three entry points live here; the third is in the wrapper's repository and pins
this package by exact version, so its arm lands after this core is published.
"""
from __future__ import annotations

import json
import re

import pytest

import invisible_core._geo as _geo
import invisible_core.download as _dl
from invisible_core import get_default_stealth_prefs
from invisible_core._geo import SessionGeo
from invisible_core.launch import build_launch_plan, write_user_js
from invisible_core.prefs import (DEFAULT_SHOW_CURSOR, compose_session_prefs,
                                  humanize_prefs)

pytestmark = pytest.mark.unit

SEED = 424242
LOCALE = "en-US"
TZ = "America/New_York"
#: RFC 5737 documentation address; never routed anywhere.
PROXY = {"server": "socks5://198.51.100.9:1080", "username": "u", "password": "p"}

_LINE = re.compile(r'^user_pref\((".*?"), (.*)\);$')


def read_user_js(path):
    """Parse a user.js back into {key: raw literal}.

    Compared as WRITTEN, not as built. `write_user_js` is where a float becomes a
    quoted string and a bool becomes `true`, so two dicts that differ only in
    type would look equal before serialization and different after it - and the
    file is what Firefox reads.
    """
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line)
        assert m, f"unparseable user.js line: {line}"
        out[json.loads(m.group(1))] = m.group(2)
    return out


@pytest.fixture
def no_network(monkeypatch):
    """Everything build_launch_plan reaches out for, and nothing else.

    The fingerprint, the pref translation and the composition all run for real:
    stubbing those would make this file compare two mocks.
    """
    monkeypatch.setattr(_dl, "ensure_binary", lambda ver=None: "/fake/firefox")
    monkeypatch.setattr(_geo, "prepare_session_geo",
                        lambda tz, proxy: SessionGeo(TZ, "198.51.100.4"))
    monkeypatch.setattr(_geo, "resolve_session_locale", lambda ip, proxy: LOCALE)


#: What the two paths are ALLOWED to differ by, and why. Asserted as an exact
#: set: a new divergence has to be added here deliberately, with a reason, or the
#: test fails. A subset check would pass while the list grew back.
DELIBERATE = {
    # direct launch only: a persistent profile can be hard-killed, and Firefox
    # would offer Safe Mode / session restore on the next start.
    "toolkit.startup.max_resumed_crashes",
    "browser.sessionstore.resume_from_crash",
    # public API only: it targets a caller driving Playwright themselves, who
    # has no trajectory generator of their own, so the binary's is left on.
    "stealthfox.humanize",
    "stealthfox.humanize.maxTime",
    # Terzo fratello, stessa ragione dei due sopra: governa la cadenza del
    # generatore DEL BINARIO, che solo il percorso pubblico accende. Prima non
    # era dichiarato da nessuno e il motore usava il proprio default compilato
    # di 10 ms, che misurato dava il 79% degli intervalli piu' fitti di quanto
    # un 60 Hz reale possa consegnare.
    "stealthfox.humanize.stepMs",
}


def test_the_two_core_paths_compose_the_same_prefs(tmp_path, no_network):
    # ⛔ SENZA PROXY DA ENTRAMBI I LATI dal 2026-08-30, e non e' una rinuncia:
    # il lancio diretto adesso RIFIUTA un proxy, perche' non tiene una
    # connessione su cui mandare il comando del motore ed era l'unico percorso
    # rimasto a inventarsi prefs di instradamento sue. Che rifiuti lo prova
    # `test_il_lancio_diretto_RIFIUTA_un_proxy_invece_di_inventarsi_una_strada`
    # in `tests/test_proxy.py`; qui si confronta cio' che i due percorsi
    # compongono, che e' un'altra domanda e vale senza proxy come con.
    plan = build_launch_plan(SEED, profile_dir=tmp_path / "direct",
                             timezone=TZ, locale=LOCALE)
    direct = read_user_js(plan.profile_dir / "user.js")

    api = get_default_stealth_prefs(SEED, locale=LOCALE, timezone=TZ)
    public = read_user_js(write_user_js(tmp_path / "api", api))

    only_direct = set(direct) - set(public)
    only_public = set(public) - set(direct)
    assert (only_direct | only_public) == DELIBERATE, (
        f"the two paths diverged.\n  only in build_launch_plan: {sorted(only_direct)}"
        f"\n  only in get_default_stealth_prefs: {sorted(only_public)}"
        f"\nIf a new one is deliberate, add it to DELIBERATE with the reason.")

    differing = {k: (direct[k], public[k])
                 for k in set(direct) & set(public) if direct[k] != public[k]}
    assert not differing, (
        "the two paths agree on which prefs exist and disagree on their VALUES, "
        f"which is worse than a missing key because nothing looks wrong: {differing}")


def test_the_public_api_declares_the_leak_prefs_and_NO_endpoint(tmp_path):
    """Turned inside out on 2026-08-30, and the inversion is the point.

    This used to assert that a SOCKS endpoint became `network.proxy.*`. That
    was one of three roads to one fact, and the scheme picked between them;
    when the Node driver went, the HTTP road stopped existing and a proxy was
    accepted and dropped. Routing is now the engine command for every scheme,
    so prefs naming an endpoint are exactly what must NOT appear here.

    What the composer still owes a proxied session is the part that was never
    routing: the channels a proxy cannot cover.
    """
    prefs = get_default_stealth_prefs(SEED, proxy=PROXY)
    endpoint = {"network.proxy.type", "network.proxy.socks",
                "network.proxy.socks_port", "network.proxy.socks_username",
                "network.proxy.socks_password", "network.proxy.http",
                "network.proxy.http_port", "network.proxy.ssl",
                "network.proxy.ssl_port"}
    assert not (endpoint & set(prefs)), sorted(endpoint & set(prefs))
    assert prefs["network.proxy.allow_bypass"] is False
    assert prefs["zoom.stealth.dns.no_local_resolution"] is True
    assert prefs["zoom.stealth.webrtc.no_direct_udp"] is True


def test_no_proxy_still_means_no_endpoint_prefs():
    """The other half. A proxy layer that fires unasked would put a session
    behind an endpoint the caller never named.

    Asserted on the keys that NAME an endpoint, not on the `network.proxy.`
    prefix: `socks_remote_dns` and `failover_direct` are part of the baseline
    fingerprint and are set whether or not a proxy is in play. Written the broad
    way first, and those two are why it went red.
    """
    prefs = get_default_stealth_prefs(SEED)
    endpoint = {"network.proxy.type", "network.proxy.socks",
                "network.proxy.socks_port", "network.proxy.socks_username",
                "network.proxy.socks_password", "network.proxy.http",
                "network.proxy.ssl"}
    assert not (endpoint & set(prefs))


# ── the composer's own layers ────────────────────────────────────────────────

def _profile():
    from invisible_core import generate_profile
    return generate_profile(SEED)


def test_extra_prefs_beat_the_cloak_and_humanize_beats_extra_prefs():
    """setdefault vs update is the precedence each layer already had.

    Swapping either is invisible in every test that checks one layer at a time,
    and it changes what a caller's extra_prefs can reach.
    """
    from invisible_core._headless import cloak_prefs

    cloak_key = next(iter(cloak_prefs()))
    composed = compose_session_prefs(
        _profile(),
        extra_prefs={cloak_key: "mine", "stealthfox.humanize": "mine"},
        cloak=True, humanize=True,
    ).prefs
    assert composed[cloak_key] == "mine", "the cloak overwrote an explicit override"
    assert composed["stealthfox.humanize"] is True, (
        "humanize did not win over extra_prefs, so a caller could leave both "
        "trajectory generators live and have every waypoint expanded again")


def test_humanize_none_is_not_humanize_false():
    """None leaves the prefs unwritten, False writes the pref off. The direct
    launch path has always left them alone, and the binary has its own default:
    never writing a pref is not the same as writing it false."""
    off = compose_session_prefs(_profile(), humanize=False).prefs
    untouched = compose_session_prefs(_profile(), humanize=None).prefs
    assert off["stealthfox.humanize"] is False
    assert "stealthfox.humanize" not in untouched


@pytest.mark.parametrize("value,expected", [
    (True, "1.5"),
    (2.5, "2.5"),
    ("fast", "1.5"),     # raised ValueError out of get_default_stealth_prefs
    (-1, "1.5"),         # wrote maxTime = "-1.0" into the profile
])
def test_any_humanize_value_produces_a_usable_cap(value, expected):
    assert humanize_prefs(value)["stealthfox.humanize.maxTime"] == expected


def test_the_hard_kill_prefs_can_be_overridden_by_the_caller():
    """setdefault, not assignment: a caller pinning either pref means to."""
    composed = compose_session_prefs(
        _profile(), survive_hard_kill=True,
        extra_prefs={"toolkit.startup.max_resumed_crashes": 3},
    ).prefs
    assert composed["toolkit.startup.max_resumed_crashes"] == 3


def test_the_visible_cursor_is_declared_by_every_core_path_and_defaults_on():
    """On by default since 2026-08-28, and DECLARED rather than left to the
    engine.

    The engine reads `getBoolPref(PREF, false)`, so an absent pref would mean
    OFF - the opposite of the shipped default now, which is exactly why the
    declaration is asserted rather than assumed. A compiled default and a
    declaration are two places that know the same thing, and the project has
    already paid for that shape (the taskbar geometry had four). Here there is
    one: `DEFAULT_SHOW_CURSOR`, both core paths say it identically, and the
    engine only obeys.
    """
    public = get_default_stealth_prefs(SEED, locale=LOCALE, timezone=TZ)
    assert public["stealthfox.showcursor"] is DEFAULT_SHOW_CURSOR
    assert DEFAULT_SHOW_CURSOR is True, (
        "the shipped default moved; that is a decision, so move this "
        "assertion deliberately rather than to make a suite green")

    silent = compose_session_prefs(_profile()).prefs
    assert silent["stealthfox.showcursor"] is DEFAULT_SHOW_CURSOR

    off = get_default_stealth_prefs(SEED, locale=LOCALE, timezone=TZ,
                                    show_cursor=False)
    assert off["stealthfox.showcursor"] is False, (
        "an explicit False must still win: None means 'did not say', and a "
        "layer that collapses the two takes the switch away from the caller")

    asked = get_default_stealth_prefs(SEED, locale=LOCALE, timezone=TZ,
                                      show_cursor=True)
    assert asked["stealthfox.showcursor"] is True

    # And the two core paths agree on the VALUE, not only on the key - which is
    # what `test_the_two_core_paths_compose_the_same_prefs` above compares.
    composed = compose_session_prefs(_profile(), show_cursor=True).prefs
    assert composed["stealthfox.showcursor"] is True


def test_the_visible_cursor_is_independent_of_humanize():
    """Two switches, not one, and no code may derive either from the other.

    `humanize` decides WHO draws the path; `show_cursor` decides whether the
    chrome window paints a dot on top of it. Humanize off with the dot on is a
    legitimate session - it is how you watch a teleporting cursor - and a
    derivation would quietly forbid it.
    """
    both = compose_session_prefs(_profile(), humanize=False,
                                 show_cursor=True).prefs
    assert both["stealthfox.humanize"] is False
    assert both["stealthfox.showcursor"] is True
