"""The default selection of a repository runs under the world plugin ([B345]).

    python -m invisible_core.world_job            # judge the repository you are in
    python -m invisible_core.world_job --selftest

The pre-push runs the default selection, so two things together make it free of
the world (the engine, a display, the network): the `e2e` marker excludes the
tests that need it, and `invisible_core.testing.world`, loaded by the root
conftest, refuses one that is not marked and reaches for it anyway. This gate
checks both on THIS tree:

  * `[tool.pytest.ini_options] addopts` excludes `e2e`;
  * `tests/conftest.py` names the plugin in its module-level `pytest_plugins`,
    read from the assignment and not the text (a comment naming the plugin is
    not the plugin being loaded).

Each repository runs it on its own tree in its own CI, with the core it pins:
whoever runs it has a core that ships the plugin. It used to be a test in the
core that read the clients' shared clones, at their published tags, so it was
red between a client pinning the core and that client's next release, and no
pull request on the client could turn it green (item 79's class, a check
judging a tree nobody was changing). `install_job` checks the other half: an
install job blocks the plugin its conftest loads.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import sys
import tomllib

PLUGIN = "invisible_core.testing.world"
CONFTEST = pathlib.Path("tests") / "conftest.py"


def loads_world(source: str) -> bool:
    """Whether a conftest's module-level `pytest_plugins` names the plugin."""
    for node in ast.parse(source).body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "pytest_plugins" for t in node.targets)
                and isinstance(node.value, (ast.List, ast.Tuple))):
            return any(isinstance(e, ast.Constant) and e.value == PLUGIN
                       for e in node.value.elts)
    return False


def addopts_problems(pyproject: str) -> list:
    addopts = (tomllib.loads(pyproject).get("tool", {}).get("pytest", {})
               .get("ini_options", {}).get("addopts", ""))
    if isinstance(addopts, list):
        addopts = " ".join(addopts)
    if "not e2e" not in addopts:
        return ["the default selection does not exclude e2e (addopts: %r)" % addopts]
    return []


def conftest_problems(source: "str | None") -> list:
    if source is None:
        return ["there is no %s to load the world plugin" % CONFTEST.as_posix()]
    if not loads_world(source):
        return ["%s does not load %s, so a default test that starts the engine "
                "runs it on the machine of whoever pushes" % (CONFTEST.as_posix(), PLUGIN)]
    return []


def judge(root: pathlib.Path) -> list:
    """Every problem of this repository's default selection, as (where, what)."""
    found = [("pyproject.toml", p) for p in
             addopts_problems((root / "pyproject.toml").read_text(encoding="utf-8"))]
    conftest = root / CONFTEST
    source = conftest.read_text(encoding="utf-8") if conftest.is_file() else None
    found += [(CONFTEST.as_posix(), p) for p in conftest_problems(source)]
    return found


def git_root(start: "pathlib.Path | None" = None) -> pathlib.Path:
    from .install_job import git_root as _git_root
    return _git_root(start)


_GOOD_ADDOPTS = '[tool.pytest.ini_options]\naddopts = "-m \'not slow and not e2e\' --strict-markers"\n'
KNOWN_BAD_CONFTESTS = (
    ("no pytest_plugins", "import os\n"),
    ("the plugin only in a comment", "# pytest_plugins = ['invisible_core.testing.world']\n"),
    ("another plugin", "pytest_plugins = ['pytester']\n"),
    ("the plugin's name misspelt", "pytest_plugins = ['invisible_core.testing.worlds']\n"),
)
MUST_NOT_FIRE_CONFTESTS = (
    ("as a list", 'pytest_plugins = ["invisible_core.testing.world"]\n'),
    ("as a tuple, with another", 'pytest_plugins = ("pytester", "invisible_core.testing.world")\n'),
)
KNOWN_BAD_PYPROJECTS = (
    ("no addopts", "[tool.pytest.ini_options]\nmarkers = []\n"),
    ("e2e not excluded", '[tool.pytest.ini_options]\naddopts = "-m \'not slow\'"\n'),
)


def selftest() -> int:
    bad = 0
    print("--- conftests that MUST be refused ---")
    for name, text in KNOWN_BAD_CONFTESTS:
        hit = bool(conftest_problems(text))
        print("  %s: %s" % ("killed" if hit else "SURVIVED", name))
        bad += not hit
    hit = bool(conftest_problems(None))
    print("  %s: no conftest at all" % ("killed" if hit else "SURVIVED"))
    bad += not hit
    print("--- conftests that must NOT be refused ---")
    for name, text in MUST_NOT_FIRE_CONFTESTS:
        found = conftest_problems(text)
        print("  %s: %s" % ("FALSE POSITIVE" if found else "silent", name))
        bad += bool(found)
    print("--- default selections that MUST be refused ---")
    for name, text in KNOWN_BAD_PYPROJECTS:
        hit = bool(addopts_problems(text))
        print("  %s: %s" % ("killed" if hit else "SURVIVED", name))
        bad += not hit
    found = addopts_problems(_GOOD_ADDOPTS)
    print("  %s: e2e excluded" % ("FALSE POSITIVE" if found else "silent"))
    bad += bool(found)
    print()
    print("selftest: %s" % ("ALL GOOD" if not bad else "%d PROBLEMS" % bad))
    return 1 if bad else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", help="the repository to judge "
                                  "(default: the one you are standing in)")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        return selftest()
    root = pathlib.Path(a.root).resolve() if a.root else git_root()
    found = judge(root)
    if found:
        print("REFUSED:")
        for where, problem in found:
            print("   %s: %s" % (where, problem))
        return 1
    print("world_job: the default selection excludes e2e and runs under %s (%s)"
          % (PLUGIN, root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
