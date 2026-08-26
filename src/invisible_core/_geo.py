"""Resolve the session timezone from the egress IP (``timezone="auto"``).

Approach B: discover the egress IP with one HTTP request - routed *through the
proxy* when one is set, otherwise a direct request that sees the host's own
public IP - then map IP → IANA timezone with an offline mmdb
(``daijro/geoip-all-in-one``, downloaded + cached by ``_geoip_db.py``).

Precedence (see ``resolve_session_timezone``):

    explicit IANA   → unchanged   explicit always wins
    "" / "auto"     → egress      ALWAYS resolve. With a proxy, from the proxy
                                  egress IP; without a proxy, from the host's
                                  own public IP. This is the default.

On failure:
    with a proxy    → raise       a foreign proxy paired with the host TZ is
                                  the precise ``timezone_mismatch`` signal, so
                                  we fail loudly rather than fall back silently.
    without a proxy → "" (host)   the host TZ is a safe default, so a transient
                                  lookup failure must not break the launch.
"""
from __future__ import annotations

import ipaddress
import time
from typing import Any, Dict, NamedTuple, Optional
from urllib.parse import quote

import requests


class GeoTimezoneError(RuntimeError):
    """Raised when ``timezone="auto"`` cannot resolve a valid IANA zone."""


# Plain-text IP echo endpoints (each returns just the caller's public IP).
_IP_ECHO_ENDPOINTS = (
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://checkip.amazonaws.com",
)

_SOCKS_SCHEMES = ("socks5://", "socks4://", "socks://")


def _proxy_is_set(proxy: Optional[Dict[str, str]]) -> bool:
    if not proxy:
        return False
    server = (proxy.get("server") or "").strip()
    return bool(server) and server.lower() != "direct://"


def _proxies_for_requests(proxy: Dict[str, str]) -> Dict[str, str]:
    """Translate our proxy dict into a ``requests`` proxies mapping.

    SOCKS5 uses the ``socks5h`` scheme so DNS is resolved proxy-side (matches
    ``network.proxy.socks_remote_dns=True`` in the Firefox path). HTTP/HTTPS
    pass through unchanged. Credentials are URL-encoded.
    """
    server = (proxy.get("server") or "").strip()
    low = server.lower()
    if low.startswith("socks5://") or low.startswith("socks://"):
        scheme = "socks5h"
    elif low.startswith("socks4://"):
        scheme = "socks4"
    elif low.startswith("https://"):
        scheme = "https"
    else:
        scheme = "http"

    host_port = server.split("://", 1)[1] if "://" in server else server
    if ":" not in host_port:
        # The same refusal `configure_proxy` makes, on the same dict. These two
        # are the only readers of a proxy endpoint in the package and they used
        # to disagree: this one built `socks5h://host` with no port and handed it
        # to requests while the browser side wrote no proxy pref at all, so one
        # half of a session was proxied and the other was not.
        raise ValueError(
            f"proxy server {server!r} has no port. An endpoint needs host:port "
            f"- e.g. socks5://host:1080")
    user = proxy.get("username") or ""
    pwd = proxy.get("password") or ""
    if user:
        auth = f"{quote(user, safe='')}:{quote(pwd, safe='')}@"
    else:
        auth = ""
    url = f"{scheme}://{auth}{host_port}"
    return {"http": url, "https": url}


