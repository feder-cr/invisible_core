"""The session language: decided ONCE, read everywhere.

WHY THIS MODULE EXISTS. Until 36.x the language of a session was decided in
pieces. `resolve_session_locale` turned "auto" into a tag from the egress
country, every caller repeated the `if locale == "auto"` branch around it, and
then each consumer derived more values from the RAW tag: the prefs wrote
`general.useragent.locale` from the tag and `intl.accept_languages` from the
table below, the wrapper's server converted a context's tag with the same
table, and three copies of the cookie builder took the CONSENT token from the
tag again.

The Australian egress is the case that showed it, measured 2026-10-04: the
country maps to "en-AU", the table maps "en-AU" to "en-US, en" (it is keyed by
LANGUAGE, as Firefox's is: there is no en-AU build), so `navigator.language`
said en-US while the requested locale said en-AU. Two answers to one question,
both computed in this package.

Now one function, :func:`decide_session_locale`, is the ONLY place "auto" is
resolved and the ONLY place the table is applied, and its result is a value
object, :class:`SessionLocale`. Every tag-shaped value reads
:attr:`SessionLocale.primary`, every list-shaped value reads
:attr:`SessionLocale.accept_languages`, and nothing re-derives either.
"""
from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

#: What "no language asked" means. A requested value of None or "" lands here,
#: and so does the warned fallback of a failed "auto": Firefox's own default
#: build is en-US.
DEFAULT_LOCALE = "en-US"


@dataclass(frozen=True)
class SessionLocale:
    """The language list a session declares, and the region it was decided for.

    Built by :func:`decide_session_locale`; consumers read it and never derive
    from the tag that produced it.

    ``languages`` is the list Firefox's language prefs carry, in order: the
    first entry is what ``navigator.language`` reports and the whole tuple is
    ``navigator.languages`` and the Accept-Language header (Firefox prepares
    the header from it, with its own q-values).

    ``region`` is, for "auto", the egress country; for an explicit tag, its
    region subtag, or None when the tag has none. It is NOT a language: it answers "where", which the language list
    cannot always say (French is "fr" first, with no region, for a French and a
    Belgian egress alike), and the Google CONSENT cookie needs exactly that.
    """

    languages: Tuple[str, ...]
    region: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.languages or not all(
                isinstance(t, str) and t for t in self.languages):
            raise ValueError(
                f"a SessionLocale needs a non-empty list of language tags, "
                f"got {self.languages!r}")

    @property
    def primary(self) -> str:
        """The language the session reports: ``navigator.language``.

        Every pref or value shaped like ONE tag uses this, never the requested
        tag: for "en-AU" it is "en-US", because that is what the list starts
        with and therefore what the page reads.
        """
        return self.languages[0]

    @property
    def accept_languages(self) -> str:
        """The list as Firefox's prefs take it: ``"en-US, en"``."""
        return ", ".join(self.languages)

    @classmethod
    def of(cls, value: Any) -> "SessionLocale":
        """The decision for a value that needs no egress: a tag, or a decision.

        A :class:`SessionLocale` passes through; a tag goes through the same
        table :func:`decide_session_locale` applies, so a caller handing a tag
        and a caller handing the decision get identical output. This is what a
        PURE consumer uses: the prefs and cookie builders, and a browser
        context that asks for its own locale (``new_context(locale="de-DE")``).

        "auto" is REFUSED: resolving it needs the egress address, which only
        the launch has (:func:`prepare_session_geo`). Resolved anywhere else it
        would discover the HOST's address and give the home country's language
        next to the proxy country's timezone; written verbatim it became the
        language "auto".
        """
        if isinstance(value, cls):
            return value
        if _is_auto(value):
            raise ValueError(
                'locale="auto" is decided once, at launch, from the egress '
                "address: pass the SessionLocale from prepare_session_geo() "
                "(its .locale) or an explicit tag such as 'de-DE'.")
        return _from_tag(value)


# ──────────────────────────────────────────────────────────────────────
#  The table: how Firefox turns a locale into its default language list
# ──────────────────────────────────────────────────────────────────────

