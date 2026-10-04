"""The session language is decided ONCE, and every value reads the decision.

KNOWN-BAD, measured 2026-10-04: an Australian egress resolved the tag "en-AU",
while the engine's table, keyed by LANGUAGE like Firefox's own (there is no
en-AU build), turns it into "en-US, en". The prefs then wrote
`general.useragent.locale` and `intl.locale.requested` from the TAG and
`intl.accept_languages` / `juggler.locale.override` from the LIST, so
`navigator.language` said en-US while the requested locale said en-AU: two
answers to one question, both computed in this package. Fifteen of the
country table's tags were in that state (every en-* but GB/CA/US, fr-*, cs,
sk, hr, sl, he, ja).
"""
import ast
import pathlib

import pytest

import invisible_core
from invisible_core import _geo
from invisible_core._fpforge import generate_profile
from invisible_core._locale import SessionLocale, decide_session_locale
from invisible_core.prefs import compose_session_prefs, translate_profile_to_prefs

pytestmark = pytest.mark.unit

SOCKS = {"server": "socks5://gw.example:1080"}


def _no_network(monkeypatch):
    """Any discovery is a failure of the test: these cases must not ask."""
    def refuse(*a, **kw):
        raise AssertionError("the network was asked")
    monkeypatch.setattr(_geo, "discover_egress_ip", refuse)


def _egress_country_tag(monkeypatch, tag, *, ip="203.0.113.7"):
    """Discovery answers `ip`, the database maps it to `tag`. Counts asks."""
    import invisible_core.download as dl

    asked = {"discover": 0, "locale": 0}

    def discover(proxy=None, **kw):
        asked["discover"] += 1
        return ip

    def to_locale(got_ip, mmdb):
        asked["locale"] += 1
        assert got_ip == ip
        return tag

    monkeypatch.setattr(_geo, "discover_egress_ip", discover)
    monkeypatch.setattr(_geo, "ip_to_locale", to_locale)
    monkeypatch.setattr(_geo, "ip_to_timezone", lambda ip, mmdb: "Australia/Sydney")
    monkeypatch.setattr(_geo, "ip_to_coordinates", lambda ip, mmdb: (-33.8, 151.2))
    monkeypatch.setattr(dl, "ensure_geoip_mmdb", lambda *a, **kw: "fake.mmdb")
    return asked


# ──────────────────────────────────────────────────────────────────────
#  The known-bad: one question, one answer
# ──────────────────────────────────────────────────────────────────────

def test_every_tag_shaped_pref_agrees_with_the_list_navigator_reports():
    """Red on 35.32.0: general.useragent.locale was "en-AU", the list en-US."""
    prefs = translate_profile_to_prefs(generate_profile(seed=7), locale="en-AU")
    first = prefs["intl.accept_languages"].split(",")[0].strip()
    assert first == prefs["juggler.locale.override"].split(",")[0].strip()
    assert prefs["general.useragent.locale"] == first, prefs["general.useragent.locale"]
    assert prefs["intl.locale.requested"] == first, prefs["intl.locale.requested"]


def test_an_australian_egress_is_one_language_end_to_end(monkeypatch):
    """The real path: egress -> geo decision -> prefs, with no tag in between."""
    _egress_country_tag(monkeypatch, "en-AU")
    geo = _geo.prepare_session_geo("auto", SOCKS, "auto")
    assert geo.locale.primary == "en-US"
    assert geo.locale.accept_languages == "en-US, en"
    assert geo.locale.region == "AU"

    prefs = compose_session_prefs(generate_profile(seed=7), locale=geo.locale,
                                  timezone=geo.timezone).prefs
    assert prefs["general.useragent.locale"] == "en-US"
    assert prefs["intl.locale.requested"] == "en-US"
    assert prefs["intl.accept_languages"] == "en-US, en"
    assert prefs["juggler.locale.override"] == "en-US, en"


def test_a_tag_and_its_decision_give_the_same_prefs():
    """One derivation, not two: handing the tag or the decision is the same."""
    profile = generate_profile(seed=11)
    for tag in ("en-AU", "fr-BE", "it-IT", "pt_BR", "ja-JP", "", None):
        by_tag = translate_profile_to_prefs(profile, locale=tag)
        by_decision = translate_profile_to_prefs(
            profile, locale=decide_session_locale(tag))
        assert by_tag == by_decision, tag