def discover_egress_ip(
    proxy: Optional[Dict[str, str]] = None,
    *,
    timeout: float = 10.0,
    budget: float = 15.0,
) -> str:
    """Return the public egress IP.

    Routes the request through ``proxy`` when given (SOCKS support requires
    ``requests[socks]`` / PySocks); with ``proxy=None`` it makes a direct
    request that sees the host's own public IP. Tries each echo endpoint in
    turn; raises :class:`GeoTimezoneError` if none return a valid IP.

    ``timeout`` bounds ONE request; ``budget`` bounds the whole step. Both are
    needed, and having only the first is what made this the slowest thing in a
    launch: three endpoints tried in sequence at ten seconds each is a
    thirty-second worst case that nothing capped, and one launch in six spent
    35s here. A per-request timeout says how long to wait for a server; it
    cannot say how long the caller is willing to wait in total. The remaining
    budget is now handed to each request, so a slow first endpoint shortens the
    second rather than adding to it, and the step returns or raises within
    ``budget`` however many endpoints the list grows to.
    """
    proxies = _proxies_for_requests(proxy) if proxy else None
    last_err: Optional[Exception] = None
    deadline = time.monotonic() + budget
    tried = 0
    for url in _IP_ECHO_ENDPOINTS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        tried += 1
        try:
            # Una COPPIA, non uno scalare. `requests` applica un timeout scalare
            # alla fase di CONNESSIONE e poi di nuovo a quella di LETTURA, quindi
            # `timeout=10` puo' spendere venti secondi in una chiamata sola e
            # sfondare da solo un budget di quindici. Misurato il 2026-08-10 con
            # un proxy che aveva smesso di instradare: l'errore riportava
            # `20.1s` con `budget=15` e "1 of 3 endpoints", cioe' il primo si e'
            # mangiato tutto e la ridondanza degli altri due non e' mai entrata
            # in gioco. Il commento della funzione descriveva gia' l'intento
            # giusto - il budget limita il passo intero - ed era il codice a
            # implementarne un altro.
            #
            # Meta' per fase garantisce che UNA chiamata non superi il rimanente,
            # quindi il ciclo arriva davvero al secondo e al terzo endpoint
            # quando il primo tace.
            slice_ = min(timeout, remaining)
            resp = requests.get(
                url, proxies=proxies, timeout=(slice_ / 2, slice_ / 2)
            )
            resp.raise_for_status()
            ip = resp.text.strip()
            ipaddress.ip_address(ip)  # validate (raises ValueError if not an IP)
            return ip
        except Exception as exc:  # noqa: BLE001 - try the next endpoint
            last_err = exc
            continue
    spent = budget - (deadline - time.monotonic())
    exhausted = (
        f" The {budget:g}s budget ran out after {tried} of "
        f"{len(_IP_ECHO_ENDPOINTS)} endpoints."
        if tried < len(_IP_ECHO_ENDPOINTS)
        else ""
    )
    raise GeoTimezoneError(
        f"could not discover the proxy egress IP via {tried} "
        f"endpoint(s) in {spent:.1f}s (last error: {last_err!r}). For SOCKS "
        f"proxies make sure requests[socks] / PySocks is installed.{exhausted}"
    )


def _geo_record(ip: str, mmdb_path: Any) -> "Optional[Dict[str, Any]]":
    """L'UNICO punto che apre il database e legge un record.

    Le tre funzioni qui sotto - fuso, locale e coordinate - leggono lo STESSO
    record dello STESSO IP, e prima di questa funzione due di loro ripetevano
    le stesse tre righe. Aggiungere la terza avrebbe fatto tre copie di come si
    legge il database, che e' la regola 16 violata mentre la si applica.

    Torna ``None`` se l'IP non c'e': chi chiama decide se e' fatale (il fuso,
    che dietro un proxy deve fallire rumorosamente) o no (il locale, che ha un
    ripiego dichiarato).
    """
    import maxminddb

    with maxminddb.open_database(str(mmdb_path)) as reader:
        record = reader.get(ip)
    return record if isinstance(record, dict) else None


