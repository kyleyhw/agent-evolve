"""End-to-end demo of agent-evolve against a toy target.

Plays the supervisor / explorer / reviewer roles manually with hardcoded
candidate implementations — naive recursive Fibonacci → memoised → buggy
forward-loop → correct iterative. Exercises the real eval runner, scope
enforcer, equivalence checker, visualiser, and LocalBackend end-to-end.

Run from the repo root:

    uv run python examples/demo_run.py

Leaves an ``evolve-state/`` tree in a temp dir and an interactive report at
``examples/demo-report.html``.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

from hypothesis.strategies import integers, tuples

from agent_evolve.backends import LocalBackend
from agent_evolve.eval import check_equivalence, run_eval
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
from agent_evolve.scope import enforce_scope
from agent_evolve.viz import build_graph, render_html, render_mermaid


VARIANTS: dict[str, str] = {
    "baseline": textwrap.dedent(
        """
        def fib(n):
            if n < 2:
                return n
            return fib(n - 1) + fib(n - 2)
        """
    ).strip(),
    "memoised": textwrap.dedent(
        """
        from functools import lru_cache

        @lru_cache(maxsize=None)
        def fib(n):
            if n < 2:
                return n
            return fib(n - 1) + fib(n - 2)
        """
    ).strip(),
    "buggy_loop": textwrap.dedent(
        """
        def fib(n):
            a, b = 0, 1
            for _ in range(n - 1):   # off-by-one: should be range(n)
                a, b = b, a + b
            return b
        """
    ).strip(),
    "iterative": textwrap.dedent(
        """
        def fib(n):
            a, b = 0, 1
            for _ in range(n):
                a, b = b, a + b
            return a
        """
    ).strip(),
}

BENCH = textwrap.dedent(
    """
    import json, sys, time
    sys.path.insert(0, '.')
    from fib import fib

    for _ in range(3):
        fib(15)
    t0 = time.perf_counter_ns()
    for _ in range(100):
        fib(22)
    us = (time.perf_counter_ns() - t0) / 100 / 1000

    expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55]
    ok = all(fib(i) == expected[i] for i in range(len(expected)))
    print(json.dumps({
        "duration_us": round(us, 2),
        "test_pass_rate": 1.0 if ok else 0.0,
    }))
    """
).strip()


def _load_fn(code: str):
    """Instantiate the candidate's fib function from its source code."""
    ns: dict = {}
    exec(code, ns)
    return ns["fib"]


def _build_spec(workdir: Path) -> ProblemSpec:
    return ProblemSpec(
        description="Optimise fib(n) while preserving correctness",
        mode="runtime",
        eval_command=f'"{sys.executable}" bench.py',
        metrics=[
            Metric(name="duration_us", optimise=OptimiseDirection.MINIMIZE),
            Metric(
                name="test_pass_rate",
                optimise=OptimiseDirection.MAXIMIZE,
                minimum=1.0,
            ),
        ],
        scope=ScopeSpec(target_files=["fib.py"], do_not_touch=["bench.py"]),
        evolution=EvolutionSpec(rounds=3, candidates_per_round=2),
        runtime_mode=RuntimeModeSpec(property_test_samples=40),
        safety=SafetySpec(final_pr_reviewers=["kyleyhw"]),
        backend=BackendSpec(type="local", root_dir=str(workdir / "evolve-state")),
    )


def _review(candidate: Candidate, baseline_us: float) -> ReviewerVerdict:
    """Stand-in reviewer — in a real run this is the reviewer agent + SKILL.md."""
    metrics = candidate.metrics
    eq = candidate.equivalence_report
    equiv_ok = bool(eq and eq.equivalent)
    pass_ok = metrics.get("test_pass_rate", 0.0) >= 1.0
    duration = metrics.get("duration_us", float("inf"))
    faster = duration < baseline_us
    real_gain = faster and (baseline_us - duration) / baseline_us >= 0.05

    checklist = {
        "scope_compliant": True,
        "hypothesis_coherent": bool(candidate.hypothesis),
        "metrics_improved": faster,
        "equivalence_passed": equiv_ok,
        "perf_gain_real": real_gain,
    }

    if not pass_ok or not equiv_ok:
        reason = (
            f"Not logic-equivalent to baseline — {eq.mismatch}"
            if eq and not eq.equivalent and eq.mismatch
            else "Failed correctness check in eval."
        )
        return ReviewerVerdict(
            verdict="REJECT", reason=reason, checklist=checklist, confidence="high"
        )

    if faster:
        return ReviewerVerdict(
            verdict="APPROVE",
            reason=f"{duration:.1f}µs vs baseline {baseline_us:.1f}µs.",
            checklist=checklist,
            confidence="high",
        )
    return ReviewerVerdict(
        verdict="REQUEST_CHANGES",
        reason="Equivalent to baseline but no meaningful speedup.",
        checklist=checklist,
        confidence="medium",
    )


