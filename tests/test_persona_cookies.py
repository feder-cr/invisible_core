"""The persona's cookies, built in the core as data.

PORTED IN 36.x from invisible_playwright/tests/test_recaptcha_seed.py, with
the builder: three byte-identical copies of it lived in the wrapper, selenium
and puppeteer packages, and each took the CONSENT cookie's language from the
raw tag the caller held. Validates the contract:
  - 5 .google.com cookies always present
  - per-site cookies built from the persona's `browsing_history`
  - determinism: the same persona, language and clock give identical content
  - the 400-day cookie cap respected
  - the fields a driver's add-cookies call requires
  - the CONSENT `lang+region` token reads the DECIDED language
"""
import types

import pytest

from invisible_core import SessionLocale, decide_session_locale, persona_cookies
from invisible_core._cookies import CONSENT_REGION_COUNTRIES
from invisible_core.seedmix import sub_seed

pytestmark = pytest.mark.unit

_FIXED_NOW = 1779600000  # 2026-05-23, frozen for determinism
_EN_US = decide_session_locale("en-US")

# Sample browsing history for tests (mimics what _fpforge produces).
_SAMPLE_HISTORY = [
    {"name": "github.com",       "category": "dev",  "cookie_profile": "ga_cf"},
    {"name": "stackoverflow.com", "category": "dev", "cookie_profile": "ga_consent_clarity"},
    {"name": "amazon.com",       "category": "shop", "cookie_profile": "ga_consent_clarity"},
    {"name": "wikipedia.org",    "category": "reference", "cookie_profile": "minimal"},
    {"name": "youtube.com",      "category": "media", "cookie_profile": "ga_only"},
]


def _persona(seed, history=None):
    return types.SimpleNamespace(seed=seed, browsing_history=history)


def _build(seed=42, history=None, locale=_EN_US):
    return persona_cookies(_persona(seed, history), locale, now=_FIXED_NOW)


def _consent(cookies):
    return next(c for c in cookies if c["name"] == "CONSENT")["value"]


# ===========================================================================
# 1. Set composition
# ===========================================================================

def test_only_google_cookies_when_no_history():
    """Empty/None history -> only the 5 .google.com cookies (1P_JAR removed,
    deprecated by Google in 2022)."""
    cookies = _build(history=None)
    names = sorted(c["name"] for c in cookies)
    assert names == sorted(["NID", "CONSENT", "SOCS", "_GRECAPTCHA", "ENID"])
    assert all(c["domain"] == ".google.com" for c in cookies)


def test_browsing_history_adds_host_cookies():
    cookies = _build(history=_SAMPLE_HISTORY)
    google = [c for c in cookies if c["domain"] == ".google.com"]
    assert len(google) == 5
    domains = {c["domain"] for c in cookies if c["domain"] != ".google.com"}
    for site in _SAMPLE_HISTORY:
        assert f".{site['name']}" in domains


def test_domain_dot_prefix_normalized():
    for c in _build(history=_SAMPLE_HISTORY):
        assert c["domain"].startswith("."), f"missing dot: {c['domain']}"


# ===========================================================================
# 2. Cookie profile recipes
# ===========================================================================

def _host_names(profile):
    cookies = _build(history=[{"name": "x.com", "cookie_profile": profile}])
    return sorted(c["name"] for c in cookies if c["domain"] == ".x.com")


def test_profile_minimal_yields_ga_only():
    assert _host_names("minimal") == ["_ga"]


def test_profile_ga_only_yields_ga_and_gid():
    assert _host_names("ga_only") == ["_ga", "_gid"]


def test_profile_ga_cf_yields_ga_and_cf_bm():
    assert _host_names("ga_cf") == ["__cf_bm", "_ga"]


def test_profile_ga_consent_yields_three_cookies():
    names = _host_names("ga_consent")
    assert "_ga" in names and "_gid" in names
    assert any(n in names for n in ("OptanonAlertBoxClosed", "cookieyes-consent"))
    assert len(names) == 3


def test_profile_ga_consent_clarity_yields_at_least_four_cookies():
    names = _host_names("ga_consent_clarity")
    assert "_ga" in names and "_gid" in names and "_clck" in names
    assert any(n in names for n in ("OptanonAlertBoxClosed", "cookieyes-consent"))
    assert len(names) >= 4  # 4 baseline + 0-3 helpers