#: Ported from `intl/locale/rust/locale_service_glue/src/lib.rs:89-222`
#: (`locale_service_default_accept_languages`). The key is the LANGUAGE code,
#: not the whole locale.
_ACCEPT_LANG_TABLE = {
    "ace": "ace, id", "ach": "ach, en-GB", "af": "af, en-ZA, en-GB",
    "ak": "ak, ak-GH", "an": "an, es-ES, es, ca", "ast": "ast, es-ES, es",
    "az": "az-AZ, az", "bo": "bo-CN, bo-IN, bo", "br": "br, fr-FR, fr",
    "brx": "brx, as", "bs": "bs-BA, bs", "cak": "cak, kaq, es",
    "crh": "tr-TR, tr", "cs": "cs, sk", "csb": "csb, csb-PL, pl",
    "cy": "cy-GB, cy", "dsb": "dsb, hsb, de", "el": "el-GR, el",
    "et": "et, et-EE", "fa": "fa-IR, fa", "ff": "ff, fr-FR, fr, en-GB",
    "fi": "fi-FI, fi", "fr": "fr, fr-FR", "frp": "frp, fr-FR, fr",
    "fur": "fur-IT, fur, it-IT, it", "fy": "fy-NL, fy, nl",
    "ga": "ga-IE, ga, en-IE, en-GB", "gd": "gd-GB, gd, en-GB",
    "gl": "gl-ES, gl", "gn": "gn, es", "gv": "gv, en-GB", "he": "he, he-IL",
    "hr": "hr, hr-HR", "hsb": "hsb, dsb, de",
    "hto": "es-MX, es-ES, es, es-AR, es-CL", "hu": "hu-HU, hu",
    "hye": "hye, hy", "ilo": "ilo-PH, ilo", "it": "it-IT, it",
    "ixl": "ixl, es-MX, es", "ja": "ja", "ka": "ka-GE, ka",
    "kab": "kab-DZ, kab, fr-FR, fr", "kk": "kk, ru, ru-RU", "kn": "kn-IN, kn",
    "ko": "ko-KR, ko", "lb": "lb, de-DE, de", "lg": "lg, en-GB",
    "lij": "lij, it", "lt": "lt, ru, pl", "ltg": "ltg, lv",
    "mai": "mai, hi-IN, en", "meh": "meh, es-MX, es", "mix": "mix, es-MX, es",
    "mk": "mk-MK, mk", "ml": "ml-IN, ml", "mr": "mr-IN, mr",
    "nb": "nb-NO, nb, no-NO, no, nn-NO, nn",
    "nn": "nn-NO, nn, no-NO, no, nb-NO, nb", "nr": "nr-ZA, nr, en-ZA, en-GB",
    "nso": "nso-ZA, nso, en-ZA, en-GB", "oc": "oc, ca, fr, es, it",
    "pa": "pa, pa-IN", "ppl": "ppl, es-MX, es", "rm": "rm, rm-CH, de-CH, de",
    "ru": "ru-RU, ru", "sah": "sah, ru-RU, ru", "sc": "sc, it-IT, it",
    "scn": "scn, it-IT, it", "si": "si-LK, si", "sk": "sk, cs",
    "son": "son, son-ML, fr", "sq": "sq, sq-AL", "sr": "sr-RS, sr",
    "st": "st-ZA, st, en-ZA, en-GB", "ta": "ta-IN, ta", "te": "te-IN, te",
    "tl": "tl-PH, tl", "tr": "tr-TR, tr", "trs": "trs, es-MX, es",
    "ts": "ts-ZA, ts, en-ZA, en-GB", "uk": "uk-UA, uk", "ur": "ur-PK, ur",
    "uz": "uz, ru", "ve": "ve-ZA, ve, en-ZA, en-GB", "vi": "vi-VN, vi",
    "xcl": "xcl, hy", "xh": "xh-ZA, xh", "zam": "zam, es-MX, es",
}

#: The SIX languages where Firefox does NOT append ", en-US, en"
#: (`add_en_us = false` in the source cited above). Every other one appends it.
_ACCEPT_LANG_NO_EN = {
    "en": None,          # the `en` table depends on the region, see below
    "my": "my, en-GB, en",
    "ro": "ro-RO, ro-GB, en",
    "sco": "sco, en-GB, en",
    "sl": "sl, en-GB, en",
    "szl": "szl, pl-PL, pl, en, de",
}


#: The locales Firefox 151 ships: `browser/locales/shipped-locales` of the
#: firefox-36 source (stealth/151), without the macOS-only ja-JP-mac. A
#: Firefox "of a country" is one of these builds and nothing else: there is no
#: en-AU, de-DE, es-CO or zh-HK build, so a list starting with one of those is
#: a list no real Firefox produces.
_FIREFOX_BUILDS = frozenset("""
    ach af an ar ast az be bg bn br bs ca ca-valencia cak cs cy da de dsb el
    en-CA en-GB en-US eo es-AR es-CL es-ES es-MX et eu fa ff fi fr fur fy-NL
    ga-IE gd gl gn gu-IN he hi-IN hr hsb hu hy-AM ia id is it ja ka kab kk km
    kn ko lij lt lv mk mr ms my nb-NO ne-NP nl nn-NO oc pa-IN pl pt-BR pt-PT rm
    ro ru sat sc sco si sk skr sl son sq sr sv-SE szl ta te tg th tl tr trs uk
    ur uz vi xh zh-CN zh-TW
""".split())

