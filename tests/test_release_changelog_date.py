"""The changelog heading is DERIVED from the index, never predicted by hand.

The defect this closes, measured: invisible-playwright 0.16.2 reached the index
at 2026-09-15T23:21Z and its CHANGELOG heading read `## [0.16.2] - 2026-09-16`,
because the person writing it was in Rome and read a wall clock. The consumer's
e2e gate compares that heading against `min(upload_time_iso_8601)[:10]` and went
red on main. 0.6.0 had been a day out the same way before it.

A heading written before the upload is a PREDICTION of a timestamp, so no amount
of care fixes it. `record` runs after the upload, which is the first moment the
fact exists, and it copies the fact out of the very field the gate compares
against - which is why the two cannot disagree by construction.

Hermetic: the index is a dict handed to the reader, never a socket. Nothing here
builds an artifact or touches the network.
"""
from __future__ import annotations

import pytest

from invisible_core import release

pytestmark = pytest.mark.unit


def fake_index(monkeypatch, releases):
    """Serve `releases` as the index. Patches the ONE function that fetches.

    That it is one function is the point of the refactor: patching a single seam
    covers both the version list and the upload date, so a test cannot be reading
    a shape that the other reader never sees.
    """
    monkeypatch.setattr(release, "_index_releases", lambda url: releases)


def files_at(*stamps):
    return [{"upload_time_iso_8601": s} for s in stamps]


def write_changelog(root, body, newline="\r\n"):
    """Write a changelog with EXPLICIT line endings, as bytes.

    CRLF by default because the wrapper's real file is CRLF end to end, and the
    write path under test has to leave it that way.
    """
    path = root / release.CHANGELOG_NAME
    path.write_bytes(newline.join(body).encode("utf-8"))
    return path


BODY = [
    "# Changelog",
    "",
    "## [Unreleased]",
    "",
    "## [0.16.2] - 2026-09-16",
    "",
    "- something happened",
    "",
    "## [0.16.1] - 2026-09-14",
    "",
    "- something else",
    "",
]


def test_the_upload_date_is_the_earliest_file_of_that_version(monkeypatch):
    """The same reduction the consumer's gate applies: min(), then [:10].

    min() and not "the wheel": a release is several files uploaded seconds
    apart, and picking one of them by role is a second rule that can disagree
    with the gate's. The gate takes the earliest, so this takes the earliest.

    The stamps STRADDLE midnight on purpose. With both files on one date, min
    and max return the same string and the test says nothing about which
    reduction is in the code - which is the whole content of the assertion.
    """
    fake_index(monkeypatch, {
        "0.16.2": files_at("2026-09-16T00:00:02.100000Z",
                           "2026-09-15T23:59:58.900000Z"),
    })
    assert release._index_upload_date("0.16.2", "http://index") == "2026-09-15"


def test_an_unknown_version_has_no_date_rather_than_a_guess(monkeypatch):
    fake_index(monkeypatch, {"0.16.1": files_at("2026-09-14T08:00:00Z")})
    assert release._index_upload_date("0.16.2", "http://index") is None
    # And a version the index lists with no files at all is the same answer, not
    # a crash on min() of an empty sequence.
    fake_index(monkeypatch, {"0.16.2": []})
    assert release._index_upload_date("0.16.2", "http://index") is None


def test_a_wrong_heading_is_moved_onto_the_indexs_date(tmp_path, monkeypatch, capsys):
    fake_index(monkeypatch, {"0.16.2": files_at("2026-09-15T23:21:07.100000Z")})
    path = write_changelog(tmp_path, BODY)
    before = path.read_bytes()

    release._align_changelog_date(tmp_path, "0.16.2", "http://index")

    after = path.read_bytes()
    assert b"## [0.16.2] - 2026-09-15\r\n" in after
    assert b"2026-09-16" not in after
    # The heading of every OTHER version is untouched: this aligns one fact, not
    # the file.
    assert b"## [0.16.1] - 2026-09-14\r\n" in after
    assert "2026-09-16 -> 2026-09-15" in capsys.readouterr().out
    # Length-preserving and line-ending-preserving, which is what makes this a
    # one-line diff instead of a file that looks rewritten.
    assert len(after) == len(before)
    assert after.count(b"\r\n") == before.count(b"\r\n")
    assert after.count(b"\n") == before.count(b"\n")


