"""Backend-agnostic graph data structure.

Given the Trait Matrix from any backend, produce an :class:`EvolutionGraph`
that the Mermaid and HTML renderers both consume. Adding a new output format
(PNG, Discord embed) is just another renderer reading the same structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agent_evolve.models import Candidate, CandidateStatus, OperatorName


NodeKind = Literal["root", "candidate"]
NodeColor = Literal["active", "winner", "pruned", "rejected", "pending"]


@dataclass
class Node:
    id: str
    kind: NodeKind
    label: str
    color: NodeColor
    operator: OperatorName | None = None
    round: int | None = None
    status: CandidateStatus | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    hypothesis: str = ""
    conclusion: str | None = None
    verdict: str | None = None


@dataclass
class Edge:
    parent_id: str
    child_id: str


@dataclass
class EvolutionGraph:
    nodes: list[Node]
    edges: list[Edge]
    winner_id: str | None
    title: str
    problem_id: str

    def node_by_id(self, id: str) -> Node | None:
        for n in self.nodes:
            if n.id == id:
                return n
        return None


def build_graph(
    candidates: list[Candidate],
    *,
    problem_id: str | None = None,
    title: str | None = None,
) -> EvolutionGraph:
    """Construct an :class:`EvolutionGraph` from a list of candidates."""
    if not candidates and problem_id is None:
        problem_id = "?"
    if problem_id is None:
        problem_id = candidates[0].problem_id
    title = title or f"Problem #{problem_id}"

    winner_id = _pick_winner(candidates)

    root = Node(
        id="ROOT",
        kind="root",
        label=title,
        color="active",
    )
    nodes: list[Node] = [root]
    edges: list[Edge] = []

    for c in candidates:
        node = Node(
            id=f"c{c.candidate_id}",
            kind="candidate",
            label=_node_label(c, is_winner=c.candidate_id == winner_id),
            color=_node_color(c, is_winner=c.candidate_id == winner_id),
            operator=c.operator,
            round=c.round,
            status=c.status,
            metrics=dict(c.metrics),
            hypothesis=c.hypothesis,
            conclusion=c.conclusion,
            verdict=c.reviewer_verdict.verdict if c.reviewer_verdict else None,
        )
        nodes.append(node)

        if not c.parent_ids:
            edges.append(Edge(parent_id="ROOT", child_id=node.id))
        else:
            for parent in c.parent_ids:
                edges.append(Edge(parent_id=f"c{parent}", child_id=node.id))

    return EvolutionGraph(
        nodes=nodes,
        edges=edges,
        winner_id=f"c{winner_id}" if winner_id else None,
        title=title,
        problem_id=problem_id,
    )


def _pick_winner(candidates: list[Candidate]) -> str | None:
    approved = [c for c in candidates if c.reviewer_verdict and c.reviewer_verdict.verdict == "APPROVE"]
    if not approved:
        return None
    # Highest round wins ties; within the highest round, first approved.
    approved.sort(key=lambda c: (-c.round, c.candidate_id))
    return approved[0].candidate_id


def _node_color(c: Candidate, *, is_winner: bool) -> NodeColor:
    if is_winner:
        return "winner"
    if c.status == "pruned":
        return "pruned"
    if c.status == "rejected":
        return "rejected"
    if c.status == "approved":
        return "active"
    return "pending"


def _node_label(c: Candidate, *, is_winner: bool) -> str:
    crown = " ⭐" if is_winner else ""
    metric_summary = _metric_summary(c.metrics)
    parts = [
        f"candidate-{c.candidate_id}{crown}",
        f"operator: {c.operator}",
        f"R{c.round}" + (f" | {metric_summary}" if metric_summary else ""),
        f"status: {'WINNER' if is_winner else c.status}",
    ]
    return "\n".join(parts)


def _metric_summary(metrics: dict[str, float]) -> str:
    if not metrics:
        return ""
    primary_key, primary_val = next(iter(metrics.items()))
    if isinstance(primary_val, float) and primary_val != primary_val:
        return f"{primary_key}=NaN"
    if abs(primary_val) >= 1e6 or (primary_val != 0 and abs(primary_val) < 1e-3):
        return f"{primary_key}={primary_val:.2e}"
    if "ms" in primary_key or "duration" in primary_key:
        return f"{primary_val:.0f}ms"
    return f"{primary_key}={primary_val:g}"
