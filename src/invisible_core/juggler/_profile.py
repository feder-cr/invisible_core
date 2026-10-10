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

import os
import pathlib
import shutil
from typing import Dict, Mapping, Optional

from .._owned_dirs import owned_dir, sweep_owned_dirs
from ..process import TOKEN_VAR, SessionToken, find_processes, terminate

#: The session directories, named `<prefix><pid>_<random>` (`_owned_dirs`).
PROFILE_PREFIX = "invisible_profile_"
TMP_PREFIX = "invisible_tmp_"


class SessionFiles:
    """The directories one browser session writes, and the one place they end.

    The profile, unless the caller named one, and a temporary directory for
    the browser alone: ``env`` is the environment to launch with, the given
    one with ``TMP``, ``TEMP`` and ``TMPDIR`` pointed at it, so the browser's
    temporary files belong to the session instead of the system. ``remove()``,
    called once the browser has exited, ends whatever still carries the
    session's token and only then takes the directories away.

    ⛔ THREE COPIES OF THIS LIVED IN THE CLIENTS, and all three left the
    profile behind. Measured on Windows (B223): a session longer than a minute
    left its `invisible_profile_*` holding an empty `saved-telemetry-pings`,
    6,091 of them in one %TEMP%. At exit the browser starts `pingsender.exe`,
    a child that outlives it holding the ping file inside the profile: the
    removal ran after the browser, failed on that file, and the child then
    deleted it. Every process of the session carries the token the launcher
    stamps into the environment, children included, so they are ended first;
    the launchers' own last step ended them anyway, a moment later, so nothing
    changes on the network.

    ⛔ AND THE TEMPORARY DIRECTORY IS NOT A NICETY (B267). Firefox writes into
    the system one and cleans up when a job finishes, and a session is short:
    measured, a 70 s session left two 4 MB copies of the Remote Settings
    certificate bundle in %TEMP%, and one machine had 439. The directory is
    made inside this process's own, so a caller's ``TMP`` still decides where
    it lives. No page can read where the temporary directory is.

    The names carry this process's pid, and a new session first sweeps the
    directories of processes that are gone (``_owned_dirs``): what an owner
    killed before ``remove()`` left behind (B268).
    """

    def __init__(self, profile_dir: Optional[str] = None,
                 env: Optional[Mapping[str, str]] = None) -> None:
        base = dict(os.environ if env is None else env)
        self.token = SessionToken(base.get(TOKEN_VAR, ""))
        self.owns_profile = profile_dir is None
        sweep_owned_dirs((PROFILE_PREFIX, TMP_PREFIX))
        self.tmp = owned_dir(TMP_PREFIX)
        try:
            self.profile = profile_dir or owned_dir(PROFILE_PREFIX)
        except BaseException:
            remove_profile(self.tmp)
            raise
        self.env = dict(base, TMP=self.tmp, TEMP=self.tmp, TMPDIR=self.tmp)

    def remove(self) -> None:
        """End what still carries the session's token, then remove the
        directories this session made - never a profile the caller named.
        Never raises, and a second call finds nothing to do."""
        try:
            terminate(find_processes(self.token))
        except Exception:
            pass
        if self.owns_profile:
            remove_profile(self.profile)
        remove_profile(self.tmp)


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
