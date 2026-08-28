"""Public helpers for building Firefox launch config without using ``InvisiblePlaywright``.

Use these when you need to call ``playwright.firefox.launch()`` (or
``firefox.launch_persistent_context()``) directly with our patched binary
and stealth prefs, instead of using the ``InvisiblePlaywright`` context
manager.

Typical caller is an external integration that owns its own browser
lifecycle (a Crawlee/Skyvern/changedetection-style fetcher, a Playwright
Server wrapper, a multi-language harness) and just wants the building
blocks::

    from playwright.async_api import async_playwright
    from invisible_playwright import ensure_binary, get_default_stealth_prefs

    async with async_playwright() as p:
        browser = await p.firefox.launch(
            executable_path=str(ensure_binary()),
            firefox_user_prefs=get_default_stealth_prefs(seed=42),
        )

For everyday Python usage the ``InvisiblePlaywright`` context manager is
still the recommended entry point; these helpers expose the same internals
without the lifecycle ownership.

.. note::
   When calling ``firefox.launch()`` yourself, pass ``headless=False`` and
   manage the display hiding (Xvfb on Linux, hidden desktop on Windows)
   externally. Passing ``headless=True`` directly to Playwright puts
   Firefox in true headless mode, which skips the real rendering pipeline
   and breaks canvas / audio / WebGL fingerprint coherence. The
   ``InvisiblePlaywright`` context manager does this translation
   automatically; the public helpers leave it to the caller.
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional, Union

from ._fpforge import generate_profile
from ._webgl_personas import forced_gpu_class
from .prefs import compose_session_prefs


def get_default_stealth_prefs(
    seed: Optional[int] = None,
    *,
    pin: Optional[Dict[str, Any]] = None,
    locale: str = "en-US",
    timezone: str = "",
    extra_prefs: Optional[Dict[str, Any]] = None,
    humanize: Union[bool, float] = True,
    virtual_display: bool = False,
    proxy: Optional[Dict[str, str]] = None,
    show_cursor: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build a complete ``firefox_user_prefs`` dict for ``firefox.launch()``.

    Same prefs that ``InvisiblePlaywright(seed=..., locale=..., timezone=...,
    extra_prefs=..., humanize=...)`` would inject. Use this when you need to
    drive ``playwright.firefox.launch()`` yourself.

    Args:
        seed: Integer seed for the Bayesian fingerprint sampler. Same seed
            produces the same fingerprint. ``None`` generates a fresh
            random int31 (matches ``InvisiblePlaywright`` default).
        pin: Optional dict forcing specific fingerprint fields while the
            rest stays seed-derived. See ``docs/pinning.md``.
        locale: BCP-47 tag (e.g. ``"en-US"``). Drives ``Accept-Language``
            and ``navigator.language``.
        timezone: IANA timezone (e.g. ``"America/New_York"``). Empty means
            use the host TZ. This pure pref builder does NOT resolve
            ``"auto"`` (that needs the proxy + a network lookup at launch
            time) - pass a concrete zone here, or use ``InvisiblePlaywright``
            / ``resolve_session_timezone(timezone, proxy)`` for ``"auto"``.
        extra_prefs: Optional dict overlaid LAST onto the generated prefs.
        humanize: When True (default), every mouse move is expanded into
            a Bezier trajectory BY THE PATCHED BINARY. A float caps the
            motion in seconds. False disables the behavior.

            READ THIS BEFORE RELYING ON IT. The binary's generator builds
            every stroke from constants compiled into it: a fixed knot-box
            padding, exactly two control knots, one easing, one step-count
            law, one jitter probability. Constants are shared by every
            install, so the shape is the same for every movement of every
            session of every user - which makes it an identity rather than a
            detail. Measured over 1.94M points: the across-travel jitter mean
            is +0.4976 px with EVERY path biased, and the along-travel
            residual's distinct-value set is literally {0.0}, so four events
            solve the control polygon in closed form.

            For a single account that is imperfect realism. For a fleet it is
            a LINKAGE KEY: it does not say "an automation", it says "that
            automation, and these accounts are one operator".

            ``invisible-playwright`` no longer uses this path - since 0.4.0 it
            generates the motion itself, per session seed, and sets this pref
            to False. That option is not available to a caller driving the
            binary with their own Playwright, which is who this function is
            for. The default is kept at True because changing it would
            silently change behaviour for existing integrations; the choice
            between a shared-shape trajectory and no trajectory at all is
            yours, and it is a real one.
        virtual_display: When True on Windows, apply GPU-disabling prefs
            to prevent GPU process crashes on virtual desktops without
            D3D11 backend.
        proxy: Optional endpoint dict. A SOCKS endpoint writes the
            ``network.proxy.*`` auth prefs the patched binary reads; an
            HTTP/HTTPS one is Playwright's to apply, so pass it to
            ``launch(proxy=...)`` yourself. Added 2026-08-01: before it,
            this function returned a prefs dict with no proxy configuration
            whatever endpoint you were about to use.
        show_cursor: Draw the pointer where the automation is, in the
            browser's own chrome window: the Windows arrow with the package
            logo's green halo around it. None means "not specified" and
            resolves to `invisible_core.prefs.DEFAULT_SHOW_CURSOR`, which is
            True; pass False for a session with nothing drawn. It is a demo
            and debugging switch, not a stealth one - the page cannot reach
            the node, so no detector sees a difference - while a person
            watching the monitor sees a pointer moving with nobody touching
            the mouse, which is the trade the default makes.

            ⛔ It is DECLARED either way, false included, on both core paths.
            The engine does carry a compiled `false` as the last resort, but
            relying on it would be a second place that knows the answer, and
            the two can then disagree without anything noticing.

    Returns:
        Dict ready to pass as ``firefox_user_prefs=`` to
        ``playwright.firefox.launch()`` or ``launch_persistent_context()``.
    """
    resolved_seed = int(seed) if seed is not None else secrets.randbits(31)
    profile = generate_profile(resolved_seed, pin=pin, fixed_gpu_class=forced_gpu_class(resolved_seed))
    # One composition for all three entry points (prefs.py). Two things this
    # function did NOT do before 2026-08-01 and now does, both by taking the
    # shared layers rather than rebuilding them:
    #   * `proxy=` reaches configure_proxy, so a SOCKS endpoint produces the
    #     network.proxy.* auth prefs. Without it a caller driving Playwright
    #     themselves got a prefs dict with no proxy configuration at all;
    #   * humanize accepts any value the way the wrapper always has. `float()`
    #     bare raised ValueError on humanize="fast", out of a pref builder, and
    #     wrote maxTime = "-1.0" for humanize=-1.
    return compose_session_prefs(
        profile,
        locale=locale,
        timezone=timezone,
        extra_prefs=extra_prefs,
        virtual_display=virtual_display,
        proxy=proxy,
        humanize=humanize,
        show_cursor=show_cursor,
    ).prefs


def get_default_args() -> List[str]:
    """Return the default Firefox CLI args to pass via ``args=``.

    Currently empty list, since all our stealth configuration is delivered
    via ``firefox_user_prefs`` rather than CLI flags. Exposed for parity
    with the ``cloakbrowser.config.get_default_stealth_args`` pattern and
    to future-proof integrations that already wire ``args=[*existing,
    *get_default_args()]``.
    """
    return []