#: ISO 3166 country -> the Firefox builds people in that country run, with
#: the percentage of Firefox desktop users on each. Firefox derives every
#: language value from the BUILD alone (`locale_service_default_accept_languages`
#: is called with the APP locale, `LocaleService.cpp` `mAppLocales[0]`), never
#: from the country, so "the Firefox of a country" is not one value: in Belgium
#: 40% run the French build, 31% the Dutch one and 22% the US English one.
#:
#: Source: the Firefox Public Data Report, "Top Languages" (data.firefox.com,
#: desktop/usage-behavior/<country>/locale), the top five locale settings per
#: country, averaged over the weeks 2026-09-07 to 2026-09-28. Cleaned in three
#: ways and no other: ja-JP-macos counted as ja (the target is Windows), the
#: entries that are not a Firefox 151 build dropped (und, en-ZA), the shares
#: that round to 0.0 dropped. The weights need not sum to 100: the top five
#: never do, and the pick normalizes. Updated BY HAND, owner's decision
#: (2026-10-06): the data moves slowly and the product must not depend on that
#: site at run time.
#:
#: A country missing here (the report has no data for it) is decided as en-US,
#: the default build.
#:
#: Until 36.32.0 this table held one country TAG per country ("DE": "de-DE")
#: fed to the Firefox function as if it were a build, which gave 17 countries a
#: list no real Firefox sends, and every session of a country the same language.
_COUNTRY_FIREFOX_BUILDS = {
    "AE": (("en-US", 89.0), ("en-GB", 5.1), ("ru", 2.5), ("ar", 1.3), ("fr", 0.3), ("zh-CN", 0.2),),
    "AF": (("en-US", 96.5), ("en-GB", 1.3), ("fa", 1.3), ("fr", 0.4), ("ru", 0.1), ("en-CA", 0.1), ("pl", 0.1), ("tr", 0.1),),
    "AL": (("en-US", 73.4), ("en-GB", 10.5), ("fr", 3.8), ("de", 3.7), ("it", 3.1),),
    "AM": (("en-US", 59.2), ("ru", 35.0), ("hy-AM", 1.5), ("zh-CN", 1.3), ("en-GB", 1.1), ("fr", 0.4),),
    "AO": (("pt-PT", 37.8), ("en-US", 29.6), ("pt-BR", 25.7), ("es-ES", 2.1), ("fr", 2.0),),
    "AR": (("es-AR", 53.6), ("es-ES", 30.5), ("en-US", 11.5), ("es-MX", 3.3), ("en-GB", 0.3),),
    "AT": (("de", 82.1), ("en-US", 14.1), ("en-GB", 1.4), ("ru", 0.6), ("hu", 0.4),),
    "AU": (("en-US", 87.8), ("en-GB", 10.7), ("zh-CN", 0.5), ("en-CA", 0.2), ("fr", 0.2),),
    "AZ": (("en-US", 57.1), ("ru", 28.7), ("tr", 7.3), ("az", 3.9), ("en-GB", 2.0),),
    "BA": (("en-US", 68.2), ("hr", 14.9), ("bs", 5.4), ("sr", 5.1), ("en-GB", 4.0),),
    "BD": (("en-US", 97.1), ("en-GB", 2.7),),
    "BE": (("fr", 40.4), ("nl", 31.2), ("en-US", 22.0), ("en-GB", 2.8), ("de", 1.7),),
    "BF": (("fr", 91.4), ("en-US", 8.4), ("zh-CN", 0.1), ("ru", 0.1), ("en-GB", 0.1),),
    "BG": (("en-US", 61.0), ("bg", 31.6), ("en-GB", 4.0), ("ru", 1.4), ("de", 0.7),),
    "BH": (("en-US", 90.8), ("en-GB", 6.3), ("ar", 2.4), ("uk", 0.2), ("tr", 0.2),),
    "BI": (("fr", 58.9), ("en-US", 39.7), ("ru", 1.0), ("it", 0.2), ("en-CA", 0.2),),
    "BJ": (("fr", 85.8), ("en-US", 13.5), ("en-CA", 0.3), ("de", 0.2), ("ru", 0.1), ("zh-CN", 0.1),),
    "BN": (("en-US", 95.7), ("en-GB", 3.7), ("de", 0.3), ("fr", 0.1), ("vi", 0.1),),
    "BO": (("es-ES", 66.3), ("es-MX", 21.5), ("en-US", 8.5), ("es-AR", 2.5), ("es-CL", 0.6),),
    "BR": (("pt-BR", 88.5), ("en-US", 9.8), ("pt-PT", 1.1), ("en-GB", 0.2), ("es-ES", 0.1),),
    "BW": (("en-US", 93.6), ("en-GB", 5.6), ("zh-CN", 0.3), ("ru", 0.3), ("nb-NO", 0.1),),
    "BY": (("ru", 91.4), ("en-US", 7.5), ("be", 0.6), ("en-GB", 0.3),),
    "CA": (("en-US", 51.8), ("en-CA", 32.5), ("fr", 11.0), ("en-GB", 3.2), ("zh-CN", 0.4),),
    "CD": (("fr", 76.0), ("en-US", 21.5), ("en-GB", 0.9), ("ru", 0.9), ("zh-CN", 0.3), ("el", 0.1),),
    "CG": (("fr", 87.9), ("en-US", 11.5), ("en-GB", 0.5), ("zh-CN", 0.2),),
    "CH": (("de", 55.6), ("fr", 19.5), ("en-US", 18.6), ("it", 2.7), ("en-GB", 2.0),),
    "CI": (("fr", 87.2), ("en-US", 12.3), ("en-GB", 0.3), ("ru", 0.1), ("es-ES", 0.1),),
    "CL": (("es-CL", 44.1), ("es-ES", 36.7), ("en-US", 13.1), ("es-MX", 4.1), ("es-AR", 1.0),),
    "CM": (("fr", 70.4), ("en-US", 27.7), ("en-GB", 1.0), ("zh-CN", 0.2), ("ru", 0.2), ("en-CA", 0.2),),
    "CN": (("zh-CN", 95.7), ("en-US", 3.8), ("zh-TW", 0.2), ("en-GB", 0.1),),
    "CO": (("es-ES", 75.1), ("en-US", 10.7), ("es-MX", 10.5), ("es-AR", 2.4), ("es-CL", 0.7),),
    "CR": (("es-ES", 48.9), ("es-MX", 28.4), ("en-US", 18.9), ("es-AR", 2.4), ("de", 0.4), ("en-GB", 0.1),),
    "CU": (("es-ES", 69.1), ("en-US", 20.1), ("es-MX", 9.8), ("es-AR", 0.6), ("en-GB", 0.1),),
    "CY": (("en-US", 70.8), ("en-GB", 13.7), ("el", 5.1), ("ru", 5.0), ("de", 1.8),),
    "CZ": (("cs", 82.2), ("en-US", 13.9), ("en-GB", 1.1), ("ru", 1.1), ("de", 0.4), ("sk", 0.1),),
    "DE": (("de", 88.5), ("en-US", 9.1), ("en-GB", 0.7), ("ru", 0.7), ("pl", 0.2),),
    "DK": (("da", 54.7), ("en-US", 37.0), ("en-GB", 3.7), ("de", 2.2), ("fr", 0.4),),
    "DO": (("es-ES", 55.9), ("en-US", 34.0), ("es-MX", 5.9), ("es-AR", 1.2), ("fr", 0.9),),
    "DZ": (("fr", 68.8), ("en-US", 23.1), ("ar", 6.7), ("en-GB", 0.8), ("ru", 0.2),),
    "EC": (("es-ES", 78.8), ("es-MX", 12.0), ("en-US", 5.6), ("es-AR", 3.0), ("es-CL", 0.3),),
    "EE": (("en-US", 54.9), ("et", 19.4), ("ru", 17.0), ("en-GB", 6.1), ("de", 0.4), ("fi", 0.4),),
    "EG": (("en-US", 78.3), ("ar", 18.9), ("en-GB", 2.1), ("de", 0.2), ("ru", 0.1),),
    "ES": (("es-ES", 74.7), ("en-US", 11.8), ("ca", 5.2), ("ca-valencia", 3.4), ("en-GB", 0.7), ("de", 0.2),),
    "ET": (("en-US", 97.2), ("en-GB", 2.3), ("ru", 0.2), ("fr", 0.1), ("en-CA", 0.1),),
    "FI": (("fi", 60.4), ("en-US", 30.1), ("ru", 3.7), ("en-GB", 3.4), ("sv-SE", 1.1),),
    "FR": (("fr", 91.5), ("en-US", 7.1), ("en-GB", 0.6), ("de", 0.3), ("ru", 0.1),),
    "GA": (("fr", 87.5), ("en-US", 11.7), ("de", 0.4), ("ru", 0.2), ("it", 0.2),),
    "GB": (("en-GB", 64.7), ("en-US", 32.7), ("ru", 0.4), ("pl", 0.4), ("fr", 0.2), ("de", 0.1), ("zh-CN", 0.1),),
    "GE": (("en-US", 75.2), ("ru", 16.6), ("ka", 3.9), ("en-GB", 2.1), ("de", 0.2), ("tr", 0.2), ("fr", 0.1), ("pt-PT", 0.1),),
    "GF": (("fr", 94.1), ("en-US", 4.5), ("en-GB", 1.2), ("zh-CN", 0.2),),
    "GH": (("en-US", 93.5), ("en-GB", 5.5), ("fr", 0.3), ("ru", 0.2), ("de", 0.1),),
    "GN": (("fr", 74.7), ("en-US", 21.1), ("zh-CN", 0.9), ("es-MX", 0.7), ("ru", 0.7), ("en-CA", 0.7), ("tr", 0.5),),
    "GP": (("fr", 93.5), ("en-US", 5.9), ("en-GB", 0.5), ("en-CA", 0.1),),
    "GR": (("el", 66.5), ("en-US", 29.4), ("en-GB", 2.0), ("de", 0.9), ("ru", 0.4),),
    "GT": (("es-ES", 70.5), ("es-MX", 14.5), ("en-US", 13.3), ("es-AR", 1.2),),
    "HK": (("en-US", 36.0), ("zh-CN", 34.0), ("zh-TW", 26.7), ("en-GB", 2.0), ("ru", 0.2), ("en-CA", 0.1), ("ja", 0.1),),
    "HN": (("es-ES", 61.0), ("en-US", 19.3), ("es-MX", 16.8), ("es-AR", 2.1), ("en-GB", 0.5),),
    "HR": (("en-US", 53.9), ("hr", 35.5), ("en-GB", 5.6), ("de", 2.7), ("pl", 0.3), ("cs", 0.2),),
    "HT": (("en-US", 62.0), ("fr", 34.6), ("es-ES", 1.4), ("de", 1.0), ("en-CA", 0.7), ("en-GB", 0.3),),
    "HU": (("hu", 87.2), ("en-US", 10.5), ("en-GB", 1.0), ("de", 0.6), ("ru", 0.2),),
    "ID": (("en-US", 72.2), ("id", 26.4), ("en-GB", 1.2), ("zh-CN", 0.1), ("en-CA", 0.1),),
    "IE": (("en-US", 72.2), ("en-GB", 21.6), ("fr", 1.0), ("pl", 0.8), ("de", 0.4), ("es-ES", 0.4), ("ru", 0.2),),
    "IL": (("en-US", 72.0), ("he", 12.4), ("ru", 7.5), ("en-GB", 3.9), ("fr", 1.6),),
    "IN": (("en-US", 95.7), ("en-GB", 4.0), ("en-CA", 0.2),),
    "IQ": (("en-US", 76.1), ("ar", 20.9), ("en-GB", 2.2), ("ru", 0.1),),
    "IS": (("en-US", 73.3), ("en-GB", 12.2), ("is", 5.0), ("ru", 1.5), ("pl", 1.3), ("fr", 1.0), ("de", 0.5),),
    "IT": (("it", 87.7), ("en-US", 9.0), ("de", 1.6), ("en-GB", 0.7), ("fr", 0.3),),
    "JM": (("en-US", 94.1), ("en-GB", 3.7), ("en-CA", 1.1), ("de", 0.5), ("es-ES", 0.4), ("es-MX", 0.1),),
    "JO": (("en-US", 81.0), ("ar", 15.0), ("en-GB", 3.0), ("de", 0.4), ("ru", 0.2),),
    "JP": (("ja", 87.3), ("en-US", 7.7), ("zh-CN", 3.1), ("en-GB", 0.4),),
    "KE": (("en-US", 93.2), ("en-GB", 6.2), ("en-CA", 0.2), ("de", 0.1), ("fr", 0.1),),
    "KG": (("ru", 79.3), ("en-US", 19.5), ("en-GB", 0.4), ("tr", 0.3), ("ar", 0.2), ("pt-BR", 0.2), ("zh-CN", 0.1),),
    "KH": (("en-US", 94.4), ("en-GB", 2.7), ("zh-CN", 0.9), ("id", 0.9), ("km", 0.1), ("fr", 0.1),),
    "KR": (("ko", 77.6), ("en-US", 18.0), ("zh-CN", 2.0), ("en-GB", 0.4), ("ru", 0.2), ("vi", 0.2),),
    "KW": (("en-US", 87.9), ("ar", 6.6), ("en-GB", 4.7), ("es-ES", 0.3), ("en-CA", 0.1),),
    "KZ": (("ru", 91.4), ("en-US", 7.5), ("en-GB", 0.5), ("kk", 0.2), ("de", 0.1),),
    "LA": (("en-US", 79.9), ("th", 12.0), ("en-GB", 3.7), ("zh-CN", 2.7), ("vi", 0.6), ("de", 0.2), ("fr", 0.2),),
    "LB": (("en-US", 92.3), ("en-GB", 3.6), ("fr", 2.2), ("ar", 1.2), ("de", 0.2), ("es-ES", 0.2),),
    "LK": (("en-US", 96.4), ("en-GB", 3.1), ("en-CA", 0.2), ("de", 0.1),),
    "LT": (("en-US", 51.7), ("lt", 34.4), ("ru", 8.9), ("en-GB", 3.9), ("de", 0.1), ("fr", 0.1),),
    "LU": (("en-US", 30.9), ("de", 29.3), ("fr", 25.0), ("en-GB", 11.0), ("nl", 0.6), ("es-ES", 0.2),),
    "LV": (("en-US", 50.8), ("ru", 25.3), ("lv", 18.6), ("en-GB", 4.0), ("tr", 0.1), ("de", 0.1), ("uk", 0.1),),
    "LY": (("en-US", 55.7), ("ar", 41.9), ("en-GB", 1.6), ("tr", 0.4), ("de", 0.3),),
    "MA": (("fr", 74.2), ("en-US", 22.5), ("ar", 1.1), ("en-GB", 0.8), ("es-ES", 0.5),),
    "MD": (("ru", 56.5), ("en-US", 31.5), ("ro", 7.5), ("en-GB", 4.0), ("zh-CN", 0.1), ("uk", 0.1),),
    "ME": (("en-US", 71.9), ("en-GB", 6.4), ("sr", 6.3), ("ru", 5.8), ("de", 1.2), ("hr", 0.4), ("pl", 0.3),),
    "MG": (("fr", 83.8), ("en-US", 15.3), ("en-GB", 0.4), ("ru", 0.2), ("en-CA", 0.1), ("zh-CN", 0.1),),
    "MK": (("en-US", 93.0), ("en-GB", 3.9), ("mk", 0.9), ("sr", 0.4), ("de", 0.4), ("es-ES", 0.1), ("tr", 0.1),),
    "ML": (("fr", 89.7), ("en-US", 9.5), ("en-GB", 0.6), ("pt-PT", 0.2),),
    "MM": (("en-US", 97.0), ("en-GB", 2.3), ("zh-CN", 0.3), ("en-CA", 0.1), ("my", 0.1),),
    "MN": (("en-US", 96.1), ("en-GB", 2.4), ("zh-CN", 0.5), ("ko", 0.2), ("de", 0.2), ("ru", 0.2), ("fr", 0.1), ("ja", 0.1),),
    "MO": (("zh-TW", 42.7), ("en-US", 38.9), ("zh-CN", 14.7), ("en-GB", 2.7), ("pt-PT", 0.6), ("ja", 0.2),),
    "MQ": (("fr", 94.0), ("en-US", 5.1), ("es-ES", 0.8), ("en-GB", 0.2),),
    "MT": (("en-US", 70.1), ("en-GB", 20.0), ("it", 1.8), ("de", 1.1), ("ru", 0.9), ("pl", 0.4), ("es-ES", 0.3), ("el", 0.2),),
    "MU": (("en-US", 81.3), ("en-GB", 11.6), ("fr", 5.0), ("de", 0.8), ("cs", 0.2), ("ru", 0.2), ("it", 0.1),),
    "MV": (("en-US", 87.7), ("en-GB", 9.0), ("tr", 1.0), ("de", 0.8), ("cs", 0.3), ("zh-CN", 0.2), ("ru", 0.2), ("fr", 0.2),),
    "MW": (("en-US", 94.3), ("en-GB", 5.2), ("ru", 0.4), ("en-CA", 0.1),),
    "MX": (("es-MX", 59.3), ("es-ES", 26.4), ("en-US", 12.7), ("es-AR", 1.1), ("en-GB", 0.2),),
    "MY": (("en-US", 94.1), ("en-GB", 3.9), ("zh-CN", 1.3), ("ms", 0.2), ("zh-TW", 0.1),),
    "MZ": (("en-US", 55.4), ("pt-PT", 20.3), ("pt-BR", 19.9), ("en-GB", 3.7), ("id", 0.3), ("vi", 0.1),),
    "NA": (("en-US", 91.2), ("en-GB", 7.7), ("pt-BR", 0.4), ("de", 0.4), ("it", 0.1), ("pt-PT", 0.1),),
    "NC": (("fr", 96.7), ("en-US", 3.2), ("de", 0.1),),
    "NE": (("fr", 87.6), ("en-US", 11.3), ("ru", 0.9), ("en-GB", 0.2),),
    "NG": (("en-US", 95.7), ("en-GB", 3.7), ("fr", 0.3), ("en-CA", 0.1), ("ru", 0.1),),
    "NI": (("es-ES", 70.4), ("es-MX", 14.3), ("en-US", 13.8), ("es-AR", 0.9), ("es-CL", 0.3), ("en-GB", 0.1),),
    "NL": (("nl", 50.1), ("en-US", 34.1), ("ru", 5.0), ("en-GB", 4.7), ("de", 1.2), ("fr", 0.4),),
    "NO": (("nb-NO", 45.7), ("en-US", 41.5), ("en-GB", 5.2), ("pl", 1.4), ("ru", 0.8), ("de", 0.7),),
    "NP": (("en-US", 96.8), ("en-GB", 2.8), ("ne-NP", 0.1), ("fr", 0.1), ("en-CA", 0.1),),
    "NZ": (("en-US", 86.1), ("en-GB", 11.7), ("zh-CN", 0.5), ("en-CA", 0.4), ("fr", 0.2), ("de", 0.2),),
    "OM": (("en-US", 58.5), ("ar", 38.3), ("en-GB", 2.4), ("fr", 0.3), ("en-CA", 0.2), ("de", 0.1),),
    "PA": (("es-ES", 60.1), ("en-US", 25.4), ("es-MX", 10.4), ("es-AR", 1.3), ("en-GB", 0.5), ("fr", 0.1),),
    "PE": (("es-ES", 73.8), ("en-US", 11.7), ("es-MX", 11.5), ("es-AR", 2.1), ("es-CL", 0.2),),
    "PF": (("fr", 91.7), ("en-US", 7.6), ("en-GB", 0.7),),
    "PG": (("en-US", 98.1), ("en-GB", 1.7), ("zh-CN", 0.2),),
    "PH": (("en-US", 96.8), ("en-GB", 2.3), ("zh-CN", 0.3), ("en-CA", 0.1), ("de", 0.1),),
    "PK": (("en-US", 94.2), ("en-GB", 5.5), ("en-CA", 0.1),),
    "PL": (("pl", 91.0), ("en-US", 7.3), ("ru", 0.6), ("en-GB", 0.6), ("uk", 0.1), ("de", 0.1),),
    "PR": (("en-US", 82.1), ("es-ES", 14.2), ("es-MX", 2.1), ("en-GB", 0.9), ("es-AR", 0.4),),
    "PS": (("en-US", 53.7), ("ar", 43.5), ("en-GB", 2.1), ("de", 0.4), ("ru", 0.2), ("he", 0.1),),
    "PT": (("pt-PT", 59.4), ("en-US", 23.1), ("pt-BR", 10.3), ("en-GB", 3.3), ("fr", 1.4),),
    "PY": (("es-ES", 73.7), ("en-US", 8.0), ("es-MX", 8.0), ("es-AR", 6.3), ("pt-BR", 1.7),),
    "QA": (("en-US", 90.9), ("en-GB", 5.4), ("ar", 1.9), ("fr", 0.6), ("en-CA", 0.3), ("zh-CN", 0.2), ("de", 0.1),),
    "RE": (("fr", 95.3), ("en-US", 4.4), ("en-GB", 0.3),),
    "RO": (("en-US", 64.5), ("ro", 25.4), ("en-GB", 5.3), ("hu", 1.7), ("ru", 0.7), ("de", 0.2),),
    "RS": (("en-US", 76.2), ("sr", 15.5), ("en-GB", 4.1), ("ru", 1.3), ("hu", 1.2),),
    "RU": (("ru", 90.6), ("en-US", 9.1), ("en-GB", 0.1), ("zh-CN", 0.1),),
    "RW": (("en-US", 96.3), ("en-GB", 2.3), ("fr", 0.8), ("ar", 0.4), ("es-ES", 0.1), ("ru", 0.1),),
    "SA": (("en-US", 54.4), ("ar", 42.4), ("en-GB", 2.7), ("fr", 0.1), ("en-CA", 0.1),),
    "SD": (("en-US", 88.3), ("ar", 9.2), ("en-GB", 2.0), ("en-CA", 0.3), ("de", 0.1),),
    "SE": (("sv-SE", 52.1), ("en-US", 39.0), ("en-GB", 3.4), ("ru", 2.4), ("de", 0.9),),
    "SG": (("en-US", 75.4), ("zh-CN", 16.7), ("en-GB", 4.1), ("id", 0.6), ("de", 0.2), ("ru", 0.2),),
    "SI": (("sl", 65.7), ("en-US", 29.1), ("en-GB", 3.1), ("de", 0.8), ("it", 0.3), ("ru", 0.1),),
    "SK": (("sk", 77.1), ("en-US", 16.5), ("cs", 2.1), ("hu", 1.3), ("en-GB", 1.3),),
    "SN": (("fr", 83.8), ("en-US", 15.4), ("es-ES", 0.3), ("it", 0.2), ("en-GB", 0.1),),
    "SO": (("en-US", 93.7), ("en-GB", 5.5), ("ar", 0.5), ("en-CA", 0.3),),
    "SV": (("es-ES", 69.4), ("en-US", 15.0), ("es-MX", 14.2), ("es-AR", 0.7), ("en-GB", 0.3),),
    "SY": (("en-US", 66.2), ("ar", 31.7), ("en-GB", 0.8), ("de", 0.4), ("tr", 0.3), ("ru", 0.1),),
    "TG": (("fr", 88.2), ("en-US", 11.6), ("es-ES", 0.1),),
    "TH": (("en-US", 51.9), ("th", 44.0), ("en-GB", 1.2), ("zh-CN", 0.8), ("de", 0.4), ("ru", 0.2),),
    "TN": (("fr", 70.9), ("en-US", 26.3), ("en-GB", 1.1), ("de", 0.6), ("it", 0.3), ("ar", 0.1),),
    "TR": (("tr", 81.6), ("en-US", 15.4), ("en-GB", 0.9), ("de", 0.7), ("ru", 0.6),),
    "TT": (("en-US", 95.1), ("en-GB", 4.2), ("zh-CN", 0.5), ("ja", 0.1),),
    "TW": (("zh-TW", 78.7), ("en-US", 15.3), ("zh-CN", 4.6), ("en-GB", 0.4), ("ja", 0.2), ("fr", 0.1),),
    "TZ": (("en-US", 96.1), ("en-GB", 3.0), ("fr", 0.2), ("de", 0.2), ("zh-CN", 0.2),),
    "UA": (("ru", 51.8), ("uk", 34.9), ("en-US", 12.4), ("en-GB", 0.5), ("pl", 0.1),),
    "UG": (("en-US", 95.5), ("en-GB", 4.0), ("en-CA", 0.1), ("zh-CN", 0.1), ("ru", 0.1), ("fr", 0.1),),
    "US": (("en-US", 97.3), ("zh-CN", 0.8), ("en-GB", 0.5), ("es-ES", 0.3), ("ru", 0.2),),
    "UY": (("es-ES", 69.7), ("en-US", 12.7), ("es-AR", 12.1), ("es-MX", 4.6), ("en-GB", 0.5),),
    "UZ": (("ru", 83.0), ("en-US", 15.4), ("uz", 0.7), ("en-GB", 0.4), ("tr", 0.1),),
    "VE": (("es-ES", 75.7), ("es-MX", 11.8), ("en-US", 8.6), ("es-AR", 3.0), ("es-CL", 0.4),),
    "VN": (("vi", 49.3), ("en-US", 47.3), ("en-GB", 1.8), ("zh-CN", 0.5), ("ru", 0.2), ("fr", 0.1),),
    "XK": (("en-US", 86.4), ("en-GB", 7.4), ("sr", 2.0), ("sq", 1.0), ("de", 1.0), ("fr", 0.6), ("sk", 0.2),),
    "YE": (("ar", 53.0), ("en-US", 45.3), ("en-CA", 0.4), ("ru", 0.4), ("id", 0.3), ("en-GB", 0.2), ("de", 0.1),),
    "ZA": (("en-US", 90.4), ("en-GB", 8.3), ("fr", 0.4), ("de", 0.1), ("zh-CN", 0.1),),
    "ZM": (("en-US", 92.3), ("en-GB", 6.4), ("zh-CN", 0.3), ("en-CA", 0.2), ("ru", 0.2), ("fr", 0.1), ("ja", 0.1), ("de", 0.1), ("es-MX", 0.1),),
    "ZW": (("en-US", 93.4), ("en-GB", 5.5), ("en-CA", 0.4), ("fr", 0.2), ("zh-CN", 0.2), ("ru", 0.1), ("pl", 0.1),),
}