def test_unknown_profile_falls_back_to_ga():
    assert _host_names("nonexistent_profile") == ["_ga"]


# ===========================================================================
# 3. Determinism
# ===========================================================================

def test_same_persona_same_content():
    assert _build(history=_SAMPLE_HISTORY) == _build(history=_SAMPLE_HISTORY)


def test_different_seed_different_content():
    a = next(c for c in _build(42, _SAMPLE_HISTORY) if c["name"] == "NID")["value"]
    b = next(c for c in _build(99, _SAMPLE_HISTORY) if c["name"] == "NID")["value"]
    assert a != b


def test_history_order_does_not_affect_domain_specific_cookies():
    """The sub-seed is keyed on the domain name, not on the order."""
    def hosts(history):
        return {(c["domain"], c["name"]): c["value"]
                for c in _build(history=history) if c["domain"] != ".google.com"}
    assert hosts([_SAMPLE_HISTORY[0], _SAMPLE_HISTORY[1]]) == \
        hosts([_SAMPLE_HISTORY[1], _SAMPLE_HISTORY[0]])


def test_sub_seed_distinct_tags_distinct_streams():
    assert sub_seed(42, "google") != sub_seed(42, "dom:github.com")
    assert sub_seed(42, "dom:github.com") != sub_seed(42, "dom:amazon.com")
    assert sub_seed(0, "any") != 0  # seed=0 still produces a non-zero sub-seed