def test_an_lf_changelog_stays_lf(tmp_path, monkeypatch):
    """The CRLF case above is the one that bites on Windows; this is the floor.

    A write that normalised endings would pass the CRLF assertions by accident
    if it happened to normalise TO CRLF, so the opposite file has to be checked
    too. Neither may acquire a single byte of the other's ending.
    """
    fake_index(monkeypatch, {"0.16.2": files_at("2026-09-15T23:21:07.100000Z")})
    path = write_changelog(tmp_path, BODY, newline="\n")

    release._align_changelog_date(tmp_path, "0.16.2", "http://index")

    after = path.read_bytes()
    assert b"\r" not in after
    assert b"## [0.16.2] - 2026-09-15\n" in after


def test_a_correct_heading_is_not_rewritten(tmp_path, monkeypatch, capsys):
    """Idempotence, asserted on the BYTES and on the mtime.

    A rewrite that lands the same bytes is still a rewrite: it is a dirty file in
    `git status` on every re-run of `record`, which is how a no-op ends up in a
    commit nobody meant to make.
    """
    fake_index(monkeypatch, {"0.16.2": files_at("2026-09-16T10:00:00Z")})
    path = write_changelog(tmp_path, BODY)
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns

    release._align_changelog_date(tmp_path, "0.16.2", "http://index")

    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime, "the file was written although nothing changed"
    assert "already reads 2026-09-16" in capsys.readouterr().out


def test_a_project_without_a_changelog_is_not_an_error(tmp_path, monkeypatch):
    """invisible-core itself has no CHANGELOG.md, and `record` must not care.

    It must also not reach the index to find that out: the absent file is the
    first thing checked, so the core's own `record` stays offline.
    """
    def explode(url):
        raise AssertionError("the index was consulted for a project with no changelog")

    monkeypatch.setattr(release, "_index_releases", explode)
    release._align_changelog_date(tmp_path, "0.16.2", "http://index")
    assert not (tmp_path / release.CHANGELOG_NAME).exists()


def test_an_index_that_does_not_know_the_version_leaves_the_file_alone(
        tmp_path, monkeypatch, capsys):
    """The partial remedy, declared. No clock, no invention, and a printed reason.

    This is the state right after an upload the index has not surfaced yet, and
    the wrong answer here is the tempting one: reach for `datetime.now()` and
    write the same guess the author would have written.
    """
    fake_index(monkeypatch, {"0.16.1": files_at("2026-09-14T08:00:00Z")})
    path = write_changelog(tmp_path, BODY)
    before = path.read_bytes()

    release._align_changelog_date(tmp_path, "0.16.2", "http://index")

    assert path.read_bytes() == before
    out = capsys.readouterr().out
    assert "NOT aligned" in out and "does not serve" in out


def test_an_index_that_will_not_answer_leaves_the_file_alone(tmp_path, monkeypatch, capsys):
    """GateBroken from the fetch is caught HERE and nowhere else.

    `record` runs after a successful upload. Letting a flaky index turn that into
    a non-zero exit would lose the ledger entry, which is the expensive outcome
    this whole pipeline is arranged around.
    """
    def broken(url):
        raise release.GateBroken("index cross-check failed: timed out")

    monkeypatch.setattr(release, "_index_releases", broken)
    path = write_changelog(tmp_path, BODY)
    before = path.read_bytes()

    release._align_changelog_date(tmp_path, "0.16.2", "http://index")

    assert path.read_bytes() == before
    assert "did not answer" in capsys.readouterr().out


