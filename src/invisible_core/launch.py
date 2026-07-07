"""Direct-launch helpers shared by the Playwright wrapper and the profile
manager: write a user.js from a prefs dict, and build the subprocess env the
patched binary reads at startup. No Playwright, no Qt."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


def _pref_literal(v: Any) -> str:
    """Serialize a pref value the way Firefox's prefs parser accepts it.

    Firefox prefs are int / bool / string only - there is no float pref type;
    a fractional value (e.g. device-pixel-ratio) is stored as a STRING and the
    float pref parses it back. ``json.dumps`` would emit a bare ``1.25``, which
    Firefox rejects with ``prefs parse error: unexpected character`` and which
    invalidates every ``user_pref`` line after it. (bool is a subclass of int
    but ``json.dumps`` already maps it to ``true``/``false``, so it is handled
    before the float check matters.)
    """
    if isinstance(v, float):
        return json.dumps(str(v))
    return json.dumps(v)


def write_user_js(profile_dir: "str | os.PathLike[str]", prefs: Dict[str, Any]) -> Path:
    """Write ``prefs`` as ``user_pref(...)`` lines into ``<profile_dir>/user.js``.

    Creates ``profile_dir`` if missing; overwrites any existing ``user.js``.
    Values are serialized so Python ``True``/strings/ints/floats map to what
    Firefox expects (``true``/quoted-strings/numbers, floats as strings).
    """
    d = Path(profile_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = d / "user.js"
    lines = [f"user_pref({json.dumps(k)}, {_pref_literal(v)});" for k, v in prefs.items()]
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


@dataclass
class LaunchPlan:
    """Everything needed to spawn the patched Firefox for one identity."""
    binary: str
    profile_dir: Path
    argv: List[str]
    env: Dict[str, str]


def build_launch_plan(
    seed: int,
    *,
    profile_dir: "str | os.PathLike[str]",
    proxy: Optional[Dict[str, str]] = None,
    timezone: str = "auto",
    locale: str = "auto",
    pin: Optional[Dict[str, Any]] = None,
    binary_ver: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
    url: str = "about:blank",
) -> LaunchPlan:
    """The single direct-launch entry point (no Playwright, no Qt).

    Give it a ``seed`` and a ``profile_dir`` (plus optionally a concrete
    ``proxy`` dict, ``timezone``/``locale`` - both default to ``"auto"`` and are
    resolved from the proxy egress - a ``pin`` and a ``binary_ver``); it resolves
    geo, generates the fingerprint profile, translates it to prefs, writes
    ``user.js`` into ``profile_dir``, and returns the binary path + argv + env.
    Both the profile manager and any other direct-launch consumer use this so the
    session-setup logic lives in one place (mirrors the wrapper's __aenter__).
    ``proxy`` must already be a concrete endpoint dict (SOCKS/HTTP), not an intent.
    """
    # Lazy imports keep launch.py free of import-order coupling with the rest of
    # the package (this module is imported near the end of invisible_core/__init__).
    from .download import ensure_binary
    from ._fpforge import generate_profile
    from ._geo import prepare_session_geo, resolve_session_locale
    from ._proxy import configure_proxy
    from .prefs import translate_profile_to_prefs

    binary = str(ensure_binary(binary_ver) if binary_ver else ensure_binary())
    # Resolves timezone="auto" from the egress AND discovers the egress IP;
    # raises behind a dead proxy (fail-early, by design).
    geo = prepare_session_geo(timezone, proxy)
    loc = locale
    if (locale or "").strip().lower() == "auto":
        loc = resolve_session_locale(geo.egress_ip, proxy)
    fp = generate_profile(seed=seed, pin=pin)
    prefs = translate_profile_to_prefs(fp, locale=loc, timezone=geo.timezone)
    configure_proxy(proxy, prefs)  # SOCKS-auth prefs (no-op for http/https/None)
    # Persistent-profile direct launch: the browser can be hard-killed (a manager
    # Stop, or killed mid-startup on a rapid relaunch). Keep Firefox from counting
    # that as a startup crash (the "closed unexpectedly / Safe Mode" prompt) or
    # offering to restore a "crashed" session. A caller can override either by
    # setting the pref in ``pin``/prefs beforehand (setdefault below).
    prefs.setdefault("toolkit.startup.max_resumed_crashes", -1)
    prefs.setdefault("browser.sessionstore.resume_from_crash", False)
    pdir = Path(profile_dir)
    write_user_js(pdir, prefs)
    env = build_launch_env(prefs, timezone=geo.timezone or None, egress_ip=geo.egress_ip)
    argv = [binary, "-no-remote", "-profile", str(pdir)]
    argv += list(extra_args or [])
    if url:
        argv.append(url)
    return LaunchPlan(binary=binary, profile_dir=pdir, argv=argv, env=env)