def _country_build(country: str, egress_ip: str) -> str:
    """The build a session in ``country`` runs, picked by its egress IP.

    The IP plays the part of a seed, owner's decision (2026-10-06): the same
    address always gets the same build, so a session does not change language
    between two launches behind the same exit, and different addresses of a
    country spread over its builds in the measured proportions. NOT the
    persona seed: the language is a fact of the place the session comes from,
    and nothing else may move it.
    """
    shares = _COUNTRY_FIREFOX_BUILDS.get(country)
    if not shares:
        return DEFAULT_LOCALE
    ip = ipaddress.ip_address(egress_ip.strip()).compressed
    digest = int.from_bytes(hashlib.sha256(ip.encode("ascii")).digest()[:8], "big")
    point = digest / 2 ** 64 * sum(weight for _, weight in shares)
    for build, weight in shares:
        point -= weight
        if point < 0:
            return build
    return shares[-1][0]


def _language_list(tag: str) -> str:
    """The language list Firefox 151 builds for ``tag``. PRIVATE on purpose.

    It is applied in exactly one place, :func:`decide_session_locale`. It was
    public for one unreleased commit as `accept_languages`, for the wrapper's
    server to convert a context's locale; that server now asks
    ``decide_session_locale(tag).accept_languages``, so the table has one
    caller and no consumer can re-derive from a raw tag.

    ⛔ THIS FUNCTION USED TO RETURN TWO ENTRIES FOR EVERY LOCALE, AND FOR 89
    LANGUAGES OUT OF 95 THAT WAS WRONG. It returned `"<locale>, <base>"` with the
    comment "the desktop default form (e.g. `en-US, en`)". That form is right
    **only for English**, which is one of the six languages where Firefox does
    not append the tail.

    The source is `locale_service_default_accept_languages`
    (`intl/locale/rust/locale_service_glue/src/lib.rs:82-234`) and it does two
    things: a TABLE of per-language special cases, and then

        if add_en_us { format!("{langs}, en-US, en") } else { langs }

    with `add_en_us` TRUE by default and false only for en, my, ro, sco, sl, szl.
    The table's default branch is `"{lang}-{region}, {lang}"`, that is, exactly
    our old formula: only the tail was missing, which is the part that shows.

    What it cost, measured: an Italian profile emitted
        it-IT,it;q=0.9
    where a real Italian Firefox emits
        it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7
    on EVERY HTTP request and in `navigator.languages`.

    ⛔ AND WHY IT HAD NOT BEEN SEEN: the comparison against retail had been made
    against an **en-US** build, that is, the one case where the old formula and
    the real one coincide by construction. The control arm was badly chosen, not
    the measurement wrong. When comparing a function that depends on a parameter,
    the arm cannot sit on the value where the defect cancels itself out.
    """
    lang = tag.replace("_", "-")
    parts = lang.split("-")
    base = parts[0]
    region = parts[1] if len(parts) > 1 else None

    if base == "en":
        # the only branch with per-region sub-cases, and with no tail
        return {"CA": "en-CA, en-US, en", "GB": "en-GB, en",
                "ZA": "en-ZA, en-GB, en-US, en"}.get(region, "en-US, en")
    if base in _ACCEPT_LANG_NO_EN:
        return _ACCEPT_LANG_NO_EN[base]

    if base == "ca" and "valencia" in lang.lower():
        langs = "ca-valencia, ca"
    elif base == "zh" and region == "CN":
        langs = "zh-CN, zh, zh-TW, zh-HK"
    elif base in _ACCEPT_LANG_TABLE:
        langs = _ACCEPT_LANG_TABLE[base]
    elif region:
        langs = f"{base}-{region}, {base}"
    else:
        langs = base
    return f"{langs}, en-US, en"


