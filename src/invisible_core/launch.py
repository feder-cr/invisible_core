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
