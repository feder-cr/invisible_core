"""What the engine's `checkScheme` does to a command, for the fake engines of these tests.

The engine checks every command it receives as a CLOSED WORLD against its
`Protocol.js` (`juggler/protocol/PrimitiveTypes.js`): a field it does not
declare, at any depth, is not ignored, the command is REJECTED at runtime. The
protocol mirror (`invisible_core.juggler.protocol`) is generated from that same
`Protocol.js`, so a fake engine that refuses what the mirror does not declare
refuses what the real one would.

⛔ ONE CHECK, AT EVERY DEPTH. The fake of `test_a_click_is_delivered_once.py`
used to look at the top-level fields only, and the fake of
`test_the_callers_world.py` at none: `worldKind`, a field of the init scripts
INSIDE `Page.setInitScripts`, went out in every session of the firefox-49 train
while the mirror did not declare it, and nothing in this suite could see it.
The wrapper's e2e would have, against the binary (`_gen_protocol --check`),
which is the last place to find it ([B322]).

The rules are the engine's, one for one: an `Optional` accepts an ABSENT field
and nothing else that its inner type refuses (a `None`, sent as `null`, is not
absent); a `Nullable` accepts `null`; `Any` accepts anything, absence included;
a `Number` is not a `bool`.
"""
from __future__ import annotations

from invisible_core.juggler.protocol import COMMANDS

#: JavaScript's `undefined`: the field is not in the object at all.
_ABSENT = object()


def _check(value, spec, where: str) -> None:
    kind = spec["k"]
    if kind == "Any":
        return
    if kind == "Optional":
        if value is not _ABSENT:
            _check(value, spec["of"], where)
        return
    if value is _ABSENT:
        raise AssertionError("%s is missing and the engine requires it" % where)
    if kind == "Nullable":
        if value is not None:
            _check(value, spec["of"], where)
        return
    if kind == "String":
        ok = isinstance(value, str)
    elif kind == "Boolean":
        ok = isinstance(value, bool)
    elif kind == "Number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif kind == "Enum":
        ok = value in spec["values"]
    elif kind == "Array":
        if not isinstance(value, list):
            raise AssertionError("%s: %r is not an Array" % (where, value))
        for i, item in enumerate(value):
            _check(item, spec["of"], "%s[%d]" % (where, i))
        return
    elif kind == "Object":
        if not isinstance(value, dict):
            raise AssertionError("%s: %r is not an Object" % (where, value))
        fields = spec.get("fields", {})
        undeclared = sorted(set(value) - set(fields))
        if undeclared:
            raise AssertionError("%s carries fields the engine does not declare: %s"
                                 % (where, undeclared))
        for name, field in fields.items():
            _check(value.get(name, _ABSENT), field, "%s.%s" % (where, name))
        return
    else:
        raise AssertionError("%s: a kind this check does not know: %r" % (where, kind))
    if not ok:
        raise AssertionError("%s: %r is not %s %s" % (where, value, kind, spec.get("values", "")))


def check_command(method: str, params) -> None:
    """Refuse `params` the way the engine would refuse `method` with them."""
    if method not in COMMANDS:
        raise AssertionError("%s is not a command the engine declares" % method)
    spec = COMMANDS[method]["params"]
    if spec is None:
        if params:
            raise AssertionError("%s takes no parameters, got %r" % (method, params))
        return
    _check(dict(params or {}), spec, method)
