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
ArtifactMode = Literal["replace", "sibling"]


class OptimiseDirection(str, Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


@dataclass(frozen=True)
class Metric:
    """A single metric the evolutionary search cares about.

    A ``minimum``/``maximum`` hard constraint, when set, must be satisfied by
    every candidate or the reviewer rejects regardless of any other score.
    Soft metrics are used for Pareto ranking.

    The ``primary`` flag marks the single metric used by tie-breakers and
    ``top_k`` pruning. At most one metric per :class:`ProblemSpec` may set
    ``primary=True``; if none is marked, the first metric in the list is
    used as the implicit primary (preserving the historical default).
    """

    name: str
    optimise: OptimiseDirection
    minimum: float | None = None
    maximum: float | None = None
    primary: bool = False

    def satisfies(self, value: float) -> bool:
        if self.minimum is not None and value < self.minimum:
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        return True


def primary_metric(metrics: list[Metric]) -> Metric:
    """Return the metric flagged ``primary=True``, or the first metric as fallback.

    Encapsulates the implicit-primary fallback so callers (top_k pruning,
    tie-break logic) do not have to reimplement it. Raises ``ValueError`` on
    an empty list — the manifest parser already rejects that case, so this
    only fires on programmatic misuse.
    """
    if not metrics:
        raise ValueError("primary_metric() requires at least one metric")
    for m in metrics:
        if m.primary:
            return m
    return metrics[0]


@dataclass(frozen=True)
class ScopeSpec:
    target_files: list[str]
    do_not_touch: list[str] = field(default_factory=list)
    max_diff_files: int | None = None


@dataclass(frozen=True)
class SiblingSpec:
    """Per-run options for ``artifact_mode: sibling``.

    All fields default to sensible values so a minimal manifest can opt
    into sibling mode with a one-line ``artifact_mode: sibling``. The
    rename patterns use template tokens documented in the SKILL — see
    :func:`agent_evolve.artifact.expand_template` for the canonical
    list.

    ``symbol_name`` is the identifier in the canonical file that gets
    renamed in the seeded sibling. When ``None``, :mod:`agent_evolve.artifact`
    auto-detects the unique top-level class or function in the target
    file; if there are zero or multiple top-level symbols, it raises
    and the user must set this field explicitly.

    ``output_dir`` is the directory the seeded file is written to. When
    ``None``, the sibling is written alongside the original. Path is
    interpreted relative to the repo root.
    """

    symbol_name: str | None = None
    symbol_rename_pattern: str = "{original}{ProblemId}{Date}"
    file_rename_pattern: str = "{original_stem}_{problem_id}_{date}"
    output_dir: str | None = None


@dataclass(frozen=True)
class EvolutionSpec:
    rounds: int = 4
    candidates_per_round: int = 3
    operators: list[OperatorName] = field(default_factory=lambda: ["mutate", "crossover", "explore"])
    prune_strategy: Literal["pareto", "top_k"] = "pareto"


@dataclass(frozen=True)
class RuntimeModeSpec:
    equivalence_check: Literal["required", "optional", "disabled"] = "required"
    property_test_samples: int = 500
    regression_tests: str | None = None


BranchCleanupMode = Literal["keep", "archive", "delete"]


@dataclass(frozen=True)
class SafetySpec:
    """Hard constraints that agents cannot override.

    ``agents_can_merge`` is always coerced to False in :class:`EvolveBackend`
    regardless of config — this field exists for surface parity with the
    example YAML, not as a tunable.

    ``protected_branch`` defaults to the literal string ``"main"`` for
    direct dataclass construction (kept zero-IO so unit tests are
    deterministic). Loaders that know the target repo
    (``agent_evolve.config.load_manifest`` and the supervisor SKILL's
    natural-language path) replace this default with the result of
    :func:`agent_evolve.git_utils.detect_default_branch`, so the actual
    repo's trunk name (``main`` / ``master`` / ``trunk`` / ``develop``)
    is used in practice. To override, set ``safety.protected_branch``
    explicitly in the manifest.

    ``branch_cleanup`` controls what ``finalize()`` does with the
    non-winning candidate branches. ``archive`` (the default) keeps the
    branches around but marks them so they no longer clutter the active
    list — closed PR + ``evolve-archived`` label on GitHub, status-only
    rename in the local backend's state file. ``delete`` removes the
    branches outright (irreversible — use only when forensic value of
    rejected candidates is not needed). ``keep`` is the historical
    behaviour and leaves losers in place.

    ``run_ablation_report`` controls whether ``finalize()`` runs a
    post-hoc ablation pass on the winning diff. When enabled (default),
    the supervisor splits the winner's diff into git hunks, runs the
    eval with each hunk removed in turn, and attaches the resulting
    contribution table to the final PR body. Set to ``False`` to skip
    this pass — useful when the eval is expensive and the human reviewer
    does not need the per-hunk breakdown.
    """

    protected_branch: str = "main"
    agents_can_merge: bool = False
    require_human_approval: bool = True
    final_pr_reviewers: list[str] = field(default_factory=list)
    branch_cleanup: BranchCleanupMode = "archive"
    run_ablation_report: bool = True


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
    """The full manifest loaded from ``agent-evolve.yaml``.

    Most fields map 1:1 onto YAML sections of the same name. The fields
    documented below need a closer look.

    ``eval_cwd`` is the working directory for the eval command. Use this
    when the eval lives in a subdirectory (``tests/perf/``, ``bench/``)
    rather than at the repo root. When unset, the supervisor uses the
    candidate's working tree root as cwd, matching the historical
    behaviour.

    ``expected_baseline`` and ``expected_baseline_tolerance`` together
    form a sanity-check gate the supervisor runs before round 1: it
    measures the actual baseline by executing ``eval_command`` on
    ``safety.protected_branch``, then refuses to start the search if any
    metric's measured baseline differs from the user-supplied
    ``expected_baseline`` value by more than ``expected_baseline_tolerance``
    (a fractional tolerance, default 5%). Catches misconfigured eval
    commands, stale fixtures, missing datasets, and similar failure
    modes where the search would otherwise optimise toward a number
    that does not reflect production behaviour. When
    ``expected_baseline`` is ``None`` (the default), the gate is
    skipped — the supervisor still records the measured baseline but
    does not validate it.

    ``production_runner`` is an optional second eval command that
    evaluates a candidate against a higher-fidelity / production-equivalent
    benchmark. The supervisor runs it only on candidates the reviewer
    has approved (not on every slot of every round, which would dominate
    wallclock). Disagreement between ``eval_command`` and
    ``production_runner`` metrics triggers an ``INFORMATIVE`` annotation
    (small drift) or a ``REQUEST_CHANGES`` demotion (large drift,
    > ``expected_baseline_tolerance``) on the candidate. Skipped
    silently when unset.
    """

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
    eval_cwd: str | None = None
    expected_baseline: dict[str, float] | None = None
    expected_baseline_tolerance: float = 0.05
    production_runner: str | None = None
    artifact_mode: ArtifactMode = "replace"
    sibling: SiblingSpec | None = None
    version: int = 1

    def primary_metric(self) -> Metric:
        """Convenience: the metric flagged ``primary=True`` (or first metric)."""
        return primary_metric(self.metrics)

    def eval_command_for(self, symbol_name: str) -> str:
        """Return ``eval_command`` with the literal ``{symbol}`` token substituted.

        Used by the sibling-mode seed-step so the eval can be aimed at
        the renamed symbol rather than the original. When the user's
        ``eval_command`` does not include ``{symbol}``, the command is
        returned verbatim — the eval is symbol-agnostic, presumably
        because it imports the canonical name. That case still works
        in ``replace`` mode (which never calls this method) but is
        almost certainly wrong in ``sibling`` mode; the seed-step's
        baseline-validation pass detects the disagreement and aborts.
        """
        return self.eval_command.replace("{symbol}", symbol_name)


@dataclass
class ReviewerVerdict:
    """Reviewer's structured judgment on a single candidate.

    ``informative`` carries an out-of-band note that does not gate
    acceptance — e.g. "approve, but the gain is concentrated in a single
    hunk that may be fragile under input drift". The supervisor surfaces
    this in the trait matrix and final PR body so a human reviewer sees
    it without it competing with the verdict label. ``None`` means no
    informative note attached.
    """

    verdict: ReviewerVerdictLabel
    reason: str
    checklist: dict[str, bool]
    confidence: Literal["high", "medium", "low"]
    informative: str | None = None


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
