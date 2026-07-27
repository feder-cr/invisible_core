"""Back-compat alias. The module is `invisible_core.pin` since 2026-07-27.

WHY IT WAS RENAMED. The leading underscore told every reader of this package
that the module was internal and free to change. It was not: measured that day
by parsing both consumers' sources, `invisible-playwright` and
`invisible-firefox` import **nineteen names** from it, at module level, on the
first line of their own `__init__`. They pin `invisible-core==` to an exact
version, so a rename in here does not fail their build - it fails their IMPORT,
on the machine of whoever upgrades next, with a traceback naming a symbol
instead of a version. That exact shape had already reached a user once.

The names are the contract either way. The rename makes the file say so, and
`tests/test_consumer_contract.py` is what enforces it.

WHY THIS FILE STILL EXISTS. Consumers published BEFORE the rename import
`invisible_core._pin` by name, and their pin normally resolves the core they
were built against - but an editable checkout, a `--no-deps` install or a
lockfile can put a new core under an old consumer, and that combination is
exactly the one the pin machinery exists to explain rather than crash on.
Two lines is a cheap way not to add another entry to the bug archive.

(No version number appears above, deliberately. A sibling guard,
`test_the_module_holds_no_version_literal`, forbids one anywhere in this
module INCLUDING the prose - a second copy of the version is the original bug -
and it caught the first draft of this docstring, which is the behaviour it is
for.)
"""
import sys as _sys

from . import pin as _mod

# A TRUE module alias, not `from .pin import *`. The star form copies bindings
# instead of sharing them, so `invisible_core._pin.INSTALL_RUNNER = fake` - a
# documented seam, used by the doctor tests and by anything that wants to
# observe the repair without running pip - would patch this module while the
# code kept reading `pin`'s copy. Measured: twelve tests went red on the star
# form, all of them monkeypatching through this name, and every one of them
# would have been a silent no-op in production code doing the same thing.
#
# Same construct as the wrapper's back-compat shims, and for the same reason.
_sys.modules[__name__] = _mod
