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


def _ip_picking(country, build):
    """An address of `country` whose pick is `build`: the language now depends
    on the address, so a test that wants one build has to choose its IP."""
    from invisible_core._locale import _country_build

    for i in range(1, 1 << 20):
        ip = f"10.{(i >> 16) & 255}.{(i >> 8) & 255}.{i & 255}"
        if _country_build(country, ip) == build:
            return ip
    raise AssertionError(f"no address of {country} picks {build}")


def _egress_country_is(monkeypatch, country, *, ip="203.0.113.7"):
    """Discovery answers `ip`, the database maps it to `country`. Counts asks."""
    import invisible_core.download as dl

    asked = {"discover": 0, "locale": 0}

    def discover(proxy=None, **kw):
        asked["discover"] += 1
        return ip

    def to_country(got_ip, mmdb):
        asked["locale"] += 1
        assert got_ip == ip
        return country

    monkeypatch.setattr(_geo, "discover_egress_ip", discover)
    monkeypatch.setattr(_geo, "ip_to_country", to_country)
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
    ip = _ip_picking("AU", "en-US")
    _egress_country_is(monkeypatch, "AU", ip=ip)
    monkeypatch.setattr(_geo, "discover_egress_ip", lambda proxy=None, **kw: ip)
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
    assert decide_session_locale("it-IT").languages == ("it-IT", "it", "en-US", "en")
    assert decide_session_locale("de").languages == ("de", "en-US", "en")
    assert decide_session_locale("fr-FR").region == "FR"


@pytest.mark.parametrize("tag,languages", [
    ("en-AU", ("en-AU", "en-US", "en")),
    ("fr-FR", ("fr-FR", "fr", "en-US", "en")),
    ("ja-JP", ("ja-JP", "ja", "en-US", "en")),
    ("pt_BR", ("pt-BR", "pt", "en-US", "en")),
    ("EN-gb", ("en-GB", "en")),
    ("ca-valencia", ("ca-valencia", "ca", "en-US", "en")),
])
def test_an_explicit_tag_is_navigator_language_and_the_table_gives_the_tail(
        monkeypatch, tag, languages):
    """Playwright's contract: ``locale`` IS navigator.language. Known-bad until
    36.32.x: "en-AU" answered "en-US" and "fr-FR" answered "fr", because the
    table's list was taken whole and its first entry replaced the request."""
    _no_network(monkeypatch)
    loc = decide_session_locale(tag)
    assert loc.languages == languages
    assert loc.primary == languages[0]


def test_auto_is_resolved_from_the_egress_and_then_the_same_table(monkeypatch):
    ip = _ip_picking("AU", "en-US")
    asked = _egress_country_is(monkeypatch, "AU", ip=ip)
    loc = decide_session_locale("auto", egress_ip=ip, proxy=SOCKS)
    assert loc == SessionLocale(decide_session_locale("en-US").languages, "AU"), (
        "auto must land on exactly what the picked build declares")
    assert asked == {"discover": 0, "locale": 1}, (
        "the egress the caller handed in must be reused, not rediscovered")


def test_auto_without_a_proxy_discovers_when_nothing_is_handed_in(monkeypatch):
    asked = _egress_country_is(monkeypatch, "IT", ip=_ip_picking("IT", "it"))
    loc = decide_session_locale("auto")
    assert loc.primary == "it-IT" and loc.region == "IT"
    assert asked["discover"] == 1


def test_auto_behind_a_proxy_with_no_egress_falls_back_and_never_goes_direct(
        monkeypatch, capsys):
    """The home country's language next to the proxy's timezone is worse than
    en-US, so a missing egress behind a proxy is never discovered directly."""
    asked = _egress_country_is(monkeypatch, "IT")
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


def test_a_context_locale_is_decided_without_the_network(monkeypatch):
    """`SessionLocale.of` is what a browser context's own locale goes through.
    Known-bad: "auto" resolved there, where the proxy is unknown, discovering
    the HOST's address and giving the home country's language."""
    def _no_network(*_a, **_k):
        raise AssertionError("a context locale must not discover anything")
    monkeypatch.setattr(_geo, "discover_egress_ip", _no_network)
    with pytest.raises(ValueError, match="auto"):
        SessionLocale.of("auto")
    for tag in ("de-DE", "en-AU", "fr", "pt_BR", "zh-CN"):
        assert SessionLocale.of(tag) == decide_session_locale(tag)
    decided = decide_session_locale("it-IT")
    assert SessionLocale.of(decided) is decided


def test_an_empty_language_list_is_not_a_session_locale():
    with pytest.raises(ValueError):
        SessionLocale(())


def test_the_primary_of_every_country_decides_the_same_list():
    """The wrapper hands a context the session's `primary` back; deciding it
    again must give the very list the launch declared, for every country."""
    from invisible_core._locale import _COUNTRY_FIREFOX_BUILDS, _from_country

    for cc, shares in _COUNTRY_FIREFOX_BUILDS.items():
        for build, _ in shares:
            decided = _from_country(cc, _ip_picking(cc, build))
            assert SessionLocale.of(decided.primary).languages == decided.languages, (cc, build)
            assert decided.region == cc, (cc, decided.region)