def test_the_sub_seed_values_the_consumers_shipped_are_unchanged():
    """The mix is baked into the cookies every shipped persona already has, so
    the port must not move it. The FNV-1a 64 arithmetic, restated here
    independently of the module."""
    h = 0xCBF29CE484222325 ^ 42
    for c in b"google":
        h = ((h ^ c) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    assert sub_seed(42, "google") == h


# ===========================================================================
# 4. Format of the Google batch
# ===========================================================================

def test_nid_format():
    nid = next(c for c in _build() if c["name"] == "NID")
    prefix, b64 = nid["value"].split("=", 1)
    assert prefix.isdigit() and len(prefix) == 3
    assert 100 <= int(prefix) <= 540
    assert len(b64) == 178


def test_consent_format():
    value = _consent(_build())
    assert value.startswith("YES+cb.")
    assert "+FX+" in value


# ===========================================================================
# 5. Cookie expiry cap
# ===========================================================================

def test_all_expiries_within_400_day_cap():
    """Chromium caps cookie expiry at 400 days and drops longer ones; every
    long-lived cookie here stays at 395 days or less."""
    max_allowed = _FIXED_NOW + 400 * 86400
    for c in _build(history=_SAMPLE_HISTORY):
        if c["name"] in ("__cf_bm", "1P_JAR", "_gid"):
            continue
        assert c["expires"] <= max_allowed, c["name"]


# ===========================================================================
# 6. Fields a driver requires
# ===========================================================================

def test_all_cookies_have_the_fields_a_driver_requires():
    for c in _build(history=_SAMPLE_HISTORY):
        assert c.get("name"), c
        assert c.get("value") is not None, c
        assert c.get("domain"), c
        assert c.get("path") == "/", c["name"]


def test_modern_cookies_marked_secure():
    """sameSite=None requires secure=True in Firefox and Chromium."""
    for c in _build(history=_SAMPLE_HISTORY):
        if c.get("sameSite") == "None":
            assert c.get("secure") is True, c["name"]


def test_httponly_on_signed_cookies():
    cookies = _build()
    assert next(c for c in cookies if c["name"] == "NID").get("httpOnly") is True
    assert next(c for c in cookies if c["name"] == "ENID").get("httpOnly") is True


def test_no_1p_jar_cookie():
    assert "1P_JAR" not in {c["name"] for c in _build(history=_SAMPLE_HISTORY)}


def test_nid_prefix_broadened_range():
    seen = {int(next(c for c in _build(seed) if c["name"] == "NID")["value"]
                .split("=", 1)[0]) for seed in range(200)}
    assert min(seen) < 500
    assert max(seen) <= 540


# ===========================================================================
# 7. With a real persona
# ===========================================================================

def test_with_a_real_profile():
    from invisible_core import generate_profile
    prof = generate_profile(seed=42)
    assert 5 <= len(prof.browsing_history) <= 50
    for site in prof.browsing_history:
        assert "name" in site and "category" in site and "cookie_profile" in site
    cookies = persona_cookies(prof, _EN_US, now=_FIXED_NOW)
    assert len(cookies) >= 5 + len(prof.browsing_history)


# ===========================================================================
# 8. The CONSENT token reads the DECISION
# ===========================================================================

@pytest.mark.parametrize("tag,token", [
    ("it-IT", ".it+IT+"),
    ("de-DE", ".de+DE+"),
    ("en-US", ".en+FX+"),
    # French starts the list bare ("fr, fr-FR, ..."), and the region still
    # comes from where the decision was made, not from the list.
    ("fr-FR", ".fr+FR+"),
    ("en-IE", ".en+IE+"),
])
def test_consent_token_from_the_decided_language(tag, token):
    assert token in _consent(_build(locale=decide_session_locale(tag)))


def test_consent_lang_covers_a_country_the_old_table_did_not():
    """Romania is the case the timezone table got wrong, so it is the case pinned.

    Same name as the wrapper test it was ported from, which the gate inventory
    cites. `Europe/Bucharest` was not one of the 22 rows of the old timezone
    table, so the cookie said `en+FX` while `navigator.language` said `ro-RO` -
    a Romanian browser claiming to be a non-EU English one. Known-bad: shrink
    CONSENT_REGION_COUNTRIES to the three countries the old table carried.
    """
    assert ".ro+RO+" in _consent(_build(locale=decide_session_locale("ro-RO")))
    # And a non-EEA country keeps the FX token while still carrying its own
    # language, which is what a real Brazilian visitor gets.
    assert ".pt+FX+" in _consent(_build(locale=decide_session_locale("pt-BR")))


def test_a_tag_is_decided_the_same_way_as_a_session_locale():
    for tag in ("it-IT", "en-AU", "fr-BE", None):
        assert _build(locale=tag) == _build(locale=decide_session_locale(tag))


def test_the_consent_language_is_the_one_navigator_reports():
    """The known-bad shape: the cookie's language taken from somewhere other
    than `primary`. A decision whose list starts with another language than
    its region suggests must put THAT language in the cookie."""
    loc = SessionLocale(("de-DE", "de", "en-US", "en"), "IT")
    assert ".de+IT+" in _consent(_build(locale=loc))


def test_every_country_the_core_maps_reads_its_own_language():
    from invisible_core._locale import _COUNTRY_FIREFOX_BUILDS
    for cc, shares in _COUNTRY_FIREFOX_BUILDS.items():
        for build, _ in shares:
            loc = SessionLocale(SessionLocale.of(build).languages, cc)
            lang = loc.primary.split("-")[0].lower()
            region = cc if cc in CONSENT_REGION_COUNTRIES else "FX"
            assert f".{lang}+{region}+" in _consent(_build(locale=loc)), (cc, build)


def test_auto_is_refused_by_the_pure_builder():
    with pytest.raises(ValueError, match="auto"):
        _build(locale="auto")


# ===========================================================================
# 9. Helper cookies
# ===========================================================================

def test_new_helper_cookies_appear_in_ga_consent_clarity():
    saw = {"fbp": False, "gtm": False, "hssrc": False}
    history = [{"name": "site.com", "cookie_profile": "ga_consent_clarity"}]
    for seed in range(100):
        names = {c["name"] for c in _build(seed, history) if c["domain"] == ".site.com"}
        saw["fbp"] |= "_fbp" in names
        saw["gtm"] |= any(n.startswith("_dc_gtm_") for n in names)
        saw["hssrc"] |= "__hssrc" in names
    assert all(saw.values()), saw


def test_fbp_format():
    history = [{"name": "x.com", "cookie_profile": "ga_consent_clarity"}]
    for seed in range(20):
        fbp = next((c for c in _build(seed, history) if c["name"] == "_fbp"), None)
        if fbp:
            parts = fbp["value"].split(".")
            assert parts[0] == "fb"
            assert parts[1].isdigit()
            assert parts[2].isdigit() and len(parts[2]) >= 13  # unix ms
            assert parts[3].isdigit()
            return
    raise AssertionError("never got _fbp across 20 seeds - distribution broken")
