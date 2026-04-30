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
