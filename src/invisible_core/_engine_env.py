"""The environment of the engine's process: what the host's does not carry, and what the core writes.

ONE composition, at the lowest place the environment of the process is born (`juggler.connection.launch`),
and again, idempotently, in `launch.build_launch_env` for the callers that start the binary themselves. A
caller that hands an environment to a launch (the served path, a persistent context, an A/B harness) hands
it as raw material: whatever it carries of the host's, `MOZ_*`, the GL and toolkit families, the zone and
the language, does not reach the engine, and what the core declares is written over it.

The lists are `environment`'s (`HOST_FAMILIES`, `HOST_NAMES`, `FAMILIES_PASSED`).
"""
from __future__ import annotations

from typing import Dict, Mapping, Optional

from . import environment

#: The display backend of EVERY session, hidden or headed (A9 of
#: 28-valori-dell-host.md). Until 49.46.0 only the hidden session's Xvfb
#: carried these two, so a headed Linux session inherited the host's: on a
#: Wayland desktop (WSLg, GNOME) the browser ran on Wayland, with a window
#: geometry, a scale and a clipboard of a different backend from the one every
#: other session declares. One declaration for both modes, here, where the
#: environment is composed; a headed X11 session draws through the desktop's
#: XWayland, as `DISPLAY` says. Written on every platform, not in a
#: `sys.platform` branch: the Windows build has no GTK and reads neither, and
#: one environment for both builds is the thing that cannot drift.
DISPLAY_BACKEND = {"MOZ_ENABLE_WAYLAND": "0", "GDK_BACKEND": "x11"}

#: The browser never opens its crash reporter. The engine refuses a launch whose declarations
#: are incomplete (StealthRefuse), and that refusal ends in an abort on purpose: with the reporter
#: on, Windows starts crashreporter.exe, which shows a "Crash Reporter" window on the desktop of
#: whoever runs the browser and waits there for a click (313 windows in one e2e run of the wrapper).
#: A crash of a headless session has no one to read the dialog and nothing to send, and the
#: reason of a refusal is in the browser's output, which the launcher already reads. Written
#: here, in the one place the environment is composed, so every launch carries it, and over
#: whatever the caller's environment says: a session that opens a dialog is not a session.
NO_CRASH_REPORTER = {"MOZ_CRASHREPORTER_DISABLE": "1"}


def without_the_hosts(env: Dict[str, str]) -> Dict[str, str]:
    """`env` without any variable of `environment.HOST_FAMILIES` outside
    `environment.FAMILIES_PASSED`.

    "No value a page reads comes from the host": the prefs are locked, the
    environment of the engine was not, and `MOZ_DISABLE_SOCKET_PROCESS=1` in the
    shell that starts the library moved ICE out of the socket process. The same
    holds for what drives the GL and the toolkit on Linux (`MESA_*`, `LIBGL_*`,
    `GDK_SCALE`, `GTK_THEME`...). The rule is the same for `os.environ` and for
    the `base_env` of a caller (an A/B harness that wants a diagnostic names it
    in `environment.MOZ_DIAGNOSTICS`, or the variable is not one a session may
    carry). Names compare in upper case: on Windows the environment is
    case-insensitive, and `moz_log` is `MOZ_LOG`.
    """
    passed = frozenset(environment.FAMILIES_PASSED)
    families = tuple(environment.HOST_FAMILIES)
    names = frozenset(environment.HOST_NAMES)
    return {k: v for k, v in env.items()
            if k.upper() not in names
            and (not k.upper().startswith(families) or k.upper() in passed)}


def declared_locale(tag: Optional[str]) -> Dict[str, str]:
    """The language of the machine, as the session declares it, in the three variables a process reads it from.

    Fontconfig's default language, the language the engine gives to text with no `lang`, and the C library's
    locale come from `LANG`/`LC_ALL`/`LANGUAGE`; the host's are gone (`_without_the_hosts_families`), so what
    they say is the session's `intl.locale.requested` (`tag`) (`it-IT` -> `it_IT.UTF-8`, `it`). Nothing declared, nothing
    written. A process has ONE value of each, a context may have its own language: the engine answers a context
    from the context's declaration, this is the floor under it."""
    tag = str(tag or "").strip()
    if not tag:
        return {}
    parts = tag.replace("_", "-").split("-")
    language = parts[0].lower()
    if not language.isalpha() or not 2 <= len(language) <= 3:
        return {}
    region = next((p.upper() for p in parts[1:] if (len(p) == 2 and p.isalpha()) or (len(p) == 3 and p.isdigit())), "")
    posix = "%s%s.UTF-8" % (language, "_" + region if region else "")
    return {"LANG": posix, "LC_ALL": posix, "LANGUAGE": language}


def declared(locale_tag: Optional[str] = None) -> Dict[str, str]:
    """What the core writes into the engine's environment, always: the display backend, no crash reporter,
    and the language of the session (`declared_locale`), over whatever the host or the caller carried."""
    out: Dict[str, str] = {}
    out.update(DISPLAY_BACKEND)
    out.update(NO_CRASH_REPORTER)
    out.update(declared_locale(locale_tag))
    return out


def compose(env: Mapping[str, str], locale_tag: Optional[str] = None) -> Dict[str, str]:
    """The environment of the engine's process out of the raw material `env`: the host's families out,
    the declared values in."""
    result = without_the_hosts(dict(env))
    result.update(declared(locale_tag))
    return result
