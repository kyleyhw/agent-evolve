"""GitLab backend.

Mirrors :mod:`agent_evolve.backends.github` but talks to the GitLab REST API
directly (no SDK dependency — the surface we need is small and the auth story
is simpler than PyGithub). Issues take the role of problem roots, and Merge
Requests play the role of PRs.

Expected env vars (in order): ``GL_TOKEN``, ``GITLAB_TOKEN``. Optional
``GITLAB_URL`` for self-hosted instances (defaults to ``https://gitlab.com``).

This implementation is deliberately narrower than the GitHub one: it does
not install server-side push rules (GitLab's "protected branch" API varies
across hosted/self-hosted tiers). The base-class merge invariant still
applies — ``finalize`` opens the MR but never merges.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from agent_evolve.backends.base import EvolveBackend
from agent_evolve.backends.github import (
    EVOLVE_STATE_OPEN,
    EVOLVE_STATE_CLOSE,
    _parse_candidate,
    _render_issue_body,
    _render_pr_body,
    _render_verdict_comment,
)
from agent_evolve.models import Candidate, EquivalenceReport, ProblemSpec, ReviewerVerdict


class GitLabBackend(EvolveBackend):
    def __init__(
        self,
        spec: ProblemSpec,
        *,
        gitlab_token: str | None = None,
        gitlab_url: str | None = None,
    ) -> None:
        super().__init__(spec)
        if not spec.backend.repo:
            raise ValueError("gitlab backend requires backend.repo (e.g. 'group/project')")

        token = gitlab_token or os.environ.get("GL_TOKEN") or os.environ.get("GITLAB_TOKEN")
        if not token:
            raise RuntimeError("no GitLab token — set GL_TOKEN or pass gitlab_token=... explicitly")
        self._token = token
        self._base = (gitlab_url or os.environ.get("GITLAB_URL") or "https://gitlab.com").rstrip("/")
        self._project = quote_plus(spec.backend.repo)
        self.problem_id: str | None = None

    def create_problem(self, spec: ProblemSpec) -> str:
        body = _render_issue_body(spec, trait_matrix=[], mermaid="", report_url=None)
        issue = self._api("POST", f"/projects/{self._project}/issues", data={
            "title": f"[Evolve] {spec.description}",
            "description": body,
            "labels": "evolve",
        })
        self.problem_id = str(issue["iid"])
        return self.problem_id

    def submit_candidate(self, candidate: Candidate) -> str:
        self._ensure_problem()
        mr = self._api("POST", f"/projects/{self._project}/merge_requests", data={
            "source_branch": candidate.branch_name(),
            "target_branch": self.spec.safety.protected_branch,
            "title": f"Draft: [Evolve #{self.problem_id}] candidate-{candidate.candidate_id}: "
                     f"{candidate.operator}",
            "description": _render_pr_body(candidate, self.problem_id),
            "labels": "evolve-candidate",
        })
        mr_iid = str(mr["iid"])
        candidate.candidate_id = mr_iid
        self._api("PUT", f"/projects/{self._project}/merge_requests/{mr_iid}", data={
            "description": _render_pr_body(candidate, self.problem_id),
        })
        self._refresh_issue()
        return mr_iid

    def score_candidate(
        self,
        candidate_id: str,
        metrics: dict[str, float],
        *,
        equivalence: EquivalenceReport | None = None,
    ) -> None:
        candidate = self._load(candidate_id)
        candidate.metrics.update(metrics)
        if equivalence is not None:
            candidate.equivalence_report = equivalence
        candidate.status = "scored"
        self._update_mr(candidate_id, candidate)
        self._refresh_issue()

    def record_verdict(self, candidate_id: str, verdict: ReviewerVerdict) -> None:
        candidate = self._load(candidate_id)
        candidate.reviewer_verdict = verdict
        candidate.status = "approved" if verdict.verdict == "APPROVE" else "rejected"
        self._update_mr(candidate_id, candidate)
        self._comment(candidate_id, _render_verdict_comment(verdict))
        self._refresh_issue()

    def get_leaderboard(self) -> list[Candidate]:
        self._ensure_problem()
        mrs = self._api("GET", f"/projects/{self._project}/merge_requests?labels=evolve-candidate&state=all&per_page=100")
        out: list[Candidate] = []
        for mr in mrs:
            c = _parse_candidate(mr.get("description") or "")
            if c is not None and c.problem_id == self.problem_id:
                out.append(c)
        return out

    def prune(self, candidate_id: str, reason: str) -> None:
        candidate = self._load(candidate_id)
        candidate.status = "pruned"
        candidate.conclusion = (
            f"{candidate.conclusion}\n\nPruned: {reason}" if candidate.conclusion
            else f"Pruned: {reason}"
        )
        self._update_mr(candidate_id, candidate, state_event="close")
        self._comment(candidate_id, f"Pruned: {reason}")
        self._refresh_issue()

    def update_graph(self, mermaid: str, html_path: str | None = None) -> None:
        self._ensure_problem()
        self._refresh_issue(mermaid=mermaid, report_url=html_path)

    def finalize(self, winner_id: str) -> str:
        self.assert_no_merge("finalize")
        self._ensure_problem()
        winner = self._load(winner_id)
        if winner.reviewer_verdict is None or winner.reviewer_verdict.verdict != "APPROVE":
            raise ValueError(
                f"cannot finalize on MR !{winner_id}: reviewer verdict is "
                f"{winner.reviewer_verdict.verdict if winner.reviewer_verdict else 'missing'}"
            )

        for c in self.get_leaderboard():
            if c.candidate_id == winner_id:
                continue
            if c.status not in ("pruned", "rejected"):
                self.prune(c.candidate_id, reason=f"not selected — winner is !{winner_id}")

        final = self._api("POST", f"/projects/{self._project}/merge_requests", data={
            "source_branch": winner.branch_name(),
            "target_branch": self.spec.safety.protected_branch,
            "title": f"[Evolve #{self.problem_id}] winner: candidate-{winner_id} ({winner.operator})",
            "description": _render_final_body(winner, self.problem_id, self.spec),
            "labels": "evolve-winner",
        })
        self._comment_issue(f"Finalised. Winner is !{winner_id}. Awaiting human approval on !{final['iid']}.")
        return final["web_url"]

    def _ensure_problem(self) -> None:
        if self.problem_id is None:
            raise RuntimeError("no active problem — call create_problem() first")

    def _load(self, mr_iid: str) -> Candidate:
        mr = self._api("GET", f"/projects/{self._project}/merge_requests/{mr_iid}")
        candidate = _parse_candidate(mr.get("description") or "")
        if candidate is None:
            raise RuntimeError(f"MR !{mr_iid} missing EVOLVE_STATE block")
        return candidate

    def _update_mr(self, mr_iid: str, candidate: Candidate, *, state_event: str | None = None) -> None:
        data: dict[str, Any] = {"description": _render_pr_body(candidate, self.problem_id or candidate.problem_id)}
        if state_event:
            data["state_event"] = state_event
        self._api("PUT", f"/projects/{self._project}/merge_requests/{mr_iid}", data=data)

    def _comment(self, mr_iid: str, body: str) -> None:
        self._api("POST", f"/projects/{self._project}/merge_requests/{mr_iid}/notes",
                  data={"body": body})

    def _comment_issue(self, body: str) -> None:
        self._api("POST", f"/projects/{self._project}/issues/{self.problem_id}/notes",
                  data={"body": body})

    def _refresh_issue(self, *, mermaid: str | None = None, report_url: str | None = None) -> None:
        self._ensure_problem()
        leaderboard = self.get_leaderboard()
        current = self._api("GET", f"/projects/{self._project}/issues/{self.problem_id}")
        existing = current.get("description") or ""
        existing_mermaid = mermaid if mermaid is not None else _extract(existing, "<!-- EVOLVE_GRAPH -->", "<!-- /EVOLVE_GRAPH -->")
        body = _render_issue_body(
            self.spec, trait_matrix=leaderboard, mermaid=existing_mermaid or "",
            report_url=report_url,
        )
        self._api("PUT", f"/projects/{self._project}/issues/{self.problem_id}", data={"description": body})

    def _api(self, method: str, path: str, *, data: dict[str, Any] | None = None) -> Any:
        url = f"{self._base}/api/v4{path}"
        body = None
        headers = {"Private-Token": self._token, "Accept": "application/json"}
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = Request(url, method=method, data=body, headers=headers)
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode() or "null")
        except HTTPError as e:
            raise RuntimeError(f"gitlab API {method} {path} failed: {e.code} {e.read().decode(errors='replace')}") from e


def _render_final_body(winner: Candidate, problem_id: str, spec: ProblemSpec) -> str:
    verdict = winner.reviewer_verdict
    parts = [
        f"## Evolution winner: candidate-{winner.candidate_id}",
        f"Closes #{problem_id}",
        "",
        "### Hypothesis",
        winner.hypothesis or "(none)",
        "",
        "### Conclusion",
        winner.conclusion or "(none)",
        "",
        "### Reviewer verdict",
    ]
    if verdict:
        parts += [
            f"- **Verdict:** {verdict.verdict}",
            f"- **Reason:** {verdict.reason}",
            f"- **Confidence:** {verdict.confidence}",
        ]
    parts += [
        "",
        "---",
        f"This MR was opened automatically by agent-evolve. `agents_can_merge` is hardcoded "
        f"`false` — please review and merge manually against `{spec.safety.protected_branch}`.",
    ]
    return "\n".join(parts)


def _extract(text: str, open_marker: str, close_marker: str) -> str | None:
    start = text.find(open_marker)
    if start < 0:
        return None
    end = text.find(close_marker, start + len(open_marker))
    if end < 0:
        return None
    return text[start + len(open_marker): end].strip()