def ip_to_coordinates(ip: str, mmdb_path: Any) -> "tuple[float, float]":
    """Map ``ip`` -> (latitudine, longitudine) dallo stesso record del fuso.

    ⛔ E' la fonte UNICA della posizione dichiarata: il motore non chiede piu'
    niente all'hardware (niente WiFi, niente GPS, niente cella) e non chiede
    niente a Google. La posizione esce dall'IP di uscita del proxy, esattamente
    come il fuso e la lingua, quindi le tre cose non possono contraddirsi.

    ⛔ LA PRECISIONE NON VIENE DA QUI, e non e' una dimenticanza: misurato il
    2026-08-20 su un record vero, ``location`` porta ``latitude``,
    ``longitude`` e ``time_zone`` e **non** ``accuracy_radius``. Dichiararla e'
    un'altra decisione, e vive nel Profile: una posizione derivata da un IP con
    una precisione da GPS sarebbe incoerente per costruzione.

    Solleva :class:`GeoTimezoneError` - stessa classe del fuso, perche' e' lo
    stesso guasto - se l'IP manca o il record non porta le coordinate. **Non si
    inventa un ripiego**: senza dichiarazione il motore rifiuta, che e' la
    regola 7.
    """
    record = _geo_record(ip, mmdb_path)
    if not record:
        raise GeoTimezoneError(f"egress IP {ip} not present in the geoip database")
    loc = record.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if lat is None or lon is None:
        raise GeoTimezoneError(
            f"no coordinates for egress IP {ip} in the geoip database"
        )
    return float(lat), float(lon)


def ip_to_timezone(ip: str, mmdb_path: Any) -> str:
    """Map ``ip`` to its IANA timezone using the offline mmdb.

    Reads the standard MaxMind ``location.time_zone`` field and validates it
    against the system tz database. Raises :class:`GeoTimezoneError` if the IP
    is absent from the DB or the zone is missing / not a valid IANA name.
    """
    record = _geo_record(ip, mmdb_path)
    if not record:
        raise GeoTimezoneError(f"egress IP {ip} not present in the geoip database")
    tz = (record.get("location") or {}).get("time_zone")
    if not tz:
        raise GeoTimezoneError(f"no timezone for egress IP {ip} in the geoip database")
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise GeoTimezoneError(
            f"geoip returned an invalid IANA zone {tz!r} for {ip}: {exc}"
        ) from exc
    return tz


# ISO 3166 country code -> the primary BCP-47 locale a real Windows machine in that
# country most commonly runs. Multi-language countries use the majority language; the
# user can always force a specific locale instead of "auto". Unknown -> en-US.
_COUNTRY_LOCALE = {
    "US": "en-US", "GB": "en-GB", "CA": "en-CA", "AU": "en-AU", "NZ": "en-NZ", "IE": "en-IE",
    "ZA": "en-ZA", "IN": "en-IN", "SG": "en-SG", "PH": "en-PH",
    "FR": "fr-FR", "BE": "fr-BE", "LU": "fr-LU",
    "DE": "de-DE", "AT": "de-AT", "CH": "de-CH",
    "IT": "it-IT", "ES": "es-ES", "PT": "pt-PT", "NL": "nl-NL",
    "SE": "sv-SE", "NO": "nb-NO", "DK": "da-DK", "FI": "fi-FI", "IS": "is-IS",
    "PL": "pl-PL", "CZ": "cs-CZ", "SK": "sk-SK", "HU": "hu-HU", "RO": "ro-RO",
    "GR": "el-GR", "BG": "bg-BG", "HR": "hr-HR", "RS": "sr-RS", "SI": "sl-SI",
    "RU": "ru-RU", "UA": "uk-UA", "TR": "tr-TR", "IL": "he-IL",
    "BR": "pt-BR", "MX": "es-MX", "AR": "es-AR", "CL": "es-CL", "CO": "es-CO", "PE": "es-PE",
    "JP": "ja-JP", "KR": "ko-KR", "CN": "zh-CN", "TW": "zh-TW", "HK": "zh-HK",
    "ID": "id-ID", "TH": "th-TH", "VN": "vi-VN", "MY": "ms-MY",
    "SA": "ar-SA", "AE": "ar-AE", "EG": "ar-EG",
}


#: The EEA plus the UK and Switzerland, i.e. every country where a real Google
#: CONSENT cookie carries `<lang>+<COUNTRY>` rather than the `en+FX` a non-EU
#: visitor gets. A finite, knowable set; the alternative was a 22-row timezone
#: table in the wrapper that silently answered "non-EU English" for every
#: country it did not list.
CONSENT_REGION_COUNTRIES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",            # EU 27
    "IS", "LI", "NO",            # EEA
    "GB", "CH",                  # UK and Switzerland behave the same way here
})


