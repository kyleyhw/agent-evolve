"""Local filesystem backend — great for offline runs, CI, and unit tests.

Layout::

    evolve-state/
        <problem_id>/
            problem.json              ← ProblemSpec + Trait Matrix + graph
            candidates/
                <candidate_id>.json   ← serialized Candidate (the EVOLVE_STATE)
            graph.mmd                 ← latest Mermaid source
            report.html               ← latest D3 HTML report (optional)
            final_pr.json             ← written by finalize(); describes the "PR"

"Branches" are represented as directories under ``branches/`` when the backend
is used against a real git checkout; for pure-state runs (no checkout) the
branch is just a label on the Candidate.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from agent_evolve.backends.base import EvolveBackend
from agent_evolve.models import Candidate, EquivalenceReport, ProblemSpec, ReviewerVerdict


DEFAULT_ROOT = "evolve-state"


class LocalBackend(EvolveBackend):
    """Filesystem-backed implementation of :class:`EvolveBackend`."""

    def __init__(self, spec: ProblemSpec, *, root: str | Path | None = None) -> None:
        super().__init__(spec)
        self.root = Path(root or spec.backend.root_dir or DEFAULT_ROOT).resolve()
        self.problem_id: str | None = None

    def create_problem(self, spec: ProblemSpec) -> str:
        problem_id = self._next_problem_id()
        self.problem_id = problem_id
        problem_dir = self._problem_dir(problem_id)
        (problem_dir / "candidates").mkdir(parents=True, exist_ok=True)

        problem_doc = {
            "problem_id": problem_id,
            "created_at": _now_iso(),
            "spec": _spec_to_dict(spec),
            "trait_matrix": [],
            "graph_mermaid": "",
            "report_html_path": None,
            "status": "active",
        }
        self._atomic_write_json(problem_dir / "problem.json", problem_doc)
        return problem_id

    def submit_candidate(self, candidate: Candidate) -> str:
        self._ensure_problem()
        candidate.status = "pending"
        self._write_candidate(candidate)
        self._append_trait_row(candidate)
        return candidate.candidate_id

    def score_candidate(
        self,
        candidate_id: str,
        metrics: dict[str, float],
        *,
        equivalence: EquivalenceReport | None = None,
    ) -> None:
        candidate = self._read_candidate(candidate_id)
        candidate.metrics.update(metrics)
        if equivalence is not None:
            candidate.equivalence_report = equivalence
        candidate.status = "scored"
        self._write_candidate(candidate)
        self._refresh_trait_row(candidate)

    def record_verdict(self, candidate_id: str, verdict: ReviewerVerdict) -> None:
        candidate = self._read_candidate(candidate_id)
        candidate.reviewer_verdict = verdict
        candidate.status = "approved" if verdict.verdict == "APPROVE" else "rejected"
        self._write_candidate(candidate)
        self._refresh_trait_row(candidate)

    def get_leaderboard(self) -> list[Candidate]:
        self._ensure_problem()
        candidates_dir = self._problem_dir(self.problem_id) / "candidates"
        if not candidates_dir.exists():
            return []
        out: list[Candidate] = []
        for path in sorted(candidates_dir.glob("*.json")):
            out.append(self._read_candidate_path(path))
        return out

    def prune(self, candidate_id: str, reason: str) -> None:
        candidate = self._read_candidate(candidate_id)
        candidate.status = "pruned"
        if candidate.conclusion:
            candidate.conclusion = f"{candidate.conclusion}\n\nPruned: {reason}"
        else:
            candidate.conclusion = f"Pruned: {reason}"
        self._write_candidate(candidate)
        self._refresh_trait_row(candidate)

    def update_graph(self, mermaid: str, html_path: str | None = None) -> None:
        self._ensure_problem()
        problem_dir = self._problem_dir(self.problem_id)
        (problem_dir / "graph.mmd").write_text(mermaid, encoding="utf-8")
        doc = self._read_problem_doc()
        doc["graph_mermaid"] = mermaid
        if html_path is not None:
            doc["report_html_path"] = html_path
        self._atomic_write_json(problem_dir / "problem.json", doc)

    def finalize(self, winner_id: str) -> str:
        self.assert_no_merge("finalize")
        winner = self._read_candidate(winner_id)
        if winner.reviewer_verdict is None or winner.reviewer_verdict.verdict != "APPROVE":
            raise ValueError(
                f"cannot finalize on candidate {winner_id}: reviewer verdict is "
                f"{winner.reviewer_verdict.verdict if winner.reviewer_verdict else 'missing'}"
            )

        for other in self.get_leaderboard():
            if other.candidate_id == winner_id:
                continue
            if other.status not in ("pruned", "rejected"):
                self.prune(other.candidate_id, reason=f"not selected — winner was {winner_id}")

        doc = self._read_problem_doc()
        pr_descriptor = {
            "kind": "final_pr",
            "opened_at": _now_iso(),
            "problem_id": self.problem_id,
            "winner_id": winner_id,
            "winner_branch": winner.branch_name(),
            "target_branch": self.spec.safety.protected_branch,
            "reviewers": list(self.spec.safety.final_pr_reviewers),
            "summary": _render_summary(winner, doc),
            "trait_matrix": doc.get("trait_matrix", []),
            "graph_mermaid": doc.get("graph_mermaid", ""),
            "report_html_path": doc.get("report_html_path"),
            "merged": False,
        }
        pr_path = self._problem_dir(self.problem_id) / "final_pr.json"
        self._atomic_write_json(pr_path, pr_descriptor)

        doc["status"] = "awaiting_human_approval"
        doc["winner_id"] = winner_id
        self._atomic_write_json(self._problem_dir(self.problem_id) / "problem.json", doc)
        return str(pr_path)

    def _ensure_problem(self) -> None:
        if self.problem_id is None:
            raise RuntimeError(
                "no active problem — call create_problem() first or set problem_id on the backend"
            )

    def _problem_dir(self, problem_id: str) -> Path:
        return self.root / problem_id

    def _read_problem_doc(self) -> dict[str, Any]:
        self._ensure_problem()
        path = self._problem_dir(self.problem_id) / "problem.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_candidate(self, candidate: Candidate) -> None:
        self._ensure_problem()
        path = self._problem_dir(self.problem_id) / "candidates" / f"{candidate.candidate_id}.json"
        self._atomic_write_json(path, candidate.to_dict())

    def _read_candidate(self, candidate_id: str) -> Candidate:
        self._ensure_problem()
        path = self._problem_dir(self.problem_id) / "candidates" / f"{candidate_id}.json"
        return self._read_candidate_path(path)

    def _read_candidate_path(self, path: Path) -> Candidate:
        return Candidate.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _append_trait_row(self, candidate: Candidate) -> None:
        doc = self._read_problem_doc()
        doc.setdefault("trait_matrix", []).append(_trait_row(candidate))
        self._atomic_write_json(self._problem_dir(self.problem_id) / "problem.json", doc)

    def _refresh_trait_row(self, candidate: Candidate) -> None:
        doc = self._read_problem_doc()
        rows: list[dict[str, Any]] = doc.setdefault("trait_matrix", [])
        new_row = _trait_row(candidate)
        for i, row in enumerate(rows):
            if row.get("candidate_id") == candidate.candidate_id:
                rows[i] = new_row
                break
        else:
            rows.append(new_row)
        self._atomic_write_json(self._problem_dir(self.problem_id) / "problem.json", doc)

    def _next_problem_id(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        existing = [int(p.name) for p in self.root.iterdir() if p.is_dir() and p.name.isdigit()]
        return str(max(existing, default=0) + 1)

    @staticmethod
    def _atomic_write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")
        os.replace(tmp, path)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _trait_row(c: Candidate) -> dict[str, Any]:
    return {
        "candidate_id": c.candidate_id,
        "parent_ids": list(c.parent_ids),
        "operator": c.operator,
        "round": c.round,
        "status": c.status,
        "metrics": dict(c.metrics),
    }


def _spec_to_dict(spec: ProblemSpec) -> dict[str, Any]:
    return {
        "description": spec.description,
        "mode": spec.mode,
        "eval_command": spec.eval_command,
        "metrics": [
            {
                "name": m.name,
                "optimise": m.optimise.value,
                "minimum": m.minimum,
                "maximum": m.maximum,
            }
            for m in spec.metrics
        ],
        "scope": {
            "target_files": list(spec.scope.target_files),
            "do_not_touch": list(spec.scope.do_not_touch),
            "max_diff_files": spec.scope.max_diff_files,
        },
        "evolution": {
            "rounds": spec.evolution.rounds,
            "candidates_per_round": spec.evolution.candidates_per_round,
            "operators": list(spec.evolution.operators),
            "prune_strategy": spec.evolution.prune_strategy,
        },
        "runtime_mode": {
            "equivalence_check": spec.runtime_mode.equivalence_check,
            "property_test_samples": spec.runtime_mode.property_test_samples,
            "regression_tests": spec.runtime_mode.regression_tests,
        },
        "safety": {
            "protected_branch": spec.safety.protected_branch,
            "agents_can_merge": False,
            "require_human_approval": spec.safety.require_human_approval,
            "final_pr_reviewers": list(spec.safety.final_pr_reviewers),
        },
        "backend": {
            "type": spec.backend.type,
            "repo": spec.backend.repo,
            "root_dir": spec.backend.root_dir,
        },
        "agents": {
            "supervisor": spec.agents.supervisor,
            # ``explorer`` is ``str | list[str]``; serialise verbatim so a
            # round-trip through YAML preserves whether the user wrote a
            # bare name or an ensemble list.
            "explorer": (
                list(spec.agents.explorer)
                if isinstance(spec.agents.explorer, list)
                else spec.agents.explorer
            ),
            "reviewer": spec.agents.reviewer,
        },
    }


def _render_summary(winner: Candidate, doc: dict[str, Any]) -> str:
    spec = doc.get("spec", {})
    rows = doc.get("trait_matrix", [])
    lines = [
        f"# Evolution winner: candidate-{winner.candidate_id}",
        "",
        f"**Problem:** {spec.get('description', '(no description)')}",
        f"**Mode:** {spec.get('mode', 'algorithm')}",
        f"**Rounds run:** {max((r.get('round', 0) for r in rows), default=0)}",
        f"**Candidates tried:** {len(rows)}",
        "",
        "## Hypothesis",
        winner.hypothesis or "(none recorded)",
        "",
        "## Conclusion",
        winner.conclusion or "(none recorded)",
        "",
        "## Reviewer verdict",
        f"- **Verdict:** {winner.reviewer_verdict.verdict}" if winner.reviewer_verdict else "- (no verdict)",
    ]
    if winner.reviewer_verdict:
        lines.append(f"- **Reason:** {winner.reviewer_verdict.reason}")
        lines.append(f"- **Confidence:** {winner.reviewer_verdict.confidence}")
    return "\n".join(lines)