# ──────────────────────────────────────────────────────────────────────
#  The geo decision carries the language
# ──────────────────────────────────────────────────────────────────────

def test_prepare_session_geo_decides_the_language_with_one_discovery(monkeypatch):
    asked = _egress_country_is(monkeypatch, "AT", ip=_ip_picking("AT", "de"))
    geo = _geo.prepare_session_geo("auto", None)
    assert geo.locale == SessionLocale(("de", "en-US", "en"), "AT")
    assert asked["discover"] == 1, "one fact, one round trip"


def test_prepare_session_geo_takes_an_explicit_language_without_a_lookup(monkeypatch):
    asked = _egress_country_is(monkeypatch, "AT")
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


# ──────────────────────────────────────────────────────────────────────
#  "auto" is what the Firefox of the egress country declares
# ──────────────────────────────────────────────────────────────────────

def test_every_country_maps_to_builds_firefox_ships():
    """The class gate. Known-bad: the table before 36.33.0 held country tags
    ("DE": "de-DE", "HK": "zh-HK", "CO": "es-CO") that no Firefox build has,
    so 17 countries declared a list no real Firefox sends."""
    from invisible_core._locale import _COUNTRY_FIREFOX_BUILDS, _FIREFOX_BUILDS

    for cc, shares in _COUNTRY_FIREFOX_BUILDS.items():
        assert len(cc) == 2 and cc.isalpha() and cc.isupper(), cc
        assert shares, f"{cc}: a country with no build is left out, not listed empty"
        builds = [build for build, _ in shares]
        assert len(builds) == len(set(builds)), f"{cc}: a build listed twice"
        for build, weight in shares:
            assert build in _FIREFOX_BUILDS, f"{cc} -> {build}: Firefox ships no such build"
            assert weight > 0, (cc, build, weight)


@pytest.mark.parametrize("country,build,languages", [
    # a build with no region: the German Firefox is "de", wherever it runs
    ("DE", "de", ("de", "en-US", "en")), ("AT", "de", ("de", "en-US", "en")),
    ("CH", "fr", ("fr", "fr-FR", "en-US", "en")), ("NL", "nl", ("nl", "en-US", "en")),
    ("SA", "ar", ("ar", "en-US", "en")),
    # Belgium runs three builds, none of them Belgian
    ("BE", "fr", ("fr", "fr-FR", "en-US", "en")), ("BE", "nl", ("nl", "en-US", "en")),
    ("BE", "en-US", ("en-US", "en")),
    # countries with no build of their own
    ("AU", "en-US", ("en-US", "en")), ("AU", "en-GB", ("en-GB", "en")),
    ("HK", "zh-TW", ("zh-TW", "zh", "en-US", "en")),
    ("IT", "it", ("it-IT", "it", "en-US", "en")), ("GB", "en-GB", ("en-GB", "en")),
    ("JP", "ja", ("ja", "en-US", "en")),
])
def test_auto_declares_what_the_picked_firefox_declares(monkeypatch, country, build, languages):
    ip = _ip_picking(country, build)
    _egress_country_is(monkeypatch, country, ip=ip)
    loc = decide_session_locale("auto", egress_ip=ip, proxy=SOCKS)
    assert loc.languages == languages
    assert loc.region == country, "the CONSENT cookie needs the country, not the build"


def test_the_same_address_always_gets_the_same_build():
    """The IP is the seed: two launches behind one exit speak one language."""
    from invisible_core._locale import _country_build

    for ip in ("203.0.113.7", "198.51.100.200", "2001:db8::1"):
        assert len({_country_build("BE", ip) for _ in range(5)}) == 1
    assert _country_build("BE", "2001:db8::1") == _country_build("BE", "2001:0db8:0:0::1"), (
        "one address written two ways is one address")


def test_the_addresses_of_a_country_spread_over_its_builds_as_measured():
    """Known-bad: until 36.32.0 every Belgian session ran one build."""
    from invisible_core._locale import _COUNTRY_FIREFOX_BUILDS, _country_build

    n = 20000
    for cc in ("BE", "CH", "CA", "UA"):
        shares = dict(_COUNTRY_FIREFOX_BUILDS[cc])
        total = sum(shares.values())
        seen = {}
        for i in range(n):
            build = _country_build(cc, f"10.{(i >> 16) & 255}.{(i >> 8) & 255}.{i & 255}")
            seen[build] = seen.get(build, 0) + 1
        assert set(seen) <= set(shares), (cc, seen)
        for build, weight in shares.items():
            expected = weight / total
            assert abs(seen.get(build, 0) / n - expected) < 0.015, (cc, build, seen, expected)


def test_a_country_the_report_has_no_data_for_is_the_default_build(monkeypatch):
    from invisible_core._locale import _COUNTRY_FIREFOX_BUILDS

    assert "AQ" not in _COUNTRY_FIREFOX_BUILDS
    _egress_country_is(monkeypatch, "AQ")
    loc = decide_session_locale("auto", egress_ip="203.0.113.7", proxy=SOCKS)
    assert loc == SessionLocale(("en-US", "en"), "AQ")


def test_auto_is_resolved_in_one_module_only():
    """The egress country is an input to the decision, read nowhere else."""
    assert _code_references("_egress_country") <= {"_locale.py", "_geo.py"}
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
