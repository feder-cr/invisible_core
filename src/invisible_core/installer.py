"""The command that installs into THIS environment, from the tool that built it.

Every remedy this package prints ends in a command, and the automatic pin repair
runs one. Until 49.45.0 every one of them was pip: `pip install ...` in the
messages, `python -m pip install --force-reinstall` in the repair. That was true
of the environments the README used to make. It is not true of the one it makes
now, because `uv init` + `uv add` builds a venv with NO pip in it.

MEASURED 2026-10-17, in a project made exactly that way (uv 0.12.10,
invisible-playwright 0.37.0): `importlib.util.find_spec("pip")` is None, and the
INSTALLER record of both distributions says `uv`. With a mismatched core
(48.43.0 installed under a 49.44.0 pin) the import tried
`python -m pip install --force-reinstall "invisible-core==49.44.0"`, which died
with `No module named pip`, and the ImportError then said
`Run: pip install "invisible-core==49.44.0"` - a command that in that venv does
not exist, or reaches some other Python's pip. The repair failed and the
instruction was wrong, for every user who had followed the README.

THE ENVIRONMENT ALREADY SAYS WHICH TOOL BUILT IT. Every installer writes its
name into the INSTALLER file of each distribution it installs (PEP 376): `pip`,
`uv`, and so on. Nothing read it. This module does, and every install command in
the package comes from here:

  * `uv`      -> `uv pip install --python <this interpreter> ...`
  * anything  -> `<this interpreter> -m pip install ...`
    else

The second line is what pip-made environments were always told, and they still
are: an environment somebody built with pip has pip in it, and that person gets
the command that works for them. A distribution with no record at all - a source
tree on `sys.path` - gets it too, which is what it got before. Both commands
name THIS interpreter, so they land where the running code lives and not in
whatever `pip` or `python` a shell happens to find first.

The flags are pip's spellings for both (`--force-reinstall`, `--upgrade`, `-e`,
`--no-deps`), because uv accepts them: measured with `--force-reinstall` against
uv 0.12.10. So a caller says WHAT to install, and only this module says HOW.

RUNNING uv NEEDS ITS EXECUTABLE, and a venv uv made does not contain one.
`uv run` puts the path of the uv that is running in `$UV`; a shell with the venv
activated finds uv on PATH, where its installer put it. `runnable()` asks those
two, in that order, and answers None when neither has it. The repair then
refuses and hands over the command, the same outcome as any other repair that
cannot run - never a pip command in an environment pip did not build.
"""
from __future__ import annotations

import os
import shutil
import sys
from importlib.metadata import PackageNotFoundError, distribution
from typing import List, Optional, Sequence

from . import environment

#: The INSTALLER record uv writes, and the executable the command starts with.
UV = "uv"

def installer_of(dist: str) -> str:
    """`uv` when uv installed `dist` into this environment, `pip` otherwise.

    "Otherwise" includes no record at all (a source tree on the path, a
    distribution that is not installed): that is the case pip was always
    assumed for, and the command it gets is the one it always got.
    """
    # `read_text` already answers None for a missing or unreadable file; the
    # one thing it raises is a distribution that is not installed at all.
    try:
        record = distribution(dist).read_text("INSTALLER")
    except PackageNotFoundError:
        record = None
    return UV if (record or "").strip().lower() == UV else "pip"


def install(dist: str, *args: str) -> List[str]:
    """The install command for this interpreter, as the tool that installed
    `dist` spells it. `args` are what to install and how, in pip's flags."""
    if installer_of(dist) == UV:
        return [UV, "pip", "install", "--python", sys.executable, *args]
    return [sys.executable, "-m", "pip", "install", *args]


def runnable(cmd: Sequence[str]) -> Optional[List[str]]:
    """`cmd` ready for subprocess: `uv` replaced by the executable that will run
    it, from `$UV` and then PATH. None when the command needs uv and neither has
    one. A command that does not start with uv comes back unchanged."""
    cmd = list(cmd)
    if not cmd or cmd[0] != UV:
        return cmd
    found = os.environ.get(environment.UV) or shutil.which(UV)
    return [found, *cmd[1:]] if found else None
