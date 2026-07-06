"""Direct-launch helpers shared by the Playwright wrapper and the profile
manager: write a user.js from a prefs dict, and build the subprocess env the
patched binary reads at startup. No Playwright, no Qt."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def write_user_js(profile_dir: "str | os.PathLike[str]", prefs: Dict[str, Any]) -> Path:
    """Write ``prefs`` as ``user_pref(...)`` lines into ``<profile_dir>/user.js``.

    Creates ``profile_dir`` if missing; overwrites any existing ``user.js``.
    Values are JSON-encoded so Python ``True``/strings/ints map to JS
    ``true``/quoted-strings/numbers exactly as Firefox expects.
    """
    d = Path(profile_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = d / "user.js"
    lines = [f"user_pref({json.dumps(k)}, {json.dumps(v)});" for k, v in prefs.items()]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# IANA→POSIX TZ map — copied verbatim from the wrapper's launcher.py so the
# libc TZ env matches Date/Intl exactly (Arizona/Hawaii have no DST).
_IANA_TO_POSIX_TZ: Dict[str, str] = {
    "America/New_York":             "EST5EDT",
    "America/Detroit":              "EST5EDT",
    "America/Indiana/Indianapolis": "EST5EDT",
    "America/Kentucky/Louisville":  "EST5EDT",
    "America/Chicago":              "CST6CDT",
    "America/Denver":               "MST7MDT",
    "America/Los_Angeles":          "PST8PDT",
    "America/Phoenix":              "MST7",
    "America/Anchorage":            "AKST9AKDT",
    "Pacific/Honolulu":             "HST10",
}


def _tz_env(timezone: str) -> str:
    """POSIX TZ value for an IANA zone (falls back to the IANA name)."""
    return _IANA_TO_POSIX_TZ.get(timezone, timezone)


def build_launch_env(
    prefs: Dict[str, Any],
    *,
    timezone: Optional[str] = None,
    egress_ip: Optional[str] = None,
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Subprocess env for the patched binary. Mirrors the wrapper's _build_env.

    Fonts need NO env: the patched binary is self-contained (always bundle-only,
    the exposed set is the bundle itself). TZ tunes the libc clock.
    STEALTHFOX_WEBRTC_PUBLIC_IP feeds nICEr the proxy egress IP; an already-set
    value in base_env wins.
    """
    env = dict(os.environ if base_env is None else base_env)
    if timezone:
        env["TZ"] = _tz_env(timezone)
    webrtc_ip = env.get("STEALTHFOX_WEBRTC_PUBLIC_IP") or egress_ip
    if webrtc_ip:
        env["STEALTHFOX_WEBRTC_PUBLIC_IP"] = webrtc_ip
        env["STEALTHFOX_WEBRTC_DISABLE_IPV6"] = "1"
    return env
