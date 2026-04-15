"""Shared test fixtures."""

from __future__ import annotations

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
