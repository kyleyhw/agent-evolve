"""End-to-end demo of agent-evolve against a toy target.

Plays the supervisor / explorer / reviewer roles manually with hardcoded
candidate implementations against a toy ``fib(n)`` optimisation target.
The candidates are deliberately chosen to walk the reader through every
rejection path the real loop would surface:

* a buggy forward-loop variant (return-value divergence)
* a "raise on n<2" variant (exception-type divergence)
* a "scope violator" that pretends to touch ``bench.py`` (pruned by the
  scope enforcer before the reviewer is ever invoked)
* two *equivalent* survivors at different ``(duration_us, code_lines)``
  trade-offs, so the Pareto front is non-trivial

Exercises the real eval runner, scope enforcer, equivalence checker,
visualiser, and ``LocalBackend`` end-to-end. The reviewer agent and the
explorer agents are stubbed in this script — that is all the demo
simulates by hand; everything else is the production code path.

Run from the repo root::

    uv run python examples/demo_run.py

Leaves an ``evolve-state/`` tree in a temp dir and an interactive report
at ``examples/demo-report.html``.
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
    AgentsSpec,
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


# Force UTF-8 on stdout so the unicode arrows / Greek mu render under cp1252
# (the Windows default codepage).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


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
    "raises_on_small": textwrap.dedent(
        """
        # Plausible-looking O(n) loop, but raises on n<2 instead of returning n.
        # The equivalence checker catches this via exception-type divergence:
        # the baseline returns 0 / 1 for n in {0, 1}; this one raises ValueError.
        def fib(n):
            if n < 2:
                raise ValueError("n must be >= 2")
            a, b = 0, 1
            for _ in range(n):
                a, b = b, a + b
            return a
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

    # code_lines metric: count non-blank source lines in fib.py.
    # Used to give the Pareto front a non-trivial second axis — two
    # equivalent candidates with different line counts both survive
    # pruning instead of one dominating.
    with open('fib.py', encoding='utf-8') as f:
        code_lines = sum(1 for line in f if line.strip())

    # Wrap the import + correctness probe so a candidate that crashes on
    # any expected input still produces a metrics blob (with
    # test_pass_rate=0.0). Without this the bench would crash before
    # printing JSON and the supervisor would see no metrics at all.
    try:
        from fib import fib
        for _ in range(3):
            fib(15)
        t0 = time.perf_counter_ns()
        for _ in range(100):
            fib(22)
        us = (time.perf_counter_ns() - t0) / 100 / 1000

        expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55]
        ok = all(fib(i) == expected[i] for i in range(len(expected)))
    except Exception:
        ok = False
        us = float('inf')

    print(json.dumps({
        "duration_us": round(us, 2) if us != float('inf') else -1.0,
        "test_pass_rate": 1.0 if ok else 0.0,
        "code_lines": code_lines,
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
            # Soft secondary metric. With two equivalent candidates that
            # tie on speed (within noise), the one with fewer source lines
            # is preferred — and both stay on the Pareto front when they
            # trade off speed against compactness.
            Metric(name="code_lines", optimise=OptimiseDirection.MINIMIZE),
        ],
        scope=ScopeSpec(target_files=["fib.py"], do_not_touch=["bench.py"]),
        evolution=EvolutionSpec(rounds=3, candidates_per_round=2),
        runtime_mode=RuntimeModeSpec(property_test_samples=40),
        safety=SafetySpec(final_pr_reviewers=["kyleyhw"]),
        backend=BackendSpec(type="local", root_dir=str(workdir / "evolve-state")),
        # Multi-model dispatch: in this demo all roles are still played by
        # the in-script stand-ins, but the spec carries a non-default
        # explorer ensemble + reviewer assignment so the printed agent
        # dispatch lines show what a real run would do — explorer slots
        # round-robin between Claude and Gemini, reviewer is Gemini.
        agents=AgentsSpec(
            supervisor="claude",
            explorer=["claude", "gemini"],
            reviewer="gemini",
        ),
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
    # (candidate_id, operator,   parents,    round, variant,         touches,                hypothesis)
    ("1", "explore",   [],         1, "baseline",        ["fib.py"],
        "Reference implementation — naive recursion."),
    ("2", "mutate",    ["1"],      2, "memoised",        ["fib.py"],
        "Add @lru_cache — trades a tiny amount of memory for never recomputing the same fib(k) twice."),
    ("3", "mutate",    ["1"],      2, "buggy_loop",      ["fib.py"],
        "Replace recursion with a forward loop for O(n) time."),
    ("4", "explore",   [],         3, "raises_on_small", ["fib.py"],
        "Different attack: iterative loop with explicit precondition n>=2 to avoid the n<2 special case."),
    # Scope-violator: the candidate's *code* is fine (iterative), but the
    # explorer also poked at bench.py — which is in `do_not_touch`. The
    # supervisor prunes before the reviewer is even invoked.
    ("5", "mutate",    ["2"],      3, "iterative",       ["fib.py", "bench.py"],
        "Iterative rewrite plus a tweak to bench.py to widen the warmup loop — should make timing measurements more stable."),
    ("6", "crossover", ["2", "3"], 3, "iterative",       ["fib.py"],
        "Combine memoised's 'never recompute' insight with the forward-loop idea from #3, fixing the off-by-one."),
]


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="agent-evolve-demo-"))
    (workdir / "bench.py").write_text(BENCH, encoding="utf-8")
    print(f"[demo] workdir: {workdir}")

    spec = _build_spec(workdir)
    backend = LocalBackend(spec, root=workdir / "evolve-state")
    problem_id = backend.create_problem(spec)

    print(
        f"[demo] agents:  supervisor={spec.agents.supervisor}  "
        f"explorer={spec.agents.explorer}  reviewer={spec.agents.reviewer}"
    )

    baseline_fn = _load_fn(VARIANTS["baseline"])
    baseline_us: float | None = None

    # Round-robin counter for explorer ensemble dispatch. The supervisor
    # SKILL applies the same modular indexing — ``ensemble[slot_index %
    # len(ensemble)]`` — so the demo's printed assignment matches a real
    # run's bookkeeping.
    explorer_ensemble = spec.agents.explorer_list()
    slot_index = 0

    for cid, op, parents, rnd, variant, touches, hypothesis in PLAN:
        explorer_for_slot = explorer_ensemble[slot_index % len(explorer_ensemble)]
        slot_index += 1
        print(f"\n[demo] round {rnd} · candidate {cid} ({op} → {variant})")
        print(f"[demo]   explorer dispatch: {explorer_for_slot} (demo stand-in invoked)")
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

        # Scope check first — violations are pruned immediately, no eval
        # or reviewer dispatch. This is the same guard the real supervisor
        # applies in Phase D before scoring.
        scope_report = enforce_scope(touches, spec.scope)
        if not scope_report.in_scope:
            reason = "; ".join(scope_report.violations)
            backend.prune(cid, reason=f"scope violation: {reason}")
            print(f"[demo]   touches:    {touches}")
            print(f"[demo]   PRUNED (pre-review): {reason}")
            continue

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
        # In a real run the reviewer dispatch path would be:
        #   spec.agents.reviewer == "claude" -> in-session reviewer SKILL
        #   else                             -> Bash("<cli> -p '<prompt>'") then parse VERDICT block
        # The demo plays the reviewer in-process; the print line below
        # records what a real run would have done.
        print(f"[demo]   reviewer dispatch: {spec.agents.reviewer} (demo stand-in invoked)")
        verdict = _review(scored, baseline_us or 0.0)
        backend.record_verdict(cid, verdict)

        print(f"[demo]   metrics:    {result.metrics}")
        ce = f" (counterexample {eq.counterexample[0]})" if not eq.equivalent and eq.counterexample else ""
        print(f"[demo]   equivalent: {eq.equivalent}{ce}")
        if not eq.equivalent and eq.mismatch:
            print(f"[demo]   mismatch:   {eq.mismatch}")
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

    # Pareto-aware tie-break: among approved candidates, prefer fewer
    # source lines on a tie of (duration_us). This makes the code_lines
    # metric load-bearing for the demo: two equivalent fast candidates
    # surface a real choice rather than an arbitrary one.
    winner = min(
        approved,
        key=lambda c: (
            c.metrics.get("duration_us", float("inf")),
            c.metrics.get("code_lines", float("inf")),
        ),
    )
    for other in board:
        if other.candidate_id != winner.candidate_id and other.status not in {"pruned", "rejected"}:
            backend.prune(other.candidate_id, reason=f"not selected — winner is #{winner.candidate_id}")
    pr_path = backend.finalize(winner.candidate_id)

    print("\n--- Leaderboard ---")
    for c in sorted(board, key=lambda c: int(c.candidate_id)):
        v = c.reviewer_verdict.verdict if c.reviewer_verdict else c.status.upper()
        duration = c.metrics.get("duration_us", 0.0)
        lines = int(c.metrics.get("code_lines", 0))
        print(f"  #{c.candidate_id:<2} R{c.round} {c.operator:9s} {duration:9.2f}µs  {lines:3d}L   {v}")
    print(
        f"\n[demo] winner:          #{winner.candidate_id} "
        f"({winner.operator}, {winner.metrics.get('duration_us', 0.0):.1f}µs, "
        f"{int(winner.metrics.get('code_lines', 0))}L)"
    )
    print(f"[demo] final PR file:   {pr_path}")
    print(f"[demo] HTML report:     {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
