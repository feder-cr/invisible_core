"""Direct-launch helpers shared by the Playwright wrapper and the profile
manager: write a user.js from a prefs dict, and build the subprocess env the
patched binary reads at startup. No Playwright, no Qt."""
from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass, field
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


# IANA→POSIX TZ map - copied verbatim from the wrapper's launcher.py so the
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


def tz_env(timezone: str) -> str:
    """The value to put in ``TZ`` for an IANA zone.

    PUBLIC since 18.5.0. It was private, so the wrapper kept its own byte-identical
    copy of both this and the table - and the core's own comment admitted the table
    was "copied verbatim from the wrapper". A private name is not a reason to
    duplicate ten entries whose Phoenix row exists because getting it wrong made an
    identification service deduce the wrong origin timezone.
    """
    return _IANA_TO_POSIX_TZ.get(timezone, timezone)


#: The private name kept as an alias, not a second body. It was byte-identical
#: to `tz_env` above - two definitions of a ten-entry table lookup whose Phoenix
#: row exists because getting it wrong made an identification service deduce the
#: wrong origin timezone. Nothing outside this module should reach for it.
_tz_env = tz_env


def build_launch_env(
    prefs: Dict[str, Any],
    *,
    timezone: Optional[str] = None,
    egress_ip: Optional[str] = None,
    manifest_path: "Optional[str | os.PathLike[str]]" = None,
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Subprocess env for the patched binary. Mirrors the wrapper's _build_env.

    TZ tunes the libc clock. STEALTHFOX_WEBRTC_PUBLIC_IP feeds nICEr the proxy
    egress IP; an already-set value in base_env wins.

    STEALTHFOX_FONT_MANIFEST points the engine at the font manifest THIS package
    declares, and it has to be an env var rather than the pref of the same name:
    the font list is built during app startup, inside
    InitSharedFontListForPlatform, and on the Juggler path the caller's prefs
    never reach the profile at all - they travel over the wire and are applied
    by JS once the browser is already running. Measured 2026-08-08: removing a
    family from this package's copy changed nothing, because the pref read empty
    and the engine's own file won. An env var is set at process creation and
    inherited by every content process.

    It carries a PATH. The manifest is ~20 KB and an environment block is not
    the place for it. `manifest_path=None` writes no variable, which leaves the
    engine on its packaged copy - the floor a browser launched without this
    package still has to stand on.
    """
    env = dict(os.environ if base_env is None else base_env)
    if timezone:
        env["TZ"] = _tz_env(timezone)
    if manifest_path is not None:
        # An already-set value in base_env wins, same rule as the WebRTC IP:
        # an A/B harness has to be able to point this somewhere else.
        env.setdefault("STEALTHFOX_FONT_MANIFEST", str(manifest_path))
    webrtc_ip = env.get("STEALTHFOX_WEBRTC_PUBLIC_IP") or egress_ip
    if webrtc_ip:
        env["STEALTHFOX_WEBRTC_PUBLIC_IP"] = webrtc_ip
        env["STEALTHFOX_WEBRTC_DISABLE_IPV6"] = "1"
    return env



class FontManifestMismatch(RuntimeError):
    """The manifest this package declares describes faces the engine lacks."""


def verify_font_manifest(manifest: str,
                         binary: "str | os.PathLike[str]") -> "Optional[List[str]]":
    """Face files `manifest` names that the engine lacks, or None if unchecked.

    THREE outcomes, not two, and collapsing the last two is the trap:
      * `[]`   - checked, every face is present. Safe.
      * `[...]`- checked, these are missing. The caller should refuse.
      * `None` - could NOT check: the engine has no `fonts/` directory beside
                 it, so it is not a bundle-only layout this can inspect.

    `None` is not a pass and must not be treated as `[]`. It is returned rather
    than an empty list for the same reason `download.py` prints "an absent
    check is not a passed check" when an asset carries no omni.ja digest. What
    the caller may reasonably do with it is proceed anyway: an engine with no
    bundled fonts of its own cannot be made worse by a manifest it will read
    against files it does not have either way, and refusing would break every
    layout that is not ours - including every test fixture, which is how this
    distinction got written.

    This is the check that makes handing our manifest to the engine safe, and
    it deliberately is NOT "the two manifests are equal". They are supposed to
    diverge: the whole point of carrying one here is that this package can move
    ahead of a published engine. What must hold is narrower and is the thing
    that fails silently - every `f|` record gives vertical metrics for a named
    FILE, and metrics for a file the engine does not have are not an error, they
    are text laid out a few pixels wrong on a page nobody is measuring.

    Empty result means safe. The caller decides what a non-empty one costs: at
    launch it is worth refusing over, because the alternative is shipping a
    wrong layout to every session, whereas a browser that will not start is at
    least loud.

    An engine with no fonts directory at all returns every file as missing
    rather than an empty list - an absent check must never read like a passed
    one.
    """
    fonts_dir = Path(binary).parent / "fonts"
    referenced = []
    for line in manifest.splitlines():
        if line.startswith("f|"):
            parts = line.split("|")
            if len(parts) > 1 and parts[1]:
                referenced.append(parts[1])
    if not referenced:
        return []
    if not fonts_dir.is_dir():
        return None
    have = {p.name for p in fonts_dir.iterdir()}
    return sorted({f for f in referenced if f not in have})


def cached_font_manifest_path(manifest: str) -> "Path | None":
    """Materialise `manifest` somewhere the engine can read it, once per content.

    The Playwright path does not own the profile directory - Playwright creates
    it - so the file cannot simply live beside the profile. Addressing it by the
    sha256 of its own bytes gives a stable path per manifest: launching a
    thousand sessions with the same core writes one file, and a session with a
    pinned manifest gets its own without disturbing the others. Nothing has to
    be cleaned up on teardown, which is the failure mode a per-session temp dir
    would introduce.

    Returns None for an empty manifest: the engine reads that as "use my own
    copy", and writing an empty file would say the same thing while looking
    like a configured one.
    """
    if not manifest:
        return None
    raw = manifest.encode("ascii")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    d = cache_root() / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"bundle-fonts-{digest}.list"
    if not out.is_file() or out.stat().st_size != len(raw):
        assert bytes([13, 10]) not in raw, "il manifest ha terminazioni CRLF"
        # Write to a sibling and rename: two sessions starting together must
        # never let one read the half-written file of the other.
        tmp = out.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, out)
    return out


def write_font_manifest(profile_dir: "str | os.PathLike[str]",
                        manifest: str) -> "Path | None":
    """Write `manifest` beside the profile and return its path, or None.

    Returns None for an empty manifest rather than writing an empty file: the
    engine treats an unreadable or empty source as "use my own copy", and an
    empty file would be an indistinguishable way of saying the same thing while
    looking like a configured one.

    Written as BYTES with an explicit ascii encode. `write_text` on Windows
    translates every newline to CRLF, and this project has already shipped a
    30k-star repository a commit that was 48 additions and 48 deletions for a
    one-line change because of exactly that. The engine's parser splits on a
    bare newline.
    """
    if not manifest:
        return None
    d = Path(profile_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = d / "bundle-fonts.list"
    raw = manifest.encode("ascii")
    # bytes([13, 10]), not a literal: a CR escape written through a shell
    # heredoc is how this very line was corrupted on the first attempt, which is
    # the same failure the assertion is guarding against.
    assert bytes([13, 10]) not in raw, "il manifest ha terminazioni CRLF"
    out.write_bytes(raw)
    return out


# Imported here rather than at module scope: download.py imports seal.py,
# and pulling that chain in at import time would make launch.py the heavy
# module in a package whose consumers import it first.
from .download import cache_root

from .process import SessionToken


@dataclass
class LaunchPlan:
    """Everything needed to spawn the patched Firefox for one identity."""
    binary: str
    profile_dir: Path
    argv: List[str]
    env: Dict[str, str]
    #: Identity for the process tree this plan will start. Already stamped into
    #: ``env``, so every process in the tree inherits it; the caller keeps it to
    #: bind a lifetime guard and to reap by positive identification rather than
    #: by guessing which firefox is theirs. Defaulted so that anything
    #: constructing a LaunchPlan positionally keeps working.
    session_token: SessionToken = field(default_factory=SessionToken)


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
    # ⛔ IL DEFAULT ERA "about:blank", TOLTO IL 2026-08-20 col revert del newtab.
    # Un URL sulla riga di comando ha la PRECEDENZA sulla pagina d'avvio, quindi
    # finche' c'era il lancio diretto non apriva about:home nemmeno con i cinque
    # file del sorgente riportati a upstream e le prefs newtab tolte: era la
    # seconda soppressione, indipendente da `browser.startup.page`.
    #
    # E ne chiude anche un'altra, misurata il 2026-08-20: un URL iniziale spegne
    # le chiamate ad `accounts.firefox.com` e `addons.mozilla.org` che il retail
    # fa all'avvio (2/2 senza URL, 0/2 con). Su Playwright quell'argomento lo
    # impone la libreria e non e' nostro; QUI era nostro, ed era una scelta.
    #
    # Il parametro resta pubblico: chi vuole una pagina la passa esplicitamente.
    url: str = "",
) -> LaunchPlan:
    """The single direct-launch entry point (no Playwright, no Qt).

    Give it a ``seed`` and a ``profile_dir`` (plus optionally a concrete
    ``proxy`` dict, ``timezone``/``locale`` - both default to ``"auto"`` and are
    resolved from the proxy egress - a ``pin`` and a ``binary_ver``); it resolves
    geo, generates the fingerprint profile, translates it to prefs, writes
    ``user.js`` into ``profile_dir``, and returns the binary path + argv + env.
    Any direct-launch consumer uses this so the session-setup logic lives in
    one place (mirrors the wrapper's __aenter__) - the profile manager did,
    until its 2026-08-18 deletion.
    ``proxy`` must already be a concrete endpoint dict (SOCKS/HTTP), not an intent.
    """
    # Lazy imports keep launch.py free of import-order coupling with the rest of
    # the package (this module is imported near the end of invisible_core/__init__).
    from .download import ensure_binary
    from ._fpforge import generate_profile
    from ._geo import prepare_session_geo, resolve_session_locale
    from .prefs import compose_session_prefs

    binary = str(ensure_binary(binary_ver) if binary_ver else ensure_binary())
    # Resolves timezone="auto" from the egress AND discovers the egress IP;
    # raises behind a dead proxy (fail-early, by design).
    geo = prepare_session_geo(timezone, proxy)
    loc = locale
    if (locale or "").strip().lower() == "auto":
        loc = resolve_session_locale(geo.egress_ip, proxy)
    fp = generate_profile(seed=seed, pin=pin)
    # One composition for all three entry points (prefs.py). This path takes the
    # proxy layer and the hard-kill layer; it does NOT write the humanize prefs,
    # which is what humanize=None means - see compose_session_prefs.
    # ⛔ delegates_auth=False, e non e' un dettaglio di stile: questo percorso
    # lancia il binario con subprocess, quindi non ha nessun Playwright a cui
    # passare un endpoint HTTP. Finche' non lo diceva, `configure_proxy`
    # restituiva il dict e la riga qui sotto teneva solo `.prefs` buttandolo via:
    # il browser partiva SENZA proxy mentre `_geo` aveva gia' risolto fuso e
    # lingua ATTRAVERSO il proxy, quindi la sessione dichiarava un paese e ne
    # navigava un altro, in silenzio. Adesso un endpoint HTTP senza credenziali
    # viene instradato dalle prefs, e uno CON credenziali viene rifiutato.
    prefs = compose_session_prefs(
        fp, locale=loc, timezone=geo.timezone,
        proxy=proxy, survive_hard_kill=True, delegates_auth=False,
    ).prefs
    pdir = Path(profile_dir)
    write_user_js(pdir, prefs)
    env = build_launch_env(prefs, timezone=geo.timezone or None, egress_ip=geo.egress_ip)
    argv = [binary, "-no-remote", "-profile", str(pdir)]
    argv += list(extra_args or [])
    if url:
        argv.append(url)
    # Mint the identity here rather than in each consumer: the manager had none
    # at all and leaked its whole tree on every kill, and a token that is not
    # part of the plan is a token somebody has to remember to add to the env.
    token = SessionToken.mint()
    return LaunchPlan(binary=binary, profile_dir=pdir, argv=argv,
                      env=token.stamp(env), session_token=token)
