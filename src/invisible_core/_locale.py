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

    ``region`` is the region subtag of the tag the decision started from (for
    "auto", the egress country the table mapped), or None when that tag had
    none. It is NOT a language: it answers "where", which the language list
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
    """Apply the table to one explicit tag. The one application of it."""
    tag = (tag or "").strip().replace("_", "-") or DEFAULT_LOCALE
    parts = tag.split("-")
    # A region subtag is two letters or three digits (BCP-47); "valencia" in
    # "ca-valencia" is a variant, not a region.
    region = next((p.upper() for p in parts[1:]
                   if (len(p) == 2 and p.isalpha()) or (len(p) == 3 and p.isdigit())),
                  None)
    languages = tuple(t.strip() for t in _language_list(tag).split(","))
    return SessionLocale(languages=languages, region=region)


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
    from ._geo import _egress_locale_tag

    return _from_tag(_egress_locale_tag(
        egress_ip, proxy, may_discover=may_discover,
        discovery_failure=discovery_failure))


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
    * "auto" maps the egress country to a tag and then goes through the SAME
      table. Behind a proxy it uses ``egress_ip`` (the address the caller
      already discovered through it) and NEVER discovers on its own: a missing
      address there means discovery failed, and the direct address would give
      the HOME country's language next to the proxy country's timezone. Without
      a proxy it reuses ``egress_ip`` when given and discovers the host's
      public address otherwise. Any failure falls back to en-US with a warning
      on stderr, never silently and never by raising.

    A launch normally does not call this directly: :func:`prepare_session_geo`
    calls it with the egress it has already paid for and returns the result as
    ``SessionGeo.locale``. Call it yourself for a value that needs no egress,
    such as an explicit tag a browser context asks for.
    """
    return _decide(requested, egress_ip=egress_ip, proxy=proxy, may_discover=True)


def _coerce_session_locale(value: Any) -> SessionLocale:
    """What a PURE builder (prefs, cookies) accepts as a locale.

    A :class:`SessionLocale` passes through; a tag is decided through
    :func:`decide_session_locale`, the same derivation a launch uses, so a
    caller handing a tag and a caller handing the decision get identical
    output. "auto" is REFUSED: resolving it needs the egress and possibly the
    network, which a pure builder must not reach for. It used to be written
    into the prefs verbatim, as the language "auto".
    """
    if isinstance(value, SessionLocale):
        return value
    if _is_auto(value):
        raise ValueError(
            'locale="auto" cannot be resolved by a pure builder: it needs the '
            "egress address. Pass the SessionLocale from prepare_session_geo() "
            "(its .locale) or decide_session_locale(), or an explicit tag.")
    return _from_tag(value)
