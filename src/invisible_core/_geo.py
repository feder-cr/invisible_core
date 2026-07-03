"""Resolve the session timezone from the egress IP (``timezone="auto"``).

Approach B: discover the egress IP with one HTTP request — routed *through the
proxy* when one is set, otherwise a direct request that sees the host's own
public IP — then map IP → IANA timezone with an offline mmdb
(``daijro/geoip-all-in-one``, downloaded + cached by ``download.py``).

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
    user = proxy.get("username") or ""
    pwd = proxy.get("password") or ""
    if user:
        auth = f"{quote(user, safe='')}:{quote(pwd, safe='')}@"
    else:
        auth = ""
    url = f"{scheme}://{auth}{host_port}"
    return {"http": url, "https": url}


def discover_egress_ip(
    proxy: Optional[Dict[str, str]] = None, *, timeout: float = 10.0
) -> str:
    """Return the public egress IP.

    Routes the request through ``proxy`` when given (SOCKS support requires
    ``requests[socks]`` / PySocks); with ``proxy=None`` it makes a direct
    request that sees the host's own public IP. Tries each echo endpoint in
    turn; raises :class:`GeoTimezoneError` if none return a valid IP.
    """
    proxies = _proxies_for_requests(proxy) if proxy else None
    last_err: Optional[Exception] = None
    for url in _IP_ECHO_ENDPOINTS:
        try:
            resp = requests.get(url, proxies=proxies, timeout=timeout)
            resp.raise_for_status()
            ip = resp.text.strip()
            ipaddress.ip_address(ip)  # validate (raises ValueError if not an IP)
            return ip
        except Exception as exc:  # noqa: BLE001 - try the next endpoint
            last_err = exc
            continue
    raise GeoTimezoneError(
        f"could not discover the proxy egress IP via {len(_IP_ECHO_ENDPOINTS)} "
        f"endpoints (last error: {last_err!r}). For SOCKS proxies make sure "
        f"requests[socks] / PySocks is installed."
    )


def ip_to_timezone(ip: str, mmdb_path: Any) -> str:
    """Map ``ip`` to its IANA timezone using the offline mmdb.

    Reads the standard MaxMind ``location.time_zone`` field and validates it
    against the system tz database. Raises :class:`GeoTimezoneError` if the IP
    is absent from the DB or the zone is missing / not a valid IANA name.
    """
    import maxminddb

    with maxminddb.open_database(str(mmdb_path)) as reader:
        record = reader.get(ip)
    if not record:
        raise GeoTimezoneError(f"egress IP {ip} not present in the geoip database")
    tz = ((record.get("location") or {}) if isinstance(record, dict) else {}).get(
        "time_zone"
    )
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


def ip_to_locale(ip: str, mmdb_path: Any) -> str:
    """Map ``ip`` -> a BCP-47 locale via the MaxMind ``country.iso_code`` field, so the
    browser language stays consistent with the proxy egress country. Falls back to
    ``en-US`` for IPs absent from the DB or countries we don't map."""
    import maxminddb

    with maxminddb.open_database(str(mmdb_path)) as reader:
        record = reader.get(ip)
    cc = ""
    if isinstance(record, dict):
        cc = ((record.get("country") or {}).get("iso_code") or "")
    return _COUNTRY_LOCALE.get(cc.upper(), "en-US")


def resolve_session_locale(egress_ip: Optional[str], proxy: Optional[Dict[str, str]]) -> str:
    """Resolve ``locale="auto"`` to a BCP-47 locale from the egress country. Behind a proxy
    it reuses the already-discovered ``egress_ip`` (no extra round-trip); without a proxy it
    discovers the host's public IP. On any failure it returns ``en-US`` (never breaks launch
    — locale is cosmetic, unlike timezone which traps a foreign-proxy mismatch)."""
    from .download import ensure_geoip_mmdb

    try:
        ip = egress_ip if _proxy_is_set(proxy) else discover_egress_ip(None)
        if ip is None:
            return "en-US"
        return ip_to_locale(ip, ensure_geoip_mmdb())
    except Exception:  # noqa: BLE001
        return "en-US"


class SessionGeo(NamedTuple):
    """Geo facts resolved once per session from a single egress round-trip.

    ``timezone`` follows the precedence in the module docstring.
    ``egress_ip`` is the proxy egress IP (the IP the *outside world* sees) when
    a proxy is set, else ``None`` — it feeds the WebRTC srflx override, which is
    only meaningful behind a proxy (a direct connection's real STUN already
    reports the truthful public IP, so we leave it alone).
    """

    timezone: str
    egress_ip: Optional[str]


def prepare_session_geo(
    timezone: str, proxy: Optional[Dict[str, str]]
) -> SessionGeo:
    """Resolve the session timezone AND the proxy egress IP in ONE round-trip.

    The egress IP is discovered once and reused for both the timezone mapping
    (when ``timezone`` is ``""``/``"auto"``) and the WebRTC public-IP override.
    Timezone precedence is identical to :func:`resolve_session_timezone`; the
    egress IP is best-effort for the WebRTC side (a discovery failure that the
    timezone path doesn't need won't break the launch — but if the timezone
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

    # Timezone resolution — same precedence as resolve_session_timezone.
    if tz and tz.lower() != "auto":
        return SessionGeo(tz, egress_ip)  # explicit IANA wins
    try:
        ip = egress_ip if proxy_set else discover_egress_ip(None)
        if ip is None:  # proxy set but discovery failed above
            raise egress_err or GeoTimezoneError("egress IP discovery failed")
        return SessionGeo(ip_to_timezone(ip, ensure_geoip_mmdb()), egress_ip)
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
        return tz  # explicit IANA wins — no egress lookup
    from .download import ensure_geoip_mmdb

    proxy_set = _proxy_is_set(proxy)
    try:
        ip = discover_egress_ip(proxy if proxy_set else None)
        return ip_to_timezone(ip, ensure_geoip_mmdb())
    except Exception:
        if proxy_set:
            raise  # fail-early behind a proxy (timezone_mismatch trap)
        return ""  # no proxy: host TZ is a safe fallback
