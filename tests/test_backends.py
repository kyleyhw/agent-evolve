"""Backend tests — local filesystem + safety invariants."""

from __future__ import annotations

import json

import pytest

from agent_evolve.backends import LocalBackend
from agent_evolve.backends.base import EvolveBackend
from agent_evolve.models import Candidate, ReviewerVerdict


def test_cannot_override_agents_can_merge():
    with pytest.raises(TypeError, match="safety invariant"):
        class Evil(LocalBackend):
            @property
            def agents_can_merge(self):
                return True


def test_cannot_override_assert_no_merge():
    with pytest.raises(TypeError, match="safety invariant"):
        class Evil(LocalBackend):
            def assert_no_merge(self, action):
                return None


def test_local_backend_roundtrip(tmp_path, sample_spec):
    backend = LocalBackend(sample_spec, root=tmp_path)
    assert backend.agents_can_merge is False

    problem_id = backend.create_problem(sample_spec)
    assert problem_id == "1"
    assert (tmp_path / problem_id / "problem.json").exists()

    c = Candidate(
        problem_id=problem_id, candidate_id="1", operator="explore", round=1,
        hypothesis="baseline",
    )
    backend.submit_candidate(c)
    backend.score_candidate("1", {"duration_ms": 120.0, "test_pass_rate": 1.0})

    loaded = backend.get_leaderboard()
    assert len(loaded) == 1
    assert loaded[0].status == "scored"
    assert loaded[0].metrics["duration_ms"] == 120.0


def test_local_backend_pruning_updates_status(tmp_path, sample_spec):
    backend = LocalBackend(sample_spec, root=tmp_path)
    pid = backend.create_problem(sample_spec)
    c = Candidate(problem_id=pid, candidate_id="1", operator="explore", round=1)
    backend.submit_candidate(c)
    backend.score_candidate("1", {"duration_ms": 120.0, "test_pass_rate": 1.0})
    backend.prune("1", reason="pareto inferior")

    loaded = backend.get_leaderboard()[0]
    assert loaded.status == "pruned"
    assert "pareto inferior" in loaded.conclusion


def test_local_backend_verdict_roundtrip(tmp_path, sample_spec, approved_verdict):
    backend = LocalBackend(sample_spec, root=tmp_path)
    pid = backend.create_problem(sample_spec)
    c = Candidate(problem_id=pid, candidate_id="1", operator="mutate", round=1)
    backend.submit_candidate(c)
    backend.score_candidate("1", {"duration_ms": 88.0, "test_pass_rate": 1.0})
    backend.record_verdict("1", approved_verdict)

    loaded = backend.get_leaderboard()[0]
    assert loaded.status == "approved"
    assert loaded.reviewer_verdict is not None
    assert loaded.reviewer_verdict.verdict == "APPROVE"


def test_finalize_requires_approved_winner(tmp_path, sample_spec):
    backend = LocalBackend(sample_spec, root=tmp_path)
    pid = backend.create_problem(sample_spec)
    c = Candidate(problem_id=pid, candidate_id="1", operator="explore", round=1)
    backend.submit_candidate(c)
    backend.score_candidate("1", {"duration_ms": 120.0, "test_pass_rate": 1.0})

    with pytest.raises(ValueError, match="reviewer verdict"):
        backend.finalize("1")


def test_finalize_opens_pr_and_marks_non_winners(tmp_path, sample_spec, approved_verdict):
    backend = LocalBackend(sample_spec, root=tmp_path)
    pid = backend.create_problem(sample_spec)

    winner = Candidate(problem_id=pid, candidate_id="1", operator="mutate", round=1,
                       hypothesis="x")
    backend.submit_candidate(winner)
    backend.score_candidate("1", {"duration_ms": 61.0, "test_pass_rate": 1.0})
    backend.record_verdict("1", approved_verdict)

    loser = Candidate(problem_id=pid, candidate_id="2", operator="explore", round=1)
    backend.submit_candidate(loser)
    backend.score_candidate("2", {"duration_ms": 120.0, "test_pass_rate": 1.0})

    pr_path = backend.finalize("1")
    data = json.loads(open(pr_path, encoding="utf-8").read())
    assert data["kind"] == "final_pr"
    assert data["merged"] is False
    assert data["winner_id"] == "1"

    leaderboard = {c.candidate_id: c for c in backend.get_leaderboard()}
    assert leaderboard["2"].status == "pruned"


def test_candidate_serialization_roundtrip():
    original = Candidate(
        problem_id="1", candidate_id="7",
        operator="crossover", round=3,
        parent_ids=["3", "5"], status="approved",
        metrics={"duration_ms": 42.1, "test_pass_rate": 1.0},
        hypothesis="vectorise + cache",
        conclusion="49% faster",
        reviewer_verdict=ReviewerVerdict(
            verdict="APPROVE", reason="clean",
            checklist={"scope_compliant": True}, confidence="high",
        ),
    )
    data = original.to_dict()
    restored = Candidate.from_dict(data)
    assert restored.reviewer_verdict.verdict == "APPROVE"
    assert restored.metrics == original.metrics
    assert restored.parent_ids == original.parent_ids


def test_evolve_backend_is_abstract():
    with pytest.raises(TypeError):
        EvolveBackend(None)  # type: ignore[abstract]
