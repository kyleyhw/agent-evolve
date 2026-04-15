"""GitHub backend — Issues as problem root + leaderboard, PRs as candidates.

This mirrors the [`gh-evolve`](https://github.com/kaiwong-sapiens/gh-evolve)
convention: the problem lives in an Issue whose body contains the Trait Matrix
and the Mermaid graph; each candidate is a draft PR whose body embeds the
``EVOLVE_STATE`` JSON in a hidden ``<details>`` block.

Safety
------
``create_problem`` installs a branch protection rule on the protected branch
(``main`` by default). ``finalize`` opens the final PR but **never** merges.
Both invariants are enforced in code, not just via GitHub settings.

Authentication
--------------
Expects a token with ``repo`` scope via one of (in order):

* ``github_token`` argument to the constructor
* ``GH_TOKEN`` environment variable
* ``GITHUB_TOKEN`` environment variable

If none are present, :class:`GitHubBackend` raises immediately — it will not
attempt unauthenticated calls that silently rate-limit.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any

from agent_evolve.backends.base import EvolveBackend, MergeNotPermittedError
from agent_evolve.models import Candidate, ProblemSpec, ReviewerVerdict

if TYPE_CHECKING:  # pragma: no cover
    from github import Github
    from github.Issue import Issue
    from github.PullRequest import PullRequest
    from github.Repository import Repository


EVOLVE_STATE_OPEN = "<!-- EVOLVE_STATE:"
EVOLVE_STATE_CLOSE = "-->"
TRAIT_MATRIX_OPEN = "<!-- TRAIT_MATRIX -->"
TRAIT_MATRIX_CLOSE = "<!-- /TRAIT_MATRIX -->"
GRAPH_OPEN = "<!-- EVOLVE_GRAPH -->"
GRAPH_CLOSE = "<!-- /EVOLVE_GRAPH -->"


class GitHubBackend(EvolveBackend):
    """GitHub implementation of :class:`EvolveBackend`.

    The supervisor treats problem ids as issue numbers; candidate ids are the
    PR numbers assigned by GitHub. This lets a human reading the Issue
    understand the full state without running any tooling.
    """

    def __init__(self, spec: ProblemSpec, *, github_token: str | None = None) -> None:
        super().__init__(spec)
        if not spec.backend.repo:
            raise ValueError("github backend requires backend.repo (e.g. 'owner/name')")
        self.repo_slug = spec.backend.repo

        token = github_token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise RuntimeError(
                "no GitHub token found — set GH_TOKEN or pass github_token=... explicitly"
            )

        from github import Auth, Github

        self._gh: Github = Github(auth=Auth.Token(token))
        self._repo: Repository = self._gh.get_repo(self.repo_slug)
        self._issue: Issue | None = None
        self.problem_id: str | None = None

    def create_problem(self, spec: ProblemSpec) -> str:
        body = _render_issue_body(spec, trait_matrix=[], mermaid="", report_url=None)
        issue = self._repo.create_issue(
            title=f"[Evolve] {spec.description}",
            body=body,
            labels=["evolve"],
        )
        self._issue = issue
        self.problem_id = str(issue.number)
        self._install_branch_protection(spec.safety.protected_branch)
        return self.problem_id

    def submit_candidate(self, candidate: Candidate) -> str:
        self._ensure_issue()
        branch = candidate.branch_name()
        head = candidate.commit_sha or branch

        pr = self._repo.create_pull(
            title=f"[Evolve #{self.problem_id}] candidate-{candidate.candidate_id}: "
            f"{candidate.operator}",
            body=_render_pr_body(candidate, self.problem_id),
            head=head,
            base=self.spec.safety.protected_branch,
            draft=True,
        )
        candidate_id = str(pr.number)
        candidate.candidate_id = candidate_id
        pr.edit(body=_render_pr_body(candidate, self.problem_id))
        pr.add_to_labels("evolve-candidate")
        self._refresh_issue_body()
        return candidate_id

    def score_candidate(self, candidate_id: str, metrics: dict[str, float]) -> None:
        pr = self._pr(candidate_id)
        candidate = _parse_candidate(pr.body or "")
        if candidate is None:
            raise RuntimeError(f"PR #{candidate_id} missing EVOLVE_STATE block")
        candidate.metrics.update(metrics)
        candidate.status = "scored"
        pr.edit(body=_render_pr_body(candidate, self.problem_id or candidate.problem_id))
        self._refresh_issue_body()

    def record_verdict(self, candidate_id: str, verdict: ReviewerVerdict) -> None:
        pr = self._pr(candidate_id)
        candidate = _parse_candidate(pr.body or "")
        if candidate is None:
            raise RuntimeError(f"PR #{candidate_id} missing EVOLVE_STATE block")
        candidate.reviewer_verdict = verdict
        candidate.status = "approved" if verdict.verdict == "APPROVE" else "rejected"
        pr.edit(body=_render_pr_body(candidate, self.problem_id or candidate.problem_id))
        pr.create_issue_comment(_render_verdict_comment(verdict))
        self._refresh_issue_body()

    def get_leaderboard(self) -> list[Candidate]:
        self._ensure_issue()
        out: list[Candidate] = []
        for pr in self._repo.get_pulls(state="all"):
            for label in pr.labels:
                if label.name == "evolve-candidate":
                    candidate = _parse_candidate(pr.body or "")
                    if candidate is not None and candidate.problem_id == self.problem_id:
                        out.append(candidate)
                    break
        return out

    def prune(self, candidate_id: str, reason: str) -> None:
        pr = self._pr(candidate_id)
        candidate = _parse_candidate(pr.body or "")
        if candidate is None:
            raise RuntimeError(f"PR #{candidate_id} missing EVOLVE_STATE block")
        candidate.status = "pruned"
        candidate.conclusion = (
            f"{candidate.conclusion}\n\nPruned: {reason}" if candidate.conclusion
            else f"Pruned: {reason}"
        )
        pr.edit(body=_render_pr_body(candidate, self.problem_id or candidate.problem_id),
                state="closed")
        pr.create_issue_comment(f"Pruned: {reason}")
        self._refresh_issue_body()

    def update_graph(self, mermaid: str, html_path: str | None = None) -> None:
        self._ensure_issue()
        self._refresh_issue_body(mermaid=mermaid, report_url=html_path)

    def finalize(self, winner_id: str) -> str:
        self.assert_no_merge("finalize")
        self._ensure_issue()

        winner_pr = self._pr(winner_id)
        winner = _parse_candidate(winner_pr.body or "")
        if winner is None:
            raise RuntimeError(f"winner PR #{winner_id} missing EVOLVE_STATE block")
        if winner.reviewer_verdict is None or winner.reviewer_verdict.verdict != "APPROVE":
            raise ValueError(
                f"cannot finalize on PR #{winner_id}: reviewer verdict is "
                f"{winner.reviewer_verdict.verdict if winner.reviewer_verdict else 'missing'}"
            )

        for other in self.get_leaderboard():
            if other.candidate_id == winner_id:
                continue
            if other.status not in ("pruned", "rejected"):
                self.prune(other.candidate_id, reason=f"not selected — winner is PR #{winner_id}")

        final_body = _render_final_pr_body(winner, self._issue, self.spec)
        final = self._repo.create_pull(
            title=f"[Evolve #{self.problem_id}] winner: candidate-{winner_id} "
            f"({winner.operator})",
            body=final_body,
            head=winner.branch_name(),
            base=self.spec.safety.protected_branch,
            draft=False,
        )
        final.add_to_labels("evolve-winner")
        if self.spec.safety.final_pr_reviewers:
            try:
                final.create_review_request(reviewers=list(self.spec.safety.final_pr_reviewers))
            except Exception:
                final.create_issue_comment(
                    f"Reviewers requested: {', '.join(self.spec.safety.final_pr_reviewers)}"
                )

        if self._issue is not None:
            self._issue.create_comment(
                f"Finalised. Winner is #{winner_id}. Awaiting human approval on #{final.number}."
            )

        return final.html_url

    def _ensure_issue(self) -> None:
        if self._issue is None:
            if self.problem_id is None:
                raise RuntimeError("no active problem — call create_problem() first")
            self._issue = self._repo.get_issue(int(self.problem_id))

    def _pr(self, candidate_id: str) -> PullRequest:
        return self._repo.get_pull(int(candidate_id))

    def _refresh_issue_body(self, *, mermaid: str | None = None, report_url: str | None = None) -> None:
        self._ensure_issue()
        leaderboard = self.get_leaderboard()
        current = self._issue.body or ""
        current_mermaid = mermaid if mermaid is not None else _extract_block(current, GRAPH_OPEN, GRAPH_CLOSE)
        current_report = report_url if report_url is not None else _extract_inline(current, "Report:")
        new_body = _render_issue_body(
            self.spec, trait_matrix=leaderboard, mermaid=current_mermaid or "",
            report_url=current_report,
        )
        self._issue.edit(body=new_body)

    def _install_branch_protection(self, branch: str) -> None:
        try:
            ref = self._repo.get_branch(branch)
        except Exception:
            return
        try:
            ref.edit_protection(
                strict=True,
                require_code_owner_reviews=False,
                required_approving_review_count=1,
                allow_force_pushes=False,
                allow_deletions=False,
            )
        except Exception as e:  # pragma: no cover — requires admin rights
            if self.agents_can_merge:
                raise MergeNotPermittedError("branch protection unreachable and agents_can_merge=True") from e


def _render_issue_body(
    spec: ProblemSpec, *, trait_matrix: list[Candidate], mermaid: str, report_url: str | None
) -> str:
    lines = [
        f"## Objective\n{spec.description}\n",
        f"## Evaluate\n`{spec.eval_command}`\n",
        "## Constraints",
        *[
            f"- **{m.name}** ({m.optimise.value})"
            + (f" minimum={m.minimum}" if m.minimum is not None else "")
            + (f" maximum={m.maximum}" if m.maximum is not None else "")
            for m in spec.metrics
        ],
        "",
        "## Trait Matrix",
        TRAIT_MATRIX_OPEN,
        _render_trait_matrix(trait_matrix, spec),
        TRAIT_MATRIX_CLOSE,
        "",
        "## Search Graph",
        GRAPH_OPEN,
        mermaid or "_(no candidates yet)_",
        GRAPH_CLOSE,
    ]
    if report_url:
        lines.append(f"\nReport: {report_url}")
    lines.append(
        "\n---\n`agents_can_merge: false` — the winner opens a PR against "
        f"`{spec.safety.protected_branch}` but is never merged by an agent.\n"
    )
    return "\n".join(lines)


def _render_trait_matrix(candidates: list[Candidate], spec: ProblemSpec) -> str:
    if not candidates:
        return "_(no candidates yet)_"
    metric_names = [m.name for m in spec.metrics]
    header = ["ID", "Parent", "Operator", "Round", *metric_names, "Status"]
    sep = ["----"] * len(header)
    rows = [
        [
            f"#{c.candidate_id}",
            ",".join(c.parent_ids) or "—",
            c.operator,
            str(c.round),
            *[_fmt(c.metrics.get(name)) for name in metric_names],
            c.status,
        ]
        for c in sorted(candidates, key=lambda x: (x.round, x.candidate_id))
    ]
    return "\n".join(["| " + " | ".join(r) + " |" for r in [header, sep, *rows]])


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3g}"
    return str(v)


def _render_pr_body(candidate: Candidate, problem_id: str) -> str:
    state_json = json.dumps(candidate.to_dict(), indent=2)
    parts = [
        f"## Parent(s)\n{', '.join(f'#{p}' for p in candidate.parent_ids) or '(baseline)'}",
        f"## Strategy\n{candidate.operator}",
        f"## Hypothesis\n{candidate.hypothesis or '(none)'}",
        f"## Conclusion\n{candidate.conclusion or '(pending eval)'}",
        "## Metrics",
        "```json",
        json.dumps(candidate.metrics, indent=2),
        "```",
        "",
        f"{EVOLVE_STATE_OPEN}\n{state_json}\n{EVOLVE_STATE_CLOSE}",
    ]
    return "\n\n".join(parts)


def _render_verdict_comment(verdict: ReviewerVerdict) -> str:
    checklist_lines = "\n".join(
        f"- [{'x' if v else ' '}] {k}" for k, v in verdict.checklist.items()
    )
    return (
        f"**VERDICT: {verdict.verdict}** (confidence: {verdict.confidence})\n\n"
        f"{verdict.reason}\n\n"
        f"### Checklist\n{checklist_lines}"
    )


def _render_final_pr_body(winner: Candidate, issue: Issue | None, spec: ProblemSpec) -> str:
    verdict = winner.reviewer_verdict
    parts = [
        f"## Evolution winner: candidate-{winner.candidate_id}",
        f"Closes #{issue.number}" if issue else "",
        "",
        "### Hypothesis",
        winner.hypothesis or "(none)",
        "",
        "### Conclusion",
        winner.conclusion or "(none)",
        "",
        "### Reviewer verdict",
        f"- **Verdict:** {verdict.verdict}" if verdict else "- (no verdict)",
    ]
    if verdict:
        parts += [
            f"- **Reason:** {verdict.reason}",
            f"- **Confidence:** {verdict.confidence}",
        ]
    parts += [
        "",
        "### Trait Matrix",
        f"See #{issue.number}." if issue else "(no issue link)",
        "",
        "---",
        f"This PR was opened automatically by agent-evolve. `agents_can_merge` is hardcoded `false` — "
        f"please review and merge manually against `{spec.safety.protected_branch}`.",
    ]
    return "\n".join(p for p in parts if p is not None)


def _parse_candidate(body: str) -> Candidate | None:
    raw = _extract_block(body, EVOLVE_STATE_OPEN, EVOLVE_STATE_CLOSE)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return Candidate.from_dict(data)


def _extract_block(text: str, open_marker: str, close_marker: str) -> str | None:
    m = re.search(re.escape(open_marker) + r"(.*?)" + re.escape(close_marker), text, re.DOTALL)
    if not m:
        return None
    return m.group(1).strip()


def _extract_inline(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None