PLAN = [
    # (candidate_id, operator,   parents,    round, variant,       hypothesis)
    ("1", "explore",   [],         1, "baseline",
        "Reference implementation — naive recursion."),
    ("2", "mutate",    ["1"],      2, "memoised",
        "Add @lru_cache — trades a tiny amount of memory for never recomputing the same fib(k) twice."),
    ("3", "mutate",    ["1"],      2, "buggy_loop",
        "Replace recursion with a forward loop for O(n) time."),
    ("4", "crossover", ["2", "3"], 3, "iterative",
        "Combine memoised's 'never recompute' insight with the forward-loop idea from #3, fixing the off-by-one."),
]


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="agent-evolve-demo-"))
    (workdir / "bench.py").write_text(BENCH, encoding="utf-8")
    print(f"[demo] workdir: {workdir}")

    spec = _build_spec(workdir)
    backend = LocalBackend(spec, root=workdir / "evolve-state")
    problem_id = backend.create_problem(spec)

    baseline_fn = _load_fn(VARIANTS["baseline"])
    baseline_us: float | None = None

    for cid, op, parents, rnd, variant, hypothesis in PLAN:
        print(f"\n[demo] round {rnd} · candidate {cid} ({op} → {variant})")
        (workdir / "fib.py").write_text(VARIANTS[variant], encoding="utf-8")

        c = Candidate(
            problem_id=problem_id,
            candidate_id=cid,
            operator=op,
            round=rnd,
            parent_ids=parents,
            hypothesis=hypothesis,
        )
        backend.submit_candidate(c)

        scope_report = enforce_scope(["fib.py"], spec.scope)
        assert scope_report.in_scope, scope_report.violations

        result = run_eval(spec.eval_command, cwd=workdir, timeout=30)
        if not result.passed:
            print(f"[demo]   eval exited nonzero: {result.stderr.strip()[:200]}")

        eq = check_equivalence(
            baseline_fn,
            _load_fn(VARIANTS[variant]),
            tuples(integers(min_value=0, max_value=20)),
            samples=40,
        )
        backend.score_candidate(cid, result.metrics, equivalence=eq)

        if cid == "1":
            baseline_us = result.metrics.get("duration_us", 0.0)

        scored = next(x for x in backend.get_leaderboard() if x.candidate_id == cid)
        verdict = _review(scored, baseline_us or 0.0)
        backend.record_verdict(cid, verdict)

        print(f"[demo]   metrics:    {result.metrics}")
        ce = f" (counterexample {eq.counterexample[0]})" if not eq.equivalent and eq.counterexample else ""
        print(f"[demo]   equivalent: {eq.equivalent}{ce}")
        print(f"[demo]   verdict:    {verdict.verdict} — {verdict.reason}")

    board = backend.get_leaderboard()
    graph = build_graph(board, problem_id=problem_id, title="Demo — fib(n) optimisation")
    report_path = Path("examples/demo-report.html").resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    render_html(graph, report_path)
    backend.update_graph(render_mermaid(graph), str(report_path))

    approved = [c for c in board if c.reviewer_verdict and c.reviewer_verdict.verdict == "APPROVE"]
    if not approved:
        print("\n[demo] no approved candidate — supervisor aborts without a final PR")
        return 1

    winner = min(approved, key=lambda c: c.metrics.get("duration_us", float("inf")))
    for other in board:
        if other.candidate_id != winner.candidate_id and other.status not in {"pruned", "rejected"}:
            backend.prune(other.candidate_id, reason=f"not selected — winner is #{winner.candidate_id}")
    pr_path = backend.finalize(winner.candidate_id)

    print("\n--- Leaderboard ---")
    for c in sorted(board, key=lambda c: int(c.candidate_id)):
        v = c.reviewer_verdict.verdict if c.reviewer_verdict else "—"
        duration = c.metrics.get("duration_us", 0.0)
        print(f"  #{c.candidate_id:<2} R{c.round} {c.operator:9s} {duration:8.2f}µs   {v}")
    print(f"\n[demo] winner:          #{winner.candidate_id} ({winner.operator}, {winner.metrics['duration_us']:.1f}µs)")
    print(f"[demo] final PR file:   {pr_path}")
    print(f"[demo] HTML report:     {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