def _is_auto(requested: Any) -> bool:
    return isinstance(requested, str) and requested.strip().lower() == "auto"


def _from_tag(tag: Optional[str]) -> SessionLocale:
    """One explicit tag: the tag FIRST, then the tail Firefox's table gives it.

    The caller asked for a language by name, and Playwright's contract is that
    ``locale`` IS ``navigator.language``. Until 36.32.x the table's list was
    taken whole, so ``locale="en-AU"`` answered "en-US" (there is no en-AU
    build) and ``"fr-FR"`` answered "fr": the request was silently replaced.
    The tail is still Firefox's, so the list is one a real Firefox produces
    when its user adds that language first in the settings: "en-AU, en-US, en".
    """
    tag = (tag or "").strip().replace("_", "-") or DEFAULT_LOCALE
    parts = tag.split("-")
    # A region subtag is two letters or three digits (BCP-47); "valencia" in
    # "ca-valencia" is a variant, not a region.
    is_region = [(len(p) == 2 and p.isalpha()) or (len(p) == 3 and p.isdigit())
                 for p in parts]
    region = next((p.upper() for p, r in zip(parts[1:], is_region[1:]) if r), None)
    tag = "-".join([parts[0].lower()] + [p.upper() if r else p
                                         for p, r in zip(parts[1:], is_region[1:])])
    tail = (t.strip() for t in _language_list(tag).split(","))
    languages = (tag,) + tuple(t for t in tail if t.lower() != tag.lower())
    return SessionLocale(languages=languages, region=region)


