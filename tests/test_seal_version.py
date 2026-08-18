"""The seal is the only version surface, and it is the one pip compares.

Spec: docs/firefox-stealth-architecture/17-release-seal-spec.md sections 3.2 (7),
4.7, 4.8, 4.9 and 7. Audit: 16-version-divergence-matrix.md section 4 (2) and S1.

Cells closed here:
  S1  the core's pip-visible version never moved, so pip's resolver hit
      `else: continue` and skipped the upgrade on every install for a year.
      The derived version is the mechanism that makes the upgrade take, so it
      is tested as a mechanism: a different tag MUST produce a different string.
  D8-adjacent  the claim (User-Agent) is a projection of the seal, including on
      the INVISIBLE_SEAL_FILE escape hatch, which therefore cannot decouple the
      claim from the engine - it can only move both.

No network, no browser, no build. The derivation is exercised by executing the
shipped _version.py against synthetic seals in tmp_path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import invisible_core
from invisible_core.seal import SUPPORTED_SEAL_SCHEMA, load_seal, packaged_seal_path

PKG_DIR = Path(invisible_core.__file__).resolve().parent      # .../src/invisible_core
REPO_ROOT = PKG_DIR.parents[1]                                # the repo holding pyproject.toml

# That walk only lands on the repo when the core is installed EDITABLE. From a
# wheel or an sdist, PKG_DIR is site-packages/invisible_core and REPO_ROOT is the
# venv's lib dir, which has no pyproject.toml - the test then died with
# FileNotFoundError instead of skipping. Sources-only assertions belong to a
# source checkout; say so rather than failing everyone who installed normally.
HAVE_SOURCES = (REPO_ROOT / "pyproject.toml").is_file()
needs_sources = pytest.mark.skipif(
    not HAVE_SOURCES,
    reason="asserts against the repo's own pyproject; only meaningful from a "
           "source checkout or an editable install",
)


def make_seal_json(dirpath: Path, *, tag: str, version: str = "151.0",
                   build_id: str = "20260724001949", assets: dict | None = None) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / "seal.json"
    p.write_text(json.dumps({
        "schema": 2, "tag": tag, "upstream_version": version, "build_id": build_id,
        "source_commit": "0123456789abcdef", "playwright": {"min": "1.55.0", "max": "1.61.0"},
        "assets": assets or {},
    }, sort_keys=True), encoding="utf-8")
    return p


def run_version_module(tmp_path: Path, tag: str) -> dict:
    """Execute the SHIPPED _version.py against a synthetic seal, the way
    hatchling does when it reads the version out of the source tree."""
    src = (PKG_DIR / "_version.py").read_text(encoding="utf-8")
    home = tmp_path / f"probe-{tag}"
    make_seal_json(home, tag=tag)
    ns = {"__file__": str(home / "_version.py"), "__name__": "_seal_version_probe"}
    exec(compile(src, str(home / "_version.py"), "exec"), ns)
    return ns


# ------------------------------------------------- the mechanism pip relies on

def test_pip_version_moves_when_the_seal_tag_moves(tmp_path):
    """pip's resolver reinstalls a git dependency only when
    `installed_dist.version != candidate.version`. If the string does not move
    on a binary bump, the core is skipped and the user keeps the old engine
    contract forever. This is that string, and it must move."""
    v18 = run_version_module(tmp_path, "firefox-18")
    v19 = run_version_module(tmp_path, "firefox-19")
    rev = v18["CORE_REVISION"]

    assert v18["__version__"] == f"18.{rev}.0"
    assert v19["__version__"] == f"19.{rev}.0"
    assert v18["__version__"] != v19["__version__"], "a tag bump that pip cannot see"


def test_core_revision_moves_the_version_for_a_core_only_release(tmp_path):
    """A pure-Python fix against the same engine still has to reach users, which
    is what CORE_REVISION is for. Same seal, different revision, different
    string."""
    src = (PKG_DIR / "_version.py").read_text(encoding="utf-8")
    assert "CORE_REVISION" in src
    home = tmp_path / "rev"
    make_seal_json(home, tag="firefox-18")
    out = []
    for rev in (0, 1):
        patched = "\n".join(
            f"CORE_REVISION = {rev}" if line.strip().startswith("CORE_REVISION")
            else line for line in src.splitlines())
        ns = {"__file__": str(home / "_version.py"), "__name__": "_probe"}
        exec(compile(patched, str(home / "_version.py"), "exec"), ns)
        out.append(ns["__version__"])
    assert out[0] != out[1], out


def test_packaged_version_is_the_packaged_seal(tmp_path):
    """No second literal: what the package reports is what its own seal says."""
    from invisible_core import _version

    seal = load_seal(packaged_seal_path())
    n = int(seal.tag.split("-")[1])
    assert _version.__version__ == f"{n}.{_version.CORE_REVISION}.0"


@needs_sources
def test_pyproject_hands_the_derived_version_to_the_build_backend():
    """A derived version that the build backend never reads is invisible to pip,
    which is the exact failure this closes. Assert the wiring, not the number."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" in data["project"].get("dynamic", []), \
        "pyproject still pins a hand-typed version"
    assert "version" not in data["project"], "a static version overrides the derivation"
    hatch = data["tool"]["hatch"]["version"]
    assert hatch["source"] == "code"
    assert hatch["path"].endswith("_version.py")
    assert hatch.get("expression", "__version__") == "__version__"


