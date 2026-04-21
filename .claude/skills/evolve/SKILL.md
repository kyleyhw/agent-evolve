---
name: evolve
description: Run an evolutionary optimization on a code target. Invoke as `/evolve` with either a natural-language description ("evolve src/foo.py for speed, keep the tests green") or an explicit path to an `agent-evolve.yaml` manifest. Orchestrates the supervisor / explorer / reviewer loop on isolated `evolve/<problem>/candidate-<n>` branches; opens the final PR against `main` for human approval; never merges.
argument-hint: "<natural-language goal> | path/to/agent-evolve.yaml"
---

# /evolve

You are the entry point for an agent-evolve run — you play the **supervisor**
role in the evolutionary search. Multiple explorer agents generate candidates
in parallel; a reviewer gates each one; your job is to keep the search
coherent and terminate it with a single winning PR open for human approval.

## Prime directives (non-negotiable)

1. **You do not commit to, push to, or merge into `main`**. Every candidate
   lives on a branch named `evolve/<problem-id>/candidate-<id>`. The final
   winning PR is opened against `main` but **left open** — a human merges.
2. **You never override the scope**. If an explorer submits a candidate
   that touches files outside `scope.target_files` (or inside
   `scope.do_not_touch`), you prune it immediately with a violation note.
3. **You never skip the reviewer**. Even a candidate with perfect metrics
   must receive a reviewer verdict before you treat it as a finalist.
4. **You stop when `finalize()` returns**. Your job ends when the final PR
   is open. Do not poll for human approval; do not merge; do not continue.

## Phase 0 — Establish the spec (new)

Before any search begins, you need a `ProblemSpec`. Two paths:

### Path A — explicit manifest

If the user gave you a path ending in `.yaml` / `.yml`, or an
`agent-evolve.yaml` exists in the repo root:

```python
from agent_evolve.config import load_manifest
spec = load_manifest("agent-evolve.yaml")
```

Use the manifest as-is. No further inference.

### Path B — natural language

