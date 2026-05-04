"""Unit tests for :mod:`agent_evolve.git_utils`.

The detector shells out to ``git`` — these tests stub :func:`subprocess.run`
to fix every branch deterministically without requiring a real git repo
or specific filesystem state.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from agent_evolve import git_utils


class _FakeCompleted:
    """Minimal stand-in for :class:`subprocess.CompletedProcess`."""

    def __init__(self, *, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def _make_runner(responses: list[_FakeCompleted | Exception]):
    """Return a callable that pops from *responses* on each ``subprocess.run`` call.

    The tests script the exact sequence of git invocations the detector
    is expected to make; any extra call raises ``AssertionError``.
    """
    queue = list(responses)
    invocations: list[list[str]] = []

    def fake_run(args: list[str], **_: Any) -> _FakeCompleted:
        invocations.append(list(args))
        if not queue:
            raise AssertionError(f"unexpected extra git call: {args}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    fake_run.invocations = invocations  # type: ignore[attr-defined]
    return fake_run


def test_detect_uses_origin_head_when_present(monkeypatch):
    """Strategy 1: ``origin/<branch>`` from ``symbolic-ref`` wins."""
    fake = _make_runner([_FakeCompleted(returncode=0, stdout="origin/master\n")])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.detect_default_branch() == "master"
    assert fake.invocations[0][1:] == ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]


def test_detect_falls_back_to_current_branch_when_origin_head_missing(monkeypatch):
    """Strategy 2: a non-zero ``symbolic-ref`` skips to ``rev-parse``."""
    fake = _make_runner([
        _FakeCompleted(returncode=128, stdout=""),
        _FakeCompleted(returncode=0, stdout="develop\n"),
    ])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.detect_default_branch() == "develop"
    assert fake.invocations[1][1:] == ["rev-parse", "--abbrev-ref", "HEAD"]


def test_detect_treats_detached_head_as_no_signal(monkeypatch):
    """A detached ``HEAD`` returns the literal ``"HEAD"`` — must fall through to fallback."""
    fake = _make_runner([
        _FakeCompleted(returncode=128, stdout=""),
        _FakeCompleted(returncode=0, stdout="HEAD\n"),
    ])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.detect_default_branch() == "main"


def test_detect_returns_main_when_all_strategies_fail(monkeypatch):
    """Both git invocations failing → conservative ``"main"`` fallback."""
    fake = _make_runner([
        _FakeCompleted(returncode=128, stdout=""),
        _FakeCompleted(returncode=128, stdout=""),
    ])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.detect_default_branch() == "main"


def test_detect_returns_main_when_git_binary_missing(monkeypatch):
    """``FileNotFoundError`` from the absent binary must not surface."""
    fake = _make_runner([
        FileNotFoundError("git not on PATH"),
        FileNotFoundError("git not on PATH"),
    ])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.detect_default_branch() == "main"


def test_detect_handles_timeout_silently(monkeypatch):
    """A hung git invocation must time out into a fallback, not propagate."""
    fake = _make_runner([
        subprocess.TimeoutExpired(cmd="git", timeout=2.0),
        _FakeCompleted(returncode=0, stdout="trunk\n"),
    ])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.detect_default_branch() == "trunk"


def test_detect_strips_non_origin_remote_prefix(monkeypatch):
    """``upstream/main``-style output is partitioned on the first slash."""
    fake = _make_runner([_FakeCompleted(returncode=0, stdout="upstream/main\n")])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.detect_default_branch() == "main"


def test_detect_passes_cwd_through(monkeypatch, tmp_path):
    """The *cwd* argument must reach the underlying ``subprocess.run`` call."""
    captured: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> _FakeCompleted:
        captured["cwd"] = kwargs.get("cwd")
        captured["args"] = args
        return _FakeCompleted(returncode=0, stdout="origin/main\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    git_utils.detect_default_branch(cwd=tmp_path)
    assert captured["cwd"] == str(tmp_path)


@pytest.mark.parametrize("noisy_output", ["", "   \n", "   "])
def test_detect_treats_blank_stdout_as_no_signal(monkeypatch, noisy_output: str):
    """Empty / whitespace-only stdout from a successful git call must not be returned."""
    fake = _make_runner([
        _FakeCompleted(returncode=0, stdout=noisy_output),
        _FakeCompleted(returncode=0, stdout=noisy_output),
    ])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.detect_default_branch() == "main"


# ---------------------------------------------------------------------------
# current_sha
# ---------------------------------------------------------------------------


def test_current_sha_returns_full_hex_when_git_succeeds(monkeypatch):
    """``git rev-parse <branch>`` output is returned verbatim when shaped like a SHA."""
    sha = "0123456789abcdef0123456789abcdef01234567"
    fake = _make_runner([_FakeCompleted(returncode=0, stdout=f"{sha}\n")])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.current_sha("main") == sha
    # Argv plumbed correctly.
    assert fake.invocations[0][1:] == ["rev-parse", "main"]


def test_current_sha_rejects_non_sha_output(monkeypatch):
    """Output that is not 40 hex chars (e.g. an error message) returns None."""
    fake = _make_runner([_FakeCompleted(returncode=0, stdout="not-a-sha\n")])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.current_sha("main") is None


def test_current_sha_returns_none_on_git_error(monkeypatch):
    """Non-zero exit code (e.g. branch does not exist) returns None, no exception."""
    fake = _make_runner([_FakeCompleted(returncode=128, stdout="")])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.current_sha("nonexistent-branch") is None


def test_current_sha_returns_none_when_git_binary_missing(monkeypatch):
    fake = _make_runner([FileNotFoundError("git not on PATH")])
    monkeypatch.setattr(subprocess, "run", fake)
    assert git_utils.current_sha("main") is None


# ---------------------------------------------------------------------------
# diff_stats
# ---------------------------------------------------------------------------


def test_diff_stats_counts_files_and_lines():
    """Standard two-file diff with mixed additions and deletions."""
    diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "index 1234..5678 100644\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,3 +1,4 @@\n"
        " context line\n"
        "-removed one\n"
        "+added one\n"
        "+added two\n"
        "diff --git a/src/bar.py b/src/bar.py\n"
        "index abcd..ef01 100644\n"
        "--- a/src/bar.py\n"
        "+++ b/src/bar.py\n"
        "@@ -10,2 +10,1 @@\n"
        "-removed\n"
    )
    stats = git_utils.diff_stats(diff)
    assert stats == {"files_changed": 2, "additions": 2, "deletions": 2}


def test_diff_stats_ignores_file_header_lines():
    """``+++`` and ``---`` markers are file headers, not added/removed content."""
    diff = (
        "diff --git a/foo b/foo\n"
        "--- a/foo\n"
        "+++ b/foo\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    stats = git_utils.diff_stats(diff)
    assert stats["additions"] == 1
    assert stats["deletions"] == 1


def test_diff_stats_empty_input_yields_zero_counts():
    assert git_utils.diff_stats("") == {
        "files_changed": 0, "additions": 0, "deletions": 0,
    }


def test_diff_stats_tolerates_malformed_headers():
    """A ``diff --git`` header without the expected b/<path> structure is skipped silently."""
    diff = (
        "diff --git\n"
        "+a stray addition\n"
    )
    stats = git_utils.diff_stats(diff)
    # No files counted (header malformed) but the +/- lines are still tallied.
    assert stats == {"files_changed": 0, "additions": 1, "deletions": 0}