def consent_region_lang(locale: str) -> "tuple[str, str]":
    """`(region_token, lang)` for a Google CONSENT cookie, from the LOCALE.

    WHY IT TAKES A LOCALE. This used to live in the wrapper as a 22-row IANA
    timezone table (`_TZ_TO_REGION`), while the locale a session actually runs
    with is resolved HERE, from the egress country, against a 55-row table. Two
    tables for one fact, and they drifted exactly the way two tables do: a
    Romanian session resolved `ro-RO` for `navigator.language` and fell through
    to `("FX", "en")` for the cookie, because `Europe/Bucharest` was not one of
    the 22. A page that reads the cookie and the language sees a Romanian
    browser claiming to be a non-EU English one.

    Deriving from the locale removes the second table rather than extending it:
    every locale this package can produce is covered by construction, including
    the ones nobody has added to a list yet.
    """
    tag = (locale or "en-US").replace("_", "-")
    parts = tag.split("-")
    lang = parts[0].lower()
    country = parts[-1].upper() if len(parts) > 1 else ""
    if country in CONSENT_REGION_COUNTRIES:
        return (country, lang)
    return ("FX", lang if country else "en")


def ip_to_locale(ip: str, mmdb_path: Any) -> str:
    """Map ``ip`` -> a BCP-47 locale via the MaxMind ``country.iso_code`` field, so the
    browser language stays consistent with the proxy egress country. Falls back to
    ``en-US`` for IPs absent from the DB or countries we don't map."""
    record = _geo_record(ip, mmdb_path)
    cc = ((record.get("country") or {}).get("iso_code") or "") if record else ""
    return _COUNTRY_LOCALE.get(cc.upper(), "en-US")


def resolve_session_locale(egress_ip: Optional[str], proxy: Optional[Dict[str, str]]) -> str:
    """Resolve ``locale="auto"`` to a BCP-47 locale from the egress country. Behind a proxy
    it reuses the already-discovered ``egress_ip`` (no extra round-trip); without a proxy it
    discovers the host's public IP. On any failure it returns ``en-US`` (never breaks launch
    - locale is cosmetic, unlike timezone which traps a foreign-proxy mismatch)."""
    from .download import ensure_geoip_mmdb

    try:
        # ⛔ SI RIUSA CIO' CHE IL CHIAMANTE HA GIA': `prepare_session_geo` ha
        # gia' pagato questo round-trip e adesso porta il risultato anche senza
        # proxy. Si scopre solo se non c'e' niente da riusare - il caso di un
        # fuso esplicito, in cui nessuno ha ancora chiesto niente alla rete.
        #
        # E la scoperta resta VIETATA dietro un proxy: se li' `egress_ip` manca,
        # la scoperta e' fallita, e cadere sull'indirizzo diretto derivarebbe la
        # lingua dal paese di CASA mentre il fuso dice quello del proxy. Meglio
        # `en-US` di una contraddizione fra due campi.
        ip = egress_ip
        if ip is None and not _proxy_is_set(proxy):
            ip = discover_egress_ip(None)
        if ip is None:
            _warn_locale_fallback(proxy, "no egress IP was resolved")
            return "en-US"
        return ip_to_locale(ip, ensure_geoip_mmdb())
    except Exception as exc:  # noqa: BLE001
        _warn_locale_fallback(proxy, f"{type(exc).__name__}: {exc}")
        return "en-US"


