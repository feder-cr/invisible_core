"""Package version. Derived from the seal so it CANNOT stay behind a binary
release: bumping the tag bumps the version, which is the exact condition pip's
resolver tests before it decides to skip a git dependency.

CORE_REVISION is the ONLY hand-edited number in this package. Bump it by one
for a core-only release (a pure-Python fix against the same engine). Never
reset it: pip compares with != , not <.
"""
import json
import pathlib

CORE_REVISION = 8

_seal = json.loads(
    pathlib.Path(__file__).with_name("seal.json").read_text(encoding="utf-8")
)
__version__ = f"{int(_seal['tag'].split('-')[1])}.{CORE_REVISION}.0"