# ----------------------------------------------------- the seal itself ships

def test_seal_json_sits_inside_the_package(tmp_path):
    """It has to be package data, not a repo file: scripts/playwright_pin.txt
    shipped in neither the wheel nor the sdist and no user ever received it."""
    p = packaged_seal_path()
    assert p.exists(), f"no seal shipped at {p}"
    assert p.parent == PKG_DIR
    seal = load_seal(p)
    assert seal.tag.startswith("firefox-") and seal.tag.split("-")[1].isdigit()
    assert seal.upstream_version and seal.build_id
    assert seal.digest and len(seal.digest) == 64


def test_packaged_seal_schema_is_the_one_this_core_understands():
    data = json.loads(packaged_seal_path().read_bytes().decode("utf-8"))
    assert data["schema"] == SUPPORTED_SEAL_SCHEMA


def test_the_packaged_seal_carries_a_build_id_per_leg():
    """Defect measured on the five published firefox-18 archives (2026-07-25):
    one Version, five BuildIDs, because they are five independent CI builds. A
    seal with one scalar BuildID is a launch refusal on four platforms out of
    five, so the shipped seal must carry one per leg."""
    seal = load_seal(packaged_seal_path())
    if seal.is_local:
        pytest.skip("packaged seal is a LOCAL seal with no assets")
    missing = [n for n, a in seal.assets.items() if not a.build_id]
    assert not missing, f"assets with no BuildID of their own: {missing}"
    for name, a in seal.assets.items():
        assert seal.expected_build_ids(platform_key=a.platform, arch=a.arch) == (a.build_id,), \
            f"{name} does not resolve to its own leg"


def test_the_packaged_release_seal_declares_no_seal_wide_build_id():
    """There is no value of a single build_id that is true of five builds. A
    release seal that carried one would be a true statement about one platform
    and a false one about the other four, which is how the scalar survived
    review the first time."""
    data = json.loads(packaged_seal_path().read_bytes().decode("utf-8"))
    if not data.get("assets"):
        pytest.skip("packaged seal is a LOCAL seal (one tree, one build)")
    assert "build_id" not in data, (
        "the release seal still declares a top-level build_id: "
        f"{data.get('build_id')!r}")


def test_a_leg_without_omni_ja_records_no_payload_digest():
    """The Linux archives ship the juggler unpacked and carry no omni.ja, so
    there is nothing to hash for those legs. Empty is the honest value; what
    must never happen is a fabricated digest, or emptiness being read as a
    verified payload (asserted in the adoption path, not here)."""
    seal = load_seal(packaged_seal_path())
    if seal.is_local:
        pytest.skip("packaged seal is a LOCAL seal with no assets")
    for name, a in seal.assets.items():
        if a.omni_sha256:
            assert len(a.omni_sha256) == 64, (name, a.omni_sha256)


