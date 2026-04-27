"""Shared data models.

These are the lingua franca between the backends, the agents (via SKILL.md),
the reviewer, the scope enforcer, and the visualization layer. Every piece of
`agent_evolve` that moves state around speaks in terms of these types.

The `EVOLVE_STATE` block embedded in PR bodies / local JSON files deserializes
into :class:`Candidate`; a backend's `get_leaderboard()` returns a list of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Literal

from agent_evolve.eval.equivalence import EquivalenceReport


EvolveMode = Literal["algorithm", "runtime"]
OperatorName = Literal["mutate", "crossover", "explore"]
CandidateStatus = Literal["pending", "scored", "reviewing", "approved", "rejected", "pruned"]
ReviewerVerdictLabel = Literal["APPROVE", "REQUEST_CHANGES", "REJECT"]


class OptimiseDirection(str, Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


@dataclass(frozen=True)
class Metric:
    """A single metric the evolutionary search cares about.

    A ``minimum``/``maximum`` hard constraint, when set, must be satisfied by
    every candidate or the reviewer rejects regardless of any other score.
    Soft metrics are used for Pareto ranking.
    """

    name: str
    optimise: OptimiseDirection
    minimum: float | None = None
    maximum: float | None = None

    def satisfies(self, value: float) -> bool:
        if self.minimum is not None and value < self.minimum:
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        return True


@dataclass(frozen=True)
class ScopeSpec:
    target_files: list[str]
    do_not_touch: list[str] = field(default_factory=list)
    max_diff_files: int | None = None


@dataclass(frozen=True)
class EvolutionSpec:
    rounds: int = 5
    candidates_per_round: int = 3
    operators: list[OperatorName] = field(default_factory=lambda: ["mutate", "crossover", "explore"])
    prune_strategy: Literal["pareto", "top_k"] = "pareto"


@dataclass(frozen=True)
class RuntimeModeSpec:
    equivalence_check: Literal["required", "optional", "disabled"] = "required"
    property_test_samples: int = 500
    regression_tests: str | None = None


@dataclass(frozen=True)
class SafetySpec:
    """Hard constraints that agents cannot override.

    ``agents_can_merge`` is always coerced to False in :class:`EvolveBackend`
    regardless of config — this field exists for surface parity with the
    example YAML, not as a tunable.
    """

    protected_branch: str = "main"
    agents_can_merge: bool = False
    require_human_approval: bool = True
    final_pr_reviewers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BackendSpec:
    type: Literal["github", "gitlab", "local"]
    repo: str | None = None
    root_dir: str | None = None


@dataclass(frozen=True)
class AgentsSpec:
    """Which agent fills each role in the loop.

    Each field is a bare agent name (``"claude"``, ``"gemini"``, ``"codex"``,
    ...) or — for the ``explorer`` role — a *list* of names that forms an
    **ensemble**. The supervisor SKILL — not Python — resolves names to
    concrete CLI invocations, builds the right prompt, and parses
    structured output. The default ``"claude"`` runs the role in-session
    (the current Claude Code session, currently Opus 4.7) via the ``Agent``
    subagent tool; any other value is treated as an external CLI.

    Ensemble semantics (``explorer`` only)
    --------------------------------------
    When ``explorer`` is a list like ``["claude", "gemini"]``, the
    supervisor distributes the round's ``candidates_per_round`` slots
    round-robin across the list. With three slots and the example list
    above, slots 1, 2, 3 are dispatched to ``claude``, ``gemini``,
    ``claude`` — mixing exploration heuristics from different model
    families within a single round.

    The ``supervisor`` field is informational — the supervisor is whatever
    Claude Code session loaded the spec, so swapping it requires a
    headless runner (out of scope for this version).
    """

    supervisor: str = "claude"
    explorer: str | list[str] = "claude"
    reviewer: str = "claude"

    def explorer_list(self) -> list[str]:
        """Always-list view of ``explorer`` — collapses the ``str | list`` union.

        The supervisor SKILL uses this to round-robin slot assignments
        without having to special-case the singleton form.
        """
        if isinstance(self.explorer, str):
            return [self.explorer]
        return list(self.explorer)


@dataclass(frozen=True)
class ProblemSpec:
    """The full manifest loaded from ``agent-evolve.yaml``."""

    description: str
    mode: EvolveMode
    eval_command: str
    metrics: list[Metric]
    scope: ScopeSpec
    evolution: EvolutionSpec
    runtime_mode: RuntimeModeSpec
    safety: SafetySpec
    backend: BackendSpec
    agents: AgentsSpec = field(default_factory=AgentsSpec)
    version: int = 1


@dataclass
class ReviewerVerdict:
    verdict: ReviewerVerdictLabel
    reason: str
    checklist: dict[str, bool]
    confidence: Literal["high", "medium", "low"]


@dataclass
class Candidate:
    """A single attempt in the search — the unit of work in the search graph.

    The serialized form of this dataclass IS the EVOLVE_STATE block embedded
    in PR bodies (GitHub backend) or written as JSON (local backend).
    Every field here has a direct home in the spec in PLAN.md §EVOLVE_STATE.
    """

    problem_id: str
    candidate_id: str
    operator: OperatorName
    round: int
    status: CandidateStatus = "pending"
    parent_ids: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    hypothesis: str = ""
    conclusion: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    equivalence_report: EquivalenceReport | None = None
    reviewer_verdict: ReviewerVerdict | None = None
    evolve_version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.equivalence_report is not None:
            d["equivalence_report"] = asdict(self.equivalence_report)
        if self.reviewer_verdict is not None:
            d["reviewer_verdict"] = asdict(self.reviewer_verdict)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Candidate":
        eq = data.get("equivalence_report")
        rv = data.get("reviewer_verdict")
        payload = {k: v for k, v in data.items() if k not in ("equivalence_report", "reviewer_verdict")}
        candidate = cls(**payload)
        if eq is not None:
            candidate.equivalence_report = EquivalenceReport(**eq)
        if rv is not None:
            candidate.reviewer_verdict = ReviewerVerdict(**rv)
        return candidate

    def branch_name(self) -> str:
        return self.branch or f"evolve/{self.problem_id}/candidate-{self.candidate_id}"