def _from_country(country: str, egress_ip: str) -> SessionLocale:
    """What the Firefox of a session in ``country`` declares, and the country.

    The list comes from the build `_country_build` picks, through the same
    Firefox function `_from_tag` uses; the region is the country, for the
    CONSENT cookie, which needs "where" even when the build has no region (the
    German build is "de": the region DE comes from the egress, not from the
    language).
    """
    country = country.upper()
    build = _country_build(country, egress_ip)
    languages = tuple(t.strip() for t in _language_list(build).split(","))
    return SessionLocale(languages=languages, region=country)


def _decide(requested: Optional[str], *, egress_ip: Optional[str],
            proxy: Optional[Dict[str, str]], may_discover: bool,
            discovery_failure: Optional[BaseException] = None) -> SessionLocale:
    """The decision, with the one switch only :func:`prepare_session_geo` needs.

    ``may_discover=False`` is for a caller that has ALREADY tried to discover
    the egress and failed: asking the network a second time in the same launch
    would double the wait for nothing, so the warned en-US fallback is taken at
    once, naming ``discovery_failure`` as the cause.
    """
    if not _is_auto(requested):
        return _from_tag(requested)
    from ._geo import _egress_country

    egress = _egress_country(
        egress_ip, proxy, may_discover=may_discover,
        discovery_failure=discovery_failure)
    return _from_country(egress.country, egress.ip) if egress else _from_tag(DEFAULT_LOCALE)