# ──────────────────────────────────────────────────────────────────────
#  The one decision: explicit tags and "auto" go through the same table
# ──────────────────────────────────────────────────────────────────────

def test_an_explicit_tag_goes_through_the_table_and_asks_nothing(monkeypatch):
    _no_network(monkeypatch)
    loc = decide_session_locale("en-AU")
    assert loc == SessionLocale(("en-US", "en"), "AU")
    assert loc.primary == "en-US"
    assert decide_session_locale("it-IT").languages == ("it-IT", "it", "en-US", "en")
    # A requested tag is not written through when the table starts elsewhere.
    assert decide_session_locale("fr-FR").primary == "fr"
    assert decide_session_locale("fr-FR").region == "FR"


def test_auto_is_resolved_from_the_egress_and_then_the_same_table(monkeypatch):
    asked = _egress_country_tag(monkeypatch, "en-AU")
    loc = decide_session_locale("auto", egress_ip="203.0.113.7", proxy=SOCKS)
    assert loc == decide_session_locale("en-AU"), (
        "auto must land on exactly what the explicit tag lands on")
    assert asked == {"discover": 0, "locale": 1}, (
        "the egress the caller handed in must be reused, not rediscovered")


def test_auto_without_a_proxy_discovers_when_nothing_is_handed_in(monkeypatch):
    asked = _egress_country_tag(monkeypatch, "it-IT")
    loc = decide_session_locale("auto")
    assert loc.primary == "it-IT" and loc.region == "IT"
    assert asked["discover"] == 1


def test_auto_behind_a_proxy_with_no_egress_falls_back_and_never_goes_direct(
        monkeypatch, capsys):
    """The home country's language next to the proxy's timezone is worse than
    en-US, so a missing egress behind a proxy is never discovered directly."""
    asked = _egress_country_tag(monkeypatch, "it-IT")
    loc = decide_session_locale("auto", proxy=SOCKS)
    assert loc == decide_session_locale("en-US")
    assert asked["discover"] == 0
    assert "behind a proxy" in capsys.readouterr().err


@pytest.mark.parametrize("asked", [None, ""])
def test_nobody_asking_is_en_US(monkeypatch, asked):
    _no_network(monkeypatch)
    assert decide_session_locale(asked).accept_languages == "en-US, en"


def test_a_pure_builder_refuses_auto():
    """It used to write the language "auto" into the prefs verbatim."""
    with pytest.raises(ValueError, match="auto"):
        translate_profile_to_prefs(generate_profile(seed=1), locale="auto")


def test_an_empty_language_list_is_not_a_session_locale():
    with pytest.raises(ValueError):
        SessionLocale(())


def test_the_primary_of_every_country_decides_the_same_list():
    """The wrapper hands a context the session's `primary` back; deciding it
    again must give the very list the launch declared, for every country."""
    from invisible_core._geo import _COUNTRY_LOCALE

    for cc, tag in _COUNTRY_LOCALE.items():
        decided = decide_session_locale(tag)
        assert decide_session_locale(decided.primary).languages == decided.languages, cc
        assert decided.region == cc, (cc, decided.region)


# ──────────────────────────────────────────────────────────────────────
#  The geo decision carries the language
# ──────────────────────────────────────────────────────────────────────

def test_prepare_session_geo_decides_the_language_with_one_discovery(monkeypatch):
    asked = _egress_country_tag(monkeypatch, "de-AT")
    geo = _geo.prepare_session_geo("auto", None)
    assert geo.locale == decide_session_locale("de-AT")
    assert asked["discover"] == 1, "one fact, one round trip"


def test_prepare_session_geo_takes_an_explicit_language_without_a_lookup(monkeypatch):
    asked = _egress_country_tag(monkeypatch, "de-AT")
    geo = _geo.prepare_session_geo("auto", SOCKS, "pt-BR")
    assert geo.locale == decide_session_locale("pt-BR")
    assert asked["locale"] == 0


