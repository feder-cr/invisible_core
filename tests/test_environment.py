"""Every environment variable the library reads is named by `invisible_core.environment`.

The list is what `install_job.LEAKS` keeps out of an install test's venvs and
what the world plugin keeps out of a test that is not e2e, so a variable read
somewhere else is one that both of them miss. These tests read the source:
a name of ours written as a literal outside the registry, or a literal key read
from `os.environ`, is a read the registry does not know.
"""
from __future__ import annotations

import ast
import pathlib
import re

from invisible_core import environment

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "invisible_core"
#: Our prefixes: a literal spelled like this is one of our variables.
_OURS = re.compile(r"^(INVISIBLE|STEALTHFOX|INVPW)_[A-Z0-9_]+$")
#: The host's, read for what the host is, not for what the library does.
_HOST = frozenset({"TEMP"})


def _modules():
    """The library's modules, without the registry, the pre-push gate's own
    variables (`hooks.py`) and the test harness's (`testing/`)."""
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        if rel in ("environment.py", "hooks.py") or rel.startswith("testing/"):
            continue
        yield rel, ast.parse(path.read_bytes())


def _is_os_environ(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os")


def _literal_keys(tree):
    """(line, key) of every read of `os.environ` / `os.getenv` by a literal."""
    for n in ast.walk(tree):
        key = None
        if isinstance(n, ast.Call) and n.args:
            f = n.func
            if (isinstance(f, ast.Attribute) and _is_os_environ(f.value)) or (
                    isinstance(f, ast.Attribute) and f.attr == "getenv"
                    and isinstance(f.value, ast.Name) and f.value.id == "os"):
                key = n.args[0]
        elif isinstance(n, ast.Subscript) and _is_os_environ(n.value):
            key = n.slice
        elif isinstance(n, ast.Compare) and any(_is_os_environ(c) for c in n.comparators):
            key = n.left
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            yield n.lineno, key.value


def test_no_module_spells_one_of_our_variables_itself():
    found = [(rel, n.lineno, n.value) for rel, tree in _modules()
             for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and _OURS.match(n.value)]
    assert not found, ("name these from invisible_core.environment, where the "
                       "list of what the library reads lives: %s" % found)


def test_no_module_reads_the_environment_by_a_literal_name():
    found = [(rel, line, key) for rel, tree in _modules()
             for line, key in _literal_keys(tree) if key not in _HOST]
    assert not found, ("a variable read by its literal name is one the registry "
                       "does not know: %s" % found)


def _registry():
    """The registry's own constants, name -> value, read from its source."""
    tree = ast.parse((_SRC / "environment.py").read_bytes())
    return {node.targets[0].id: node.value.value for node in tree.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)}


def test_the_registry_lists_each_of_its_names_once():
    names = _registry()
    assert sorted(names.values()) == sorted(environment.READ), (
        "a constant of the registry outside READ, or READ naming one twice")
    assert not set(environment.SETTINGS) & set(environment.HANDOFFS)


def test_every_registered_name_is_read_by_a_module():
    """A name nobody reads any more would be kept out of every venv and every
    test for nothing, and would say the library reads it."""
    used = {n.attr for _, tree in _modules() for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "environment"}
    unread = sorted(set(_registry()) - used)
    assert not unread, unread


def test_the_install_tests_keep_out_everything_the_library_reads():
    from invisible_core import install_job
    assert set(environment.READ) <= set(install_job.LEAKS)
