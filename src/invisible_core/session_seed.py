"""The seed a session runs under, and the profile that remembers it.

The seed IS the machine a session claims to be: cores, screen, GPU and every
other sampled value derive from it. A persistent profile keeps cookies and
storage across launches, so a profile that came back under a new seed showed a
site the same cookies on another machine (70-known-bugs.md [B314], measured
2026-10-15: 8 cores, 1536x864, Intel, then 16 cores, 1920x1080, NVIDIA, on one
profile directory).

The cause was not one client: the seed was drawn in five places - this
package's `get_default_stealth_prefs` and the four clients' constructors - and
none of them looked at the profile. This is the one place now, and a profile
directory remembers the seed it was created with.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Optional, Union

#: The file, inside a profile directory, that records its seed. Outside
#: `chrome/`, so no content process can read it, and read by nothing in the
#: browser.
SEED_FILE = "invisible-seed.json"


def new_seed() -> int:
    """A fresh seed. 31 bits: `zoom.stealth.fpp.hw_seed` is an int32."""
    return secrets.randbits(31)


def _recorded(profile_dir) -> Optional[int]:
    record = Path(profile_dir) / SEED_FILE
    if not record.is_file():
        return None
    return int(json.loads(record.read_bytes())["seed"])


def _refuse_another(profile_dir, recorded: int, seed: int) -> None:
    raise ValueError(
        f"profile {str(profile_dir)!r} belongs to seed {recorded}; "
        f"seed={seed} would show its cookies on another machine. "
        f"Pass seed={recorded} or no seed, or use a new profile directory.")


def resolve_session_seed(seed: Optional[int] = None,
                         profile_dir: "Union[str, os.PathLike[str], None]" = None) -> int:
    """The seed a session runs under. Reads, never writes.

    A client calls it where it builds the profile, which happens before the
    profile directory exists and must not create it (a typo in a path would
    otherwise leave directories behind). The seed the profile recorded wins;
    without a record, ``seed`` if given or a fresh one. An explicit ``seed``
    that differs from the record is REFUSED, not obeyed: obeying it would show
    the profile's cookies on another machine, which is what the record exists
    to prevent. ``record_session_seed`` writes the record at launch.
    """
    recorded = _recorded(profile_dir) if profile_dir is not None else None
    if recorded is None:
        return int(seed) if seed is not None else new_seed()
    if seed is not None and int(seed) != recorded:
        _refuse_another(profile_dir, recorded, int(seed))
    return recorded


def record_session_seed(profile_dir: "Union[str, os.PathLike[str]]", seed: int) -> None:
    """Record the seed in a profile directory, at the launch that uses it.

    Called where a client creates the profile directory. A profile that already
    has a record keeps it, and a different seed is refused here too: between
    `resolve_session_seed` and the launch another session may have recorded one.
    """
    recorded = _recorded(profile_dir)
    if recorded is not None:
        if recorded != int(seed):
            _refuse_another(profile_dir, recorded, int(seed))
        return
    record = Path(profile_dir) / SEED_FILE
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_bytes(json.dumps({"seed": int(seed)}).encode("utf-8"))