def _warn_locale_fallback(proxy: Optional[Dict[str, str]], why: str) -> None:
    """Say that the locale was NOT resolved, on stderr, every time.

    This used to be two bare returns. The docstring called locale "cosmetic",
    and for a lone session it nearly is - but the timezone is resolved from the
    SAME egress IP and does not fall back, so a failure here produces a session
    whose timezone says one country and whose language says the United States.
    That pairing is a cross-field inconsistency of exactly the kind the
    timezone trap exists to prevent, and it was reaching users with no signal
    at all.

    The OUTCOME is deliberately unchanged: raising here would break launches
    that work today, on a field that is recoverable by passing `locale=`
    explicitly. What changes is that it stops being invisible - an absent
    signal must be loud, never silent.
    """
    import sys

    where = "behind a proxy" if _proxy_is_set(proxy) else "with no proxy"
    print(
        f"invisible-core: could not resolve the session locale {where} ({why}); "
        f"falling back to en-US. The timezone is still resolved from the egress "
        f"IP, so this session may pair a non-US timezone with a US language - "
        f"pass locale=\"xx-XX\" to set it explicitly.",
        file=sys.stderr,
    )


class SessionGeo(NamedTuple):
    """Geo facts resolved once per session from a single egress round-trip.

    ``timezone`` follows the precedence in the module docstring.

    ⛔ ``egress_ip`` E' UN FATTO, NON UNA DECISIONE: l'indirizzo da cui questa
    sessione esce davvero, scoperto una volta, con o senza proxy. Se poi quel
    valore venga DICHIARATO al motore come srflx lo decide
    :meth:`srflx_da_dichiarare`, e la risposta senza proxy e' no.

    Fino al 2026-08-26 questo campo valeva ``None`` senza proxy, e il "no" era
    espresso proprio da quel ``None``. Un campo solo per due significati:
    l'effetto era che ``prepare_session_geo`` scopriva l'indirizzo per il fuso,
    **lo buttava via**, e ``resolve_session_locale`` doveva riscoprirlo. Due
    richieste identiche a un servizio esterno, dall'indirizzo vero, prima che il
    browser esista - dove un utente vero ne fa zero. Il fatto adesso si porta,
    e il "no" vive dove viveva gia' la decisione.
    """

    timezone: str
    egress_ip: Optional[str]
    #: La posizione dichiarata, dallo STESSO record dell'IP di uscita da cui
    #: escono fuso e lingua. ``None`` quando non c'e' un IP da cui derivarla
    #: (nessun proxy, o scoperta fallita): in quel caso il motore NON riceve
    #: nessuna dichiarazione e rifiuta, invece di chiedere all'hardware.
    #:
    #: Hanno un default perche' ``SessionGeo`` si costruisce per posizione con
    #: due argomenti in sei punti fra codice e test: aggiungerli senza default
    #: sarebbe stato un cambiamento non retrocompatibile di un tipo esportato.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    #: ⛔ UN INTERRUTTORE, E IL SUO DEFAULT E' QUELLO PRUDENTE.
    #:
    #: La prima stesura era un campo che portava l'INDIRIZZO da dichiarare, con
    #: default ``None``. Sbagliato, e un test lo ha colto subito: ``SessionGeo``
    #: si costruisce per posizione in sei punti fra codice e test, e tutti
    #: quelli che non conoscevano il campo nuovo hanno smesso di dichiarare il
    #: srflx **per distrazione**. Cioe' il default cadeva dal lato che porta al
    #: messaggio peggiore che un rilevatore possa scrivere.
    #:
    #: Invertito: ``False`` significa "dichiara", che e' il comportamento di
    #: sempre, e per spegnerlo bisogna dirlo. Vale ``True`` SOLO quando l'uscita
    #: ha UDP dimostrato E coerente, perche' li' il srflx vero nasce gia' con
    #: l'indirizzo giusto e dichiararne uno aggiungerebbe un candidato senza
    #: allocazione corrispondente.
    srflx_soppresso: bool = False

    def srflx_da_dichiarare(self) -> Optional[str]:
        """L'indirizzo che il motore deve annunciare come srflx, o ``None``.

        ⛔ E' L'UNICO POSTO in cui questa domanda riceve risposta. I due
        costruttori di env - ``launch.build_launch_env`` nel core e
        ``_session.build_env`` nel wrapper - la chiamano, non la ricalcolano:
        erano gia' due punti di atterraggio, e la stessa regola scritta due
        volte avrebbe potuto divergere.
        """
        return None if self.srflx_soppresso else self.egress_ip