def decide_session_locale(
    requested: Optional[str] = "auto",
    *,
    egress_ip: Optional[str] = None,
    proxy: Optional[Dict[str, str]] = None,
) -> SessionLocale:
    """Decide the session language: the ONE place it is decided.

    ``requested`` is what the caller asked for: a BCP-47 tag ("it-IT", "pt_BR",
    "fr") or "auto". None or "" means nobody asked, which is en-US.

    * An explicit tag goes through Firefox's table and touches no network.
    * "auto" picks, by the egress IP, one of the Firefox builds people in the
      egress country run, in their measured proportions
      (`_COUNTRY_FIREFOX_BUILDS`), and takes what that build declares, through
      the SAME Firefox function. Behind a proxy it uses ``egress_ip`` (the address the caller
      already discovered through it) and NEVER discovers on its own: a missing
      address there means discovery failed, and the direct address would give
      the HOME country's language next to the proxy country's timezone. Without
      a proxy it reuses ``egress_ip`` when given and discovers the host's
      public address otherwise. Any failure falls back to en-US with a warning
      on stderr, never silently and never by raising.

    A launch normally does not call this directly: :func:`prepare_session_geo`
    calls it with the egress it has already paid for and returns the result as
    ``SessionGeo.locale``. A value that needs no egress - an explicit tag a
    browser context asks for - goes through :meth:`SessionLocale.of`, which
    refuses "auto" instead of discovering the host's address.
    """
    return _decide(requested, egress_ip=egress_ip, proxy=proxy, may_discover=True)
