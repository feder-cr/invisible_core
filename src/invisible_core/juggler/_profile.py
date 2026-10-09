"""Writing the profile a Firefox launch is given, and reading back what it is.

⛔ Leaves, like the ones in `_marshal`: none of them mentions a dispatcher.
What they have in common is a different subject - a directory on disk and the
`user.js` inside it - which is why they are not in the same module. One
`_helpers.py` holding both would be the bucket every project grows and nobody
can name.

PUBLIC NAMES since 38.34.0, re-exported by `invisible_core.juggler`: every
wrapper imports them, so a leading underscore would tell a reader of this
package they are free to change what three published packages load. The
`user.js` writer that lived here is `invisible_core.launch.write_user_js`, the
one writer the direct launch already used.
"""
from __future__ import annotations

import pathlib
import shutil
from typing import Dict


def remove_profile(directory: str) -> None:
    """Take away a profile WE created. Never one the caller named.

    ⛔ IT MUST NOT RAISE. This runs while the session is already going away,
    and on Windows a file can still be held for a moment after the process that
    owned it exits. A profile left behind is a few dozen megabytes; an
    exception here would be a shutdown that fails for a reason nobody cares
    about, on a path the caller has already stopped watching.
    """
    shutil.rmtree(directory, ignore_errors=True)


def read_version(executable: str) -> str:
    """The base version, from `application.ini` next to the binary.

    ⛔ Read, never assumed: this project has three separate incidents where a
    folder name or a guess about the version sent a whole evening of
    measurements against the wrong build.
    """
    ini = pathlib.Path(executable).parent / "application.ini"
    try:
        for line in ini.read_text(encoding="utf-8",
                                  errors="replace").splitlines():
            if line.startswith("Version="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "0.0"


def only_set(params: Dict) -> Dict:
    """The parameters that were actually given, with the absent ones REMOVED.

    ⛔ IN A CLOSED-WORLD SCHEMA, `null` AND ABSENT ARE DIFFERENT ANSWERS.
    Juggler validates every field it is handed: an Optional that arrives as
    `null` is not "not provided", it is a value of the wrong type, and the
    command is REJECTED at runtime with `Object "<root>.clip" is undefined,
    but has some scheme`. Measured on 2026-08-28 on `Page.screenshot`, whose
    clip and quality are both optional and were both being sent as null.

    ⛔ AND THIS IS NOT DONE INSIDE `Connection.send`, tempting as that is:
    some commands mean something BY sending null. `Browser.setGeolocationOverride`
    with `geolocation: null` CLEARS the override, and stripping it there would
    turn "stop pretending to be somewhere" into "do nothing".
    """
    return {k: v for k, v in params.items() if v is not None}


def host_of(url: str) -> str:
    """The host of a url, without importing a parser for three characters."""
    without_scheme = url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0].split(":", 1)[0].lower()


def domain_matches(cookie_domain: str, host: str) -> bool:
    """⛔ A LEADING DOT MEANS "AND EVERY SUBDOMAIN", and dropping it turns a
    site-wide cookie into one that matches nothing. Comparing the two strings
    directly is the version that looks right and returns an empty list."""
    domain = (cookie_domain or "").lstrip(".").lower()
    if not domain or not host:
        return False
    return host == domain or host.endswith("." + domain)