def _srflx_soppresso(proxy: Optional[Dict[str, str]],
                     egress_ip: Optional[str]) -> bool:
    """Dichiarare un srflx sintetico, o lasciar passare quello vero?

    Il criterio viene dal codice di un rilevatore vero, letto e non dedotto
    (`docs_research/scrapfly-re/00-WEBRTC-LEAK.md`): la sua configurazione non
    contiene nessuno STUN, solo TURN, e la username dell'allocazione e' lo
    stesso identificativo che il POST di verifica manda al backend. Quindi in un
    browser onesto **un srflx puo' venire soltanto da un'allocazione riuscita**,
    e candidato lato client e prova lato server coincidono per costruzione. E'
    quell'implicazione che un candidato dichiarato rompe.

    Da qui la regola, che ha tre rami:

    * **nessun proxy**: lo STUN vero risponde con l'indirizzo da cui si esce
      davvero, quindi il srflx nasce gia' giusto e con la sua allocazione. Non
      si dichiara niente. Fino al 2026-08-26 questo ramo non era scritto qui:
      lo stesso esito usciva dal fatto che ``egress_ip`` valesse ``None`` senza
      proxy, cioe' la decisione era codificata nell'ASSENZA di un fatto. Le due
      cose sono ora separate, e questo ramo dice a voce cio' che prima si
      otteneva per effetto collaterale.
    * **UDP dimostrato e coerente** (l'UDP esce dallo stesso indirizzo del TCP):
      il srflx vero nascera' gia' con l'indirizzo giusto. Non si dichiara niente:
      dichiarare aggiungerebbe un candidato senza allocazione corrispondente.
    * **tutto il resto**: si dichiara l'IP di uscita, che e' il comportamento di
      sempre. Senza un srflx il rilevatore scrive *"Javascript is manipulated"*,
      cioe' accusa il browser; con lui scrive *"VPN/PROXY detected"*, cioe'
      accusa la rete. La differenza fra i due messaggi e' questa riga.

    ⛔ E LA SONDA NON PUO' FAR FALLIRE UN LANCIO. Qualunque cosa vada storta -
    rete, timeout, un campo che non c'e' - si ricade sul ramo prudente. Una
    capacita' si sfrutta solo quando e' DIMOSTRATA.
    """
    if not egress_ip:
        return False  # niente da dichiarare comunque: il ramo prudente
    if not _proxy_is_set(proxy):
        return True  # connessione diretta: il srflx vero e' gia' la verita'
    # ⛔ PRIMA DELLA SONDA, perche' se questo e' falso la sonda non serve.
    #
    # Che l'USCITA porti UDP coerente non basta: deve anche essere il BROWSER a
    # mandarci l'UDP. Con `network.proxy.socks_remote_udp` spenta l'UDP scavalca
    # il proxy, quindi un srflx vero nascerebbe con l'indirizzo di CASA - e
    # smettere di dichiarare, li', sarebbe una fuga vera invece di un rimedio.
    #
    # La prima stesura di questa funzione, il 2026-08-25, non lo controllava. Il
    # ramo era irraggiungibile per fortuna (nessun fornitore ha UDP usabile) e
    # non per costruzione, che e' esattamente la forma di difetto che questo
    # progetto paga: una condizione la cui sicurezza dipende da un fatto che non
    # verifica.
    from ._proxy import INSTRADIAMO_UDP_NEL_SOCKS
    if not INSTRADIAMO_UDP_NEL_SOCKS:
        return False
    try:
        from ._capacita import capacita
        c = capacita(proxy, uscita_tcp_nota=egress_ip)
    except Exception:  # noqa: BLE001
        return False
    return c.get("udp") is True and c.get("udp_coerente") is True


