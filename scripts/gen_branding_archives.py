#!/usr/bin/env python3
"""Write invisible_core/branding/<set>/branding.zip from a Mozilla tree, and
branding/SOURCE.json saying which tree.

Why a generator
---------------
The branding a page reads is the core's (BRANDING_FILES, served by the engine
from the declared archive), and it has to be the retail's of the Firefox the
engine is built from, byte for byte: a page loads aboutDialog.css and reads its
rules. Every file of the archive is a file of Mozilla's tree, picked by the
branding's own jar.mn (browser/branding/<set>/content/jar.mn), so the archive is
derived, never edited: the Firefox 157 port found the 151 archive still in the
core, and the engine's page-surface gate refused aboutDialog.css, which 157
changed.

    python scripts/gen_branding_archives.py --mozilla <checkout> --tag FIREFOX_157_0_1_RELEASE

<checkout> is any git checkout of mozilla-firefox/firefox that has the tag
(the engine's tree does). The archives are deterministic: the same tag writes
the same bytes. tests/test_branding_source.py checks that every shipped archive
holds exactly what SOURCE.json records.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from invisible_core._fpforge.profile import BRANDING_FILES, BRANDING_SETS  # noqa: E402

BRANDING_DIR = ROOT / "src" / "invisible_core" / "branding"
SOURCE_FILE = BRANDING_DIR / "SOURCE.json"
# `  content/branding/<name>   (<source relative to content/>)`; without the
# parenthesis the source is the name itself, in content/.
JAR_LINE = re.compile(r"^\s*content/branding/(?P<name>\S+)(?:\s+\((?P<src>[^)]+)\))?\s*$")


def jar_entries(jar_mn: str) -> dict[str, str]:
    """name in the package -> path of its source relative to content/."""
    entries = {}
    for line in jar_mn.splitlines():
        m = JAR_LINE.match(line)
        if m:
            entries[m["name"]] = m["src"] or m["name"]
    return entries


def git_show(repo: Path, tag: str, path: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), "show", f"{tag}:{path}"],
                          check=True, capture_output=True).stdout


def set_files(repo: Path, tag: str, branding_set: str) -> dict[str, bytes]:
    """The files of one branding set at a tag, exactly BRANDING_FILES."""
    content = f"browser/branding/{branding_set}/content"
    entries = jar_entries(git_show(repo, tag, f"{content}/jar.mn").decode())
    if sorted(entries) != sorted(BRANDING_FILES):
        raise SystemExit(
            f"{branding_set} at {tag}: jar.mn lists {sorted(entries)}, BRANDING_FILES "
            f"is {sorted(BRANDING_FILES)}. The package changed: update BRANDING_FILES "
            f"(and the engine, which refuses an archive with other files) first.")
    out = {}
    for name, src in entries.items():
        path = str(Path(content, src).as_posix())
        # `(../document.ico)` is relative to content/; normalise it like jar.mn does
        parts = []
        for p in path.split("/"):
            parts.pop() if p == ".." else parts.append(p)
        out[name] = git_show(repo, tag, "/".join(parts))
    return out


def archive_bytes(files: dict[str, bytes]) -> bytes:
    """A zip of `files` at its root, the same bytes for the same files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 0
            z.writestr(info, files[name], compresslevel=9)
    return buf.getvalue()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mozilla", required=True, type=Path, help="git checkout of Mozilla's tree")
    ap.add_argument("--tag", required=True, help="e.g. FIREFOX_157_0_1_RELEASE")
    a = ap.parse_args(argv)
    source = {"mozilla_tag": a.tag, "sets": {}}
    for branding_set in BRANDING_SETS:
        files = set_files(a.mozilla, a.tag, branding_set)
        (BRANDING_DIR / branding_set / "branding.zip").write_bytes(archive_bytes(files))
        source["sets"][branding_set] = {
            name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
        print(f"{branding_set}: {len(files)} files from {a.tag}")
    SOURCE_FILE.write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {SOURCE_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
