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


def resolve_session_seed(seed: Optional[int] = None,
                         profile_dir: "Union[str, os.PathLike[str], None]" = None) -> int:
    """The seed a session runs under.

    No profile directory: ``seed`` if given, a fresh one otherwise. With one:
    the seed the profile recorded, or - on its first launch - ``seed`` if given
    or a fresh one, recorded in it. An explicit ``seed`` that differs from the
    recorded one is REFUSED, not obeyed: obeying it would show the profile's
    cookies on another machine, which is the very thing the record exists to
    prevent, and overwriting it silently would make it a no-op.
    """
    if profile_dir is None:
        return int(seed) if seed is not None else new_seed()
    record = Path(profile_dir) / SEED_FILE
    if record.is_file():
        recorded = int(json.loads(record.read_bytes())["seed"])
        if seed is not None and int(seed) != recorded:
            raise ValueError(
                f"profile {str(profile_dir)!r} belongs to seed {recorded}; "
                f"seed={int(seed)} would show its cookies on another machine. "
                f"Pass seed={recorded} or no seed, or use a new profile directory.")
        return recorded
    resolved = int(seed) if seed is not None else new_seed()
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_bytes(json.dumps({"seed": resolved}).encode("utf-8"))
    return resolved