def test_every_packaged_asset_name_carries_the_sealed_base_version():
    """The tag/base pairing, checked on the bytes that were published. A name
    that disagrees with the version inside its own archive 404s for every user
    (issue #14) or, worse, resolves to another base."""
    seal = load_seal(packaged_seal_path())
    if seal.is_local:
        pytest.skip("packaged seal is a LOCAL seal with no assets")
    for name in seal.assets:
        assert seal.upstream_version in name, (name, seal.upstream_version)


def test_seal_digest_is_stable_across_reloads(tmp_path):
    p = make_seal_json(tmp_path / "d", tag="firefox-18")
    assert load_seal(p).digest == load_seal(p).digest


# ------------------------------------------------------------ the escape hatch

def test_substituted_seal_moves_the_claim_with_the_engine(tmp_path):
    """INVISIBLE_SEAL_FILE substitutes, it never disables. Because the
    User-Agent is a projection of the ACTIVE seal, even the opt-out path cannot
    produce a browser whose claim contradicts its engine. Run in a subprocess
    because the active seal is resolved once per process."""
    p = make_seal_json(tmp_path / "sub", tag="firefox-99", version="152.5.1",
                       build_id="20990101000000")
    code = (
        "import json; from invisible_core import constants as c; "
        "print(json.dumps({'tag': c.BINARY_VERSION, 'ver': c.FIREFOX_UPSTREAM_VERSION, "
        "'ua': c.USER_AGENT, 'base': c.BINARY_BASENAME, "
        "'asset': c.ARCHIVE_NAME('win32', 'x86_64')}))"
    )
    env = dict(os.environ, INVISIBLE_SEAL_FILE=str(p))
    env.pop("INVISIBLE_PLAYWRIGHT_CACHE_DIR", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])

    assert got["tag"] == "firefox-99"
    assert got["ver"] == "152.5.1"
    # Firefox puts only MAJOR.MINOR in the UA, so the claim tracks the seal in
    # the form a real build emits.
    assert "rv:152.5" in got["ua"] and got["ua"].endswith("Firefox/152.5"), got["ua"]
    assert got["base"] == "firefox-152.5.1-stealth"
    assert got["asset"] == "firefox-152.5.1-stealth-win-x86_64.zip"
    assert "firefox-99" in r.stderr, "a substituted seal must announce itself"


