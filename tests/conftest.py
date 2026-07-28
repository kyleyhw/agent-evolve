"""Shared test fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_evolve.models import (
    BackendSpec,
    Candidate,
    EvolutionSpec,
    Metric,
    OptimiseDirection,
    ProblemSpec,
    ReviewerVerdict,
    RuntimeModeSpec,
    SafetySpec,
    ScopeSpec,
)


def run_git(repo: Path, *args: str) -> str:
    """Run git in *repo* for test setup/assertions; returns stripped stdout.

    Asserts success loudly (with git's stderr) rather than returning a
    sentinel — in tests, a failed setup command is a broken test, not a
    signal to degrade gracefully.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr.strip()}"
    return result.stdout.strip()


def init_git_repo(path: Path) -> Path:
    """Initialise a throwaway repository at *path* with one commit on ``main``.

    Deliberately self-contained so machine-global git config cannot leak in:
    explicit identity, ``commit.gpgsign false`` (a global signing key would
    hang non-interactive tests), and ``init -b main`` to pin the branch name
    regardless of the machine's ``init.defaultBranch``.
    """
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-b", "main")
    run_git(path, "config", "user.email", "test@example.com")
    run_git(path, "config", "user.name", "Test User")
    run_git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    run_git(path, "add", "-A")
    run_git(path, "commit", "-m", "seed commit")
    return path


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A throwaway git repository with one commit on branch ``main``."""
    return init_git_repo(tmp_path / "repo")


@pytest.fixture
def sample_spec() -> ProblemSpec:
    return ProblemSpec(
        description="test problem",
        mode="runtime",
        eval_command="pytest -x",
        metrics=[
            Metric(name="duration_ms", optimise=OptimiseDirection.MINIMIZE),
            Metric(name="test_pass_rate", optimise=OptimiseDirection.MAXIMIZE, minimum=1.0),
        ],
        scope=ScopeSpec(
            target_files=["src/pricing/calculator.py", "src/pricing/utils.py"],
            do_not_touch=["src/auth/", "src/pricing/models.py"],
            max_diff_files=3,
        ),
        evolution=EvolutionSpec(rounds=2, candidates_per_round=2),
        runtime_mode=RuntimeModeSpec(property_test_samples=50),
        safety=SafetySpec(),
        backend=BackendSpec(type="local", root_dir="evolve-state-test"),
    )


@pytest.fixture
def baseline_candidate() -> Candidate:
    return Candidate(
        problem_id="1",
        candidate_id="1",
        operator="explore",
        round=1,
        hypothesis="baseline",
        status="scored",
        metrics={"duration_ms": 120.0, "test_pass_rate": 1.0},
    )


@pytest.fixture
def approved_verdict() -> ReviewerVerdict:
    return ReviewerVerdict(
        verdict="APPROVE",
        reason="clean",
        checklist={"scope_compliant": True, "metrics_improved": True},
        confidence="high",
    )