def test_a_changelog_with_no_heading_for_this_version_is_left_alone(
        tmp_path, monkeypatch, capsys):
    fake_index(monkeypatch, {"0.17.0": files_at("2026-09-15T23:21:07.100000Z")})
    path = write_changelog(tmp_path, BODY)
    before = path.read_bytes()

    release._align_changelog_date(tmp_path, "0.17.0", "http://index")

    assert path.read_bytes() == before
    assert "no `## [0.17.0] - YYYY-MM-DD` heading" in capsys.readouterr().out


def test_the_version_is_matched_exactly_and_not_as_a_prefix(tmp_path, monkeypatch):
    """`0.16.2` must not be found inside `## [0.16.20]`.

    re.escape covers the dots; the brackets are what bound the number. Without
    both, aligning a release would silently redate a different one.
    """
    fake_index(monkeypatch, {"0.16.2": files_at("2026-09-15T23:21:07.100000Z")})
    path = write_changelog(tmp_path, [
        "# Changelog",
        "",
        "## [0.16.20] - 2026-09-16",
        "",
        "- a later release",
        "",
    ])
    before = path.read_bytes()

    release._align_changelog_date(tmp_path, "0.16.2", "http://index")

    assert path.read_bytes() == before, "the 0.16.20 heading was redated as 0.16.2"


def test_versions_read_off_the_same_fetch_as_the_dates(monkeypatch):
    """`_index_versions` is a VIEW of `_index_releases`, not a second download.

    The rule this closes is rule 16: two fetches are two places that know the
    same thing, and they drift the moment one of them is given an option the
    other is not. Patching the fetch has to move both readers.
    """
    fake_index(monkeypatch, {"0.16.2": files_at("2026-09-15T23:21:07Z"),
                             "0.16.1": files_at("2026-09-14T08:00:00Z")})
    assert release._index_versions("http://index") == ["0.16.1", "0.16.2"]


def test_record_aligns_the_changelog_after_writing_the_ledger():
    """The call is WIRED, and it is wired after the ledger write.

    Order matters and cannot be seen from the function's behaviour: aligning
    first would put a changelog edit on disk for a version that the refusal
    branches above may still decline to record.
    """
    import inspect

    src = inspect.getsource(release.cmd_record)
    assert "_align_changelog_date(" in src, "cmd_record does not align the changelog at all"
    assert src.index("write_ledger(") < src.index("_align_changelog_date("), \
        "the changelog is aligned before the ledger is written"


def test_the_publish_workflow_stages_the_changelog_it_realigned():
    """Half a remedy is a silent no-op: `record` rewrites the file on the runner
    and the step that commits used to stage PUBLISHED.json alone, so the new date
    lived exactly as long as the job did.

    Skipped outside a checkout - .github/ is not in the sdist.

    Comments are STRIPPED before the scan. They were not at first, and the
    known-bad bench caught it: replacing the real `if [ -f CHANGELOG.md ]` with
    `if true` left this test green, because the comment above it mentions the
    same line and the assertion was reading the explanation rather than the
    code. That is the defect this project records most often, found here by
    mutating rather than by rereading.
    """
    from pathlib import Path

    wf = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish.yml"
    if not wf.is_file():
        pytest.skip("not a source checkout - .github/ is not shipped")
    text = "\n".join(line for line in wf.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))
    ledger_step = text[text.index("git add PUBLISHED.json"):]
    ledger_step = ledger_step[:ledger_step.index("git commit -m")]
    assert "git add CHANGELOG.md" in ledger_step, (
        "the ledger commit stages PUBLISHED.json and not the changelog `record` "
        "just realigned")
    assert "if [ -f CHANGELOG.md ]; then" in ledger_step, (
        "the staging is unconditional; `git add` on a missing path fails the step "
        "under `bash -e`, and this project has no changelog of its own")