def test_a_failed_discovery_is_not_asked_again_for_the_language(monkeypatch, capsys):
    """Without a proxy a failed discovery keeps the host timezone, and the
    language falls back at once, naming the failure, instead of asking the
    network the same question a second time in the same launch."""
    count = {"n": 0}

    def boom(proxy=None, **kw):
        count["n"] += 1
        raise _geo.GeoTimezoneError("offline for the test")

    monkeypatch.setattr(_geo, "discover_egress_ip", boom)
    geo = _geo.prepare_session_geo("auto", None)
    assert geo.timezone == ""
    assert geo.locale == decide_session_locale("en-US")
    assert count["n"] == 1
    err = capsys.readouterr().err
    assert "could not resolve the session locale" in err
    assert "offline for the test" in err, "the cause must be named"


# ──────────────────────────────────────────────────────────────────────
#  Exactly one place applies the table
# ──────────────────────────────────────────────────────────────────────

def _code_references(name):
    """Modules whose CODE (not comments, not docstrings) names `name`."""
    pkg = pathlib.Path(invisible_core.__file__).resolve().parent
    found = set()
    for path in sorted(pkg.rglob("*.py")):
        tree = ast.parse(path.read_bytes().decode("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == name:
                found.add(path.name)
            elif isinstance(node, ast.Attribute) and node.attr == name:
                found.add(path.name)
            elif isinstance(node, ast.alias) and node.name == name:
                found.add(path.name)
    return found


def test_the_table_is_applied_in_one_module_only():
    """Known-bad: prefs.py calling the table on a raw tag again, which is the
    shape of the defect (two derivations of one fact). An import of
    `_language_list` anywhere else turns this red."""
    assert _code_references("_language_list") == {"_locale.py"}
    assert _code_references("_ACCEPT_LANG_TABLE") == {"_locale.py"}


def test_auto_is_resolved_in_one_module_only():
    """The egress-country tag is an input to the decision, read nowhere else."""
    assert _code_references("_egress_locale_tag") <= {"_locale.py", "_geo.py"}
    assert "resolve_session_locale" not in dir(invisible_core)


# ──────────────────────────────────────────────────────────────────────
#  The table itself (moved from test_prefs.py with the table, AL1-AL3)
# ──────────────────────────────────────────────────────────────────────

def _list(tag):
    return decide_session_locale(tag).accept_languages


def test_accept_language_with_region():
    # AL1
    assert _list("en-US") == "en-US, en"


def test_accept_language_no_region():
    """AL2. A language without a region does NOT stay a single tag.

    This test used to assert the list for "fr" was "fr" and encoded the defect
    corrected on 2026-08-19, not Firefox's behaviour. The expected value below
    is DERIVED from the engine's table, not from what our code returns:

        intl/locale/rust/locale_service_glue/src/lib.rs
          "fr" => "fr, fr-FR",          <- the table's row
          add_en_us stays true          <- so ", en-US, en" goes on the end

    Note the first tag is the BARE language and not `fr-FR`: the table wants it
    that way, and it is the reason a requested region may not come first.
    """
    assert _list("fr") == "fr, fr-FR, en-US, en"


def test_accept_language_no_region_when_the_table_has_no_row():
    """AL2-bis. With no dedicated row and no region the engine returns the
    language alone and then appends en-US. `ja` is the row that is exactly
    "ja", so it exercises both roads to the same outcome."""
    assert _list("ja") == "ja, en-US, en"


def test_accept_language_underscore_normalized():
    """AL3. `pt` has no table row, so it falls into the `_` branch with a
    region present: "pt-BR, pt", plus ", en-US, en"."""
    assert _list("pt_BR") == "pt-BR, pt, en-US, en"


def test_accept_language_english_does_not_append_itself():
    """AL3-bis. For `en` the engine sets `add_en_us = false`. It is the only
    locale where the old two-entry form coincided with the right one, which is
    why a check on en-US alone let the defect through for months."""
    assert _list("en-US") == "en-US, en"
    assert _list("en-GB") == "en-GB, en"
    assert _list("en-CA") == "en-CA, en-US, en"
    # A NON-English case that also refuses the tail: "sl" => add_en_us = false.
    assert _list("sl") == "sl, en-GB, en"