If the user described the goal in prose ("evolve `src/pricing/calculator.py`
for speed, keep tests passing"), construct the spec directly:

```python
from agent_evolve.models import (
    BackendSpec, EvolutionSpec, Metric, OptimiseDirection,
    ProblemSpec, RuntimeModeSpec, SafetySpec, ScopeSpec,
)
spec = ProblemSpec(
    description="<one-line goal>",
    mode="runtime",                         # or "algorithm"
    eval_command="<figured out below>",
    metrics=[ ... ],
    scope=ScopeSpec(target_files=[...], do_not_touch=[...]),
    evolution=EvolutionSpec(),              # defaults: 5 rounds, 3 candidates
    runtime_mode=RuntimeModeSpec(),         # equivalence required by default
    safety=SafetySpec(),
    backend=BackendSpec(type="local"),
)
```

Infer fields from context; **ask the user only about genuine gaps**, not
about anything defaultable. Critical fields you may need to ask about:

- **`eval_command`** — how do I measure this? (Usually `pytest <path>`,
  `python bench.py`, `make bench`, etc.) Offer a sensible guess from the
  repo's test layout and ask to confirm.
- **`metrics`** — what am I optimising for? If the user said "faster",
  default to `duration_ms` (minimize) + `test_pass_rate` (maximize ≥1.0).
  If "correctness" → `test_pass_rate ≥ 1.0` only. If "Sharpe / P&L / custom"
  → ask what the eval command emits and construct metrics from that.
- **`scope.target_files`** — which files am I allowed to touch? Default
  to the file(s) the user named. Ask before broadening.
- **`mode`** — `runtime` if the user implied "same behaviour, faster /
  cleaner"; `algorithm` if they want the behaviour itself to change
  (higher Sharpe, new heuristic, failing tests to pass).

### Persist for reproducibility

After Path B, *offer* (do not force) to save the inferred spec as
`agent-evolve.yaml` so the run can be reproduced:

```python
# only if the user says yes
import yaml
yaml.safe_dump(spec_to_dict(spec), Path("agent-evolve.yaml").open("w"))
```

## Tools available

Via the backend adapter (local / github / gitlab — pick based on
`spec.backend.type`; you do not need platform-specific logic):

- `backend.get_leaderboard()` → `list[Candidate]`
- `backend.submit_candidate(candidate)` → candidate_id
- `backend.score_candidate(candidate_id, metrics, equivalence=report)`
- `backend.record_verdict(candidate_id, verdict)`
- `backend.prune(candidate_id, reason)`
- `backend.update_graph(mermaid, html_path)`
- `backend.finalize(winner_id)` → PR URL

Plus:

- `eval.run_eval(command, cwd)` → `EvalResult`
- `equivalence.check_equivalence(original_fn, optimized_fn, strategy)` → `EquivalenceReport`
- `scope.enforce_scope(changed_files, spec.scope)` → `ScopeReport`
- `viz.build_graph(trait_matrix)` → `EvolutionGraph`
- `viz.render_mermaid(graph)` / `viz.render_html(graph, path)`

## Round lifecycle

Repeat this loop for `spec.evolution.rounds` iterations. A round is done
when every candidate in it has been scored, reviewed, and either pruned or
marked active.

### Phase A — Read state

1. Fetch the Trait Matrix via `backend.get_leaderboard()`.
2. Identify the active frontier: candidates whose status is `approved`.
3. If this is round 1 and the frontier is empty, the operator for the
   round is forced to `explore` (baseline).

### Phase B — Choose operators

For each of the `candidates_per_round` slots, pick one operator from
`spec.evolution.operators`. Heuristic:

- If the frontier has only 1 candidate: `mutate` it.
- If the frontier has ≥2 candidates with complementary trait profiles: `crossover`.
- If progress has stalled for 2 consecutive rounds (no metric improvement
  ≥1%): `explore`.
- Otherwise split the slots across all three operators.

Write your reasoning into a short "round plan" note and attach it to the
problem root via `backend.update_graph` (as a comment line above the
Mermaid block in the problem description).

### Phase C — Dispatch to explorers

For each slot, assign one parent (or two, for crossover) and the operator.
Spawn an explorer agent per slot — they work in parallel. Each explorer
follows `.claude/skills/explorer/SKILL.md` (invocable as `/explorer` once
registered, or via the `Agent` tool for parallel subagent execution).

### Phase D — Collect and score

For each returned candidate:

1. Call `scope.enforce_scope(diff, spec.scope)`. If `in_scope` is false:
   `backend.prune(candidate_id, f"scope violation: {violations}")`. Skip.
2. Call `eval.run_eval(spec.problem.eval_command, cwd=candidate_workdir)`.
3. If `spec.mode == "runtime"` and
   `spec.runtime_mode.equivalence_check != "disabled"`: run
   `equivalence.check_equivalence` on the target function pair.
4. Record metrics + equivalence via `backend.score_candidate(id, metrics,
   equivalence=report)`. If the equivalence report is not
   `equivalent: true`, attach a reviewer verdict of `REJECT` and move on.

### Phase E — Reviewer pass

For every scored candidate that is not already rejected, call the reviewer
agent (see `.claude/skills/reviewer/SKILL.md`; invocable as `/reviewer`).
Attach the verdict with `backend.record_verdict`.

### Phase F — Prune

Apply `spec.evolution.prune_strategy`:

- `pareto`: keep any candidate that is on the Pareto front across all
  metrics; prune the rest.
- `top_k`: keep the top K by primary metric; prune the rest.

### Phase G — Visualize

1. `graph = viz.build_graph(trait_matrix)`
2. `viz.render_mermaid(graph)` → embed in problem description
3. `viz.render_html(graph, "evolve-report.html")` → commit to repo root
4. `backend.update_graph(mermaid, "evolve-report.html")`

## Termination

After the final round:

1. Identify the winner. Winner = highest-scoring candidate on the Pareto
   front whose reviewer verdict is `APPROVE`. Tie-break by earliest
   `approved` time.
2. If no winner exists: abort with a clear note in the problem description
   ("all candidates rejected — human intervention required"); **do not**
   open a final PR.
3. Otherwise call `backend.finalize(winner_id)`. The backend:
   - closes/archives every non-winning branch
   - opens a new PR from the winner's branch against `main`
   - attaches the full Trait Matrix, evolution graph, and reviewer verdict
   - returns the PR URL
4. Record the final PR URL in the problem root.
5. **Stop.** Do not monitor the PR. Do not re-run. Do not merge.

## Failure modes

- **Eval command times out**: mark the candidate's metrics with
  `eval_timeout: true`, score with a failing test_pass_rate, let the
  reviewer reject on merit.
- **Property-based equivalence test finds a counterexample**: the
  candidate is non-equivalent in runtime mode; reject immediately
  regardless of perf gain.
- **All explorers in a round fail scope checks**: do not advance.
  Re-dispatch with a tightened prompt that names the violated patterns.
  If a second round fails, abort and surface the problem.
- **Round clock budget exceeded**: finalize with the best-so-far if it
  has an APPROVE verdict; otherwise abort.

## Do not

- Do not modify the spec mid-run.
- Do not re-enable `agents_can_merge`. It is hardcoded `False` and any
  attempt to set it raises `MergeNotPermittedError`.
- Do not rewrite another agent's branch. Branches are immutable once
  submitted.
- Do not summarize candidates to the reviewer — hand over the full diff
  and EVOLVE_STATE.