def prepare_session_geo(
    timezone: str, proxy: Optional[Dict[str, str]]
) -> SessionGeo:
    """Resolve the session timezone AND the proxy egress IP in ONE round-trip.

    The egress IP is discovered once and reused for both the timezone mapping
    (when ``timezone`` is ``""``/``"auto"``) and the WebRTC public-IP override.
    Timezone precedence is identical to :func:`resolve_session_timezone`; the
    egress IP is best-effort for the WebRTC side (a discovery failure that the
    timezone path doesn't need won't break the launch - but if the timezone
    path *does* need it behind a proxy, that path still fails loudly).
    """
    from .download import ensure_geoip_mmdb

    tz = (timezone or "").strip()
    proxy_set = _proxy_is_set(proxy)

    # One discovery, reused below. Behind a proxy we always want the egress IP
    # (for WebRTC) regardless of the timezone setting.
    egress_ip: Optional[str] = None
    egress_err: Optional[Exception] = None
    if proxy_set:
        try:
            egress_ip = discover_egress_ip(proxy)
        except Exception as exc:  # noqa: BLE001
            egress_err = exc

    # Timezone resolution - same precedence as resolve_session_timezone.
    def _coordinate(ip: "Optional[str]") -> "tuple[Optional[float], Optional[float]]":
        """Le coordinate sono BEST-EFFORT, il fuso no, e la differenza e' voluta.

        Un fuso sbagliato dietro un proxy e' la trappola `tz_mismatch` e deve
        far fallire il lancio. Una posizione ASSENTE invece non e' una
        contraddizione: e' un browser a cui nessuno ha ancora chiesto dove sia,
        e il motore la rifiuta in modo pulito. Far fallire il lancio per questo
        sarebbe piu' fragile senza essere piu' fedele.
        """
        if not ip:
            return None, None
        try:
            return ip_to_coordinates(ip, ensure_geoip_mmdb())
        except Exception:  # noqa: BLE001
            return None, None

    if tz and tz.lower() != "auto":
        lat, lon = _coordinate(egress_ip)
        return SessionGeo(tz, egress_ip, lat, lon,
                          _srflx_soppresso(proxy, egress_ip))  # explicit IANA wins
    try:
        ip = egress_ip if proxy_set else discover_egress_ip(None)
        if ip is None:  # proxy set but discovery failed above
            raise egress_err or GeoTimezoneError("egress IP discovery failed")
        lat, lon = _coordinate(ip)
        # ⛔ SI PORTA `ip`, NON `egress_ip`. Dietro un proxy sono lo stesso
        # valore; senza, `ip` e' il fatto che questo giro di rete ha appena
        # pagato e `egress_ip` e' `None`. Portare il secondo significava
        # buttare la scoperta e farla rifare a `resolve_session_locale`.
        return SessionGeo(ip_to_timezone(ip, ensure_geoip_mmdb()), ip, lat, lon,
                          _srflx_soppresso(proxy, ip))
    except Exception:
        if proxy_set:
            raise  # fail-early behind a proxy (timezone_mismatch trap)
        return SessionGeo("", None)  # no proxy: host TZ is a safe fallback


def resolve_session_timezone(
    timezone: str, proxy: Optional[Dict[str, str]]
) -> str:
    """Map the user's ``timezone`` setting to a concrete IANA zone (or ``""``).

    Timezone-only path (no WebRTC side effects): an explicit IANA zone wins and
    triggers NO network call; ``""``/``"auto"`` resolve from the egress IP. The
    launch path uses :func:`prepare_session_geo` instead (which additionally
    returns the egress IP for WebRTC); this standalone resolver is kept for
    third-party integrations that only want the zone. See the module docstring
    for the precedence table.
    """
    tz = (timezone or "").strip()
    if tz and tz.lower() != "auto":
        return tz  # explicit IANA wins - no egress lookup
    from .download import ensure_geoip_mmdb

    proxy_set = _proxy_is_set(proxy)
    try:
        ip = discover_egress_ip(proxy if proxy_set else None)
        return ip_to_timezone(ip, ensure_geoip_mmdb())
    except Exception:
        if proxy_set:
            raise  # fail-early behind a proxy (timezone_mismatch trap)
        return ""  # no proxy: host TZ is a safe fallback