def test_a_local_tagged_seal_can_actually_be_imported(tmp_path):
    """D2: the remedy this package PRINTS must not break the package.

    verify_engine's refusal text and `python -m invisible_core seal` both tell
    the user to generate a local seal and export INVISIBLE_SEAL_FILE, and the
    seal subcommand defaults --tag to "local". contract_n did
    `int(self.tag.split("-")[1])`, constants.py evaluates CONTRACT_N at import
    time, so following our own documented advice raised IndexError on
    `import invisible_core`. A subprocess because the active seal resolves once
    per process, and this is the import itself.
    """
    p = make_seal_json(tmp_path / "localtag", tag="local")
    code = (
        "import json, invisible_core; from invisible_core import constants as c; "
        "print(json.dumps({'tag': c.BINARY_VERSION, 'n': c.CONTRACT_N, "
        "'ua': c.USER_AGENT, 'ver': invisible_core.__version__}))"
    )
    env = dict(os.environ, INVISIBLE_SEAL_FILE=str(p))
    env.pop("INVISIBLE_PLAYWRIGHT_CACHE_DIR", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"import invisible_core died under a local seal:\n{r.stderr}"
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["tag"] == "local"
    # 0 is "not a numbered release contract", which is the honest answer for a
    # seal that names no release. It is not a fallback to some other release.
    assert got["n"] == 0, got
    # The claim still moves with the substituted seal: a local seal is a
    # supported state, not a hole in the coupling.
    assert "rv:151.0" in got["ua"], got["ua"]
    # The package version stays a property of the INSTALLED files, not of an
    # env var: a substituted seal must not be able to restate the version pip
    # and every consumer compares.
    from invisible_core import _version
    assert got["ver"] == _version.__version__, got


@pytest.mark.parametrize("i,tag,n,release", [
    (0, "firefox-18", 18, True),
    (1, "firefox-0", 0, True),
    (2, "local", 0, False),
    (3, "", 0, False),
    (4, "firefox-", 0, False),
    (5, "firefox-18b", 0, False),
    (6, "firefox-18.1", 0, False),
    (7, "my-build-3", 0, False),
    (8, "firefox-18-rc1", 0, False),
])
def test_contract_n_answers_instead_of_raising(tmp_path, i, tag, n, release):
    """Read at import time, so it may never raise for any tag anyone can type.
    Every one of these except the first two used to be an IndexError or a
    ValueError on `import invisible_core`."""
    seal = load_seal(make_seal_json(tmp_path / f"tag{i}", tag=tag))
    assert seal.contract_n == n
    assert seal.is_release is release


def test_a_local_seal_says_so_when_it_describes_itself(tmp_path):
    """Every line that shows a seal (doctor, the substitution notice, the
    manager's status) should make the local case visible, because a local seal
    can verify a tree but can never download one."""
    local = load_seal(make_seal_json(tmp_path / "l", tag="local"))
    assert "local seal" in local.describe()


# ------------------------------------------------- one headline version (D3)

def test_the_public_version_is_the_derived_one():
    """D3: `invisible_core.__version__` was importlib.metadata's install record
    while `invisible_core._version.__version__` was derived from seal.json.
    Measured in this tree: 0.1.0 against 18.0.0. Anyone quoting the public one
    in a bug report quoted the number the design says lies."""
    from invisible_core import _version

    assert invisible_core.__version__ == _version.__version__
    assert not invisible_core.__version__.startswith("0.0.0+"), \
        "the headline version fell back to the metadata placeholder"


def test_a_stale_install_record_does_not_become_the_version(tmp_path):
    """The two numbers are genuinely different facts, so both are kept - but the
    headline one is what will RUN. Forced apart here: the record says 0.1.0, the
    files derive 18.0.0, and __version__ must be the files."""
    code = (
        "import importlib.metadata as m; m.version = lambda name: '0.1.0';"
        "import json, invisible_core;"
        "print(json.dumps({'v': invisible_core.__version__, "
        "'rec': invisible_core.__install_record_version__}))"
    )
    env = dict(os.environ)
    env.pop("INVISIBLE_SEAL_FILE", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    from invisible_core import _version
    assert got["rec"] == "0.1.0", "the install record is still readable, under its own name"
    assert got["v"] == _version.__version__, got
    assert got["v"] != got["rec"], "the public version is the install record again"


def test_both_version_names_are_exported_and_cannot_be_confused():
    assert "__version__" in invisible_core.__all__
    assert "__install_record_version__" in invisible_core.__all__


def test_substituted_seal_cannot_be_used_to_download_a_foreign_build(tmp_path, monkeypatch):
    """A local seal has no assets, so the hatch can verify a tree you point at
    but can never fetch one. There is no value of any variable that turns the
    check off."""
    from invisible_core.download import cache_root, ensure_binary
    from invisible_core.seal import SealError

    # Belt and braces: this call must refuse before it reaches any cache, but a
    # future reordering must never be able to reach the developer's real one.
    monkeypatch.setenv("INVISIBLE_PLAYWRIGHT_CACHE_DIR", str(tmp_path / "Cache"))
    assert str(cache_root()).startswith(str(tmp_path))

    seal = load_seal(make_seal_json(tmp_path / "loc", tag="local"))
    assert seal.is_local
    with pytest.raises(SealError):
        ensure_binary(seal=seal)
