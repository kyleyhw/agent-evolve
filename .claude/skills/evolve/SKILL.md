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
5. **You never steer the measurement**. Eval numbers are recorded as they
   come out — never re-run an eval to shop for a better number (re-runs
   exist to quantify noise; record every repeat). A run whose honest
   answer is "nothing beat the baseline" ends at the no-winner abort path
   in Termination — a valid outcome, not a failure to paper over.
6. **Every candidate gets its own worktree**. Before an explorer is
   dispatched, create a dedicated working tree for its branch
   (`agent_evolve.worktree.create_worktree`), anchored at the run's
   fixed base SHA (the protected branch's tip at Phase 0b; the seed
   commit in sibling mode). Never point two explorers at one tree, and never let an
   explorer work in the user's checkout: N agents checking out N branches
   in a shared directory silently overwrite each other's files, and every
   measurement downstream of that is garbage. `create_worktree` raises
   when isolation cannot be established — that is a stop, not a
   fall-back-to-shared-directory.

## Preflight — environment (do this before anything else)

The supervisor imports `agent_evolve`; the `eval_command` runs the
**target repo's** code. These are separate processes and routinely need
**different interpreters** — `agent_evolve` is typically a user-level
install (system Python, `~/.local/bin/agent-evolve`) and will **not** be
importable under a target project's `uv run` / `.venv`.

So: never conclude the backend is missing from one failed import. Check

```
python -c "import agent_evolve"      # system interpreter
py -c "import agent_evolve"          # launcher
Get-Command agent-evolve             # CLI on PATH
```

and only then decide. Set `eval_command` to whichever interpreter has the
*target repo's* dependencies (commonly `uv run python ...`), independent
of how the supervisor imports the library. If the two genuinely cannot be
reconciled, say so and ask — do not silently fall back to hand-running
the protocol, which loses every artifact below (branches, reviewer
verdicts, trait matrix, ablation report, PR).

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
from agent_evolve.git_utils import detect_default_branch
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
    evolution=EvolutionSpec(),              # defaults: 4 rounds, 3 candidates
    runtime_mode=RuntimeModeSpec(),         # equivalence required by default
    # detect_default_branch() inspects origin/HEAD then the current branch
    # so SafetySpec picks up whatever the target repo actually calls its
    # trunk (main / master / trunk / develop / ...). Falls back to "main"
    # only when no signal is available.
    safety=SafetySpec(protected_branch=detect_default_branch()),
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

## Phase 0b — Measure the baseline

Before round 1, you measure the actual baseline by running the eval
command on `safety.protected_branch`. This anchors the search: every
later candidate is "better than the baseline" or "worse than the
baseline" relative to *this measurement*, not relative to whatever the
user implied in prose.

Two reasons it is non-negotiable:

1. **Catch eval misconfiguration before burning the search budget.**
   If the eval command's `cwd` is wrong, the dataset is missing, or the
   benchmark is degenerate, the search will obediently optimise toward
   nonsense. Round-0 measurement makes that visible immediately.
2. **Anchor `metrics_improved`.** The reviewer compares each candidate
   to the active frontier, but the *frontier* is anchored to whatever
   round 1 produced. Without an explicit baseline measurement, a
   regressing-but-better-than-each-other run can pass the reviewer
   gate while still being worse than `main`.

The procedure:

```python
from agent_evolve.eval import run_eval, validate_baseline
from agent_evolve.git_utils import current_sha
from agent_evolve.worktree import create_worktree

# 1. Materialise the protected branch at a FIXED SHA in its own worktree —
#    never measure in the user's checkout (their uncommitted state would
#    leak into the baseline). This SHA is the run's anchor: resolve it
#    ONCE, here, and reuse it for every candidate worktree in every round
#    (Phase 0c re-anchors it to the seed commit in sibling mode). If it
#    cannot be resolved, stop before spending any budget.
anchor = current_sha(spec.safety.protected_branch)
if anchor is None:
    raise RuntimeError(f"cannot resolve {spec.safety.protected_branch!r} to a SHA")
baseline_wt = create_worktree(
    f"evolve/{problem_id}/baseline", repo=repo_root, base=anchor,
)

# In sibling mode, eval_command carries a literal {symbol} token; the
# baseline measures the CANONICAL symbol, so substitute it here
# (spec.sibling.symbol_name, or auto-detect the unique top-level symbol
# the same way seed_sibling does). In replace mode this is a no-op —
# the command has no token and eval_command_for returns it verbatim.
baseline_cmd = (
    spec.eval_command_for(canonical_symbol)
    if spec.artifact_mode == "sibling"
    else spec.eval_command
)
baseline_eval = run_eval(
    baseline_cmd,
    cwd=spec.resolved_eval_cwd(baseline_wt.path),
    scratch=baseline_wt.path.with_name(baseline_wt.path.name + ".scratch"),
)

# 2. Validate against the user's stated expectation, if any.
check = validate_baseline(
    measured=baseline_eval.metrics,
    expected=spec.expected_baseline,        # None when not configured
    tolerance=spec.expected_baseline_tolerance,
)

if not check.matches:
    # Abort. The user's eval setup disagrees with their stated
    # expectation by more than the tolerance — the search would optimise
    # toward a number that does not reflect production behaviour.
    raise RuntimeError(f"baseline mismatch — aborting:\n{check.message}")

# 3. Persist the measured baseline on the problem root so the reviewer
#    and later rounds can read it — the trait matrix and the reviewer's
#    metrics_improved check both anchor to this measurement.
#    Local backend: write <problem>/baseline.json.
#    GitHub / GitLab: a "## Baseline" section in the problem issue body.
```

When `spec.expected_baseline is None`, `validate_baseline` returns
`matches=True` with a "not validated" message — the gate is skipped but
the measured baseline is still recorded. Always run the measurement
even when validation is off.

If the eval command is genuinely impossible to run on the protected
branch (e.g. the function under evolution does not exist yet — a
greenfield correctness search), surface that to the user before
proceeding: a missing baseline means the reviewer cannot evaluate
"metrics_improved" honestly, and you should ask whether to relax the
metric to `test_pass_rate >= 1.0` only.

## Phase 0c — Artifact mode (replace vs sibling)

Before round 1 dispatches, decide **what should happen to the winning
artifact at finalize time**. Two modes, declared by `spec.artifact_mode`:

- **`replace`** (default) — the file in `scope.target_files[0]` is
  mutated in place across rounds. The winner PR diffs the canonical
  file. Use this when *the artifact IS the production code*: a hot
  loop being optimised, a function whose only callers are tests, a
  benchmark you want to overwrite. Behaviour-preserving runtime-mode
  evolutions (`spec.mode == "runtime"`) almost always want `replace`.

- **`sibling`** — the canonical file is sealed under `do_not_touch`,
  and a fresh sibling file is seeded from it before round 1. Evolution
  mutates the sibling. The winner PR adds a *new* file rather than
  mutating any existing one. Use this when *the artifact is one of
  many in a library catalogue* (strategy classes, optimizer variants,
  ML model registry entries, parser dialects) — anywhere there are
  downstream callers of the canonical name that should keep their
  existing behaviour.

### Decision rule

Pick `sibling` if **any** of the following is true:
- The class/function being evolved is exported from a library and is
  imported by callers outside `scope.target_files`.
- The user wants to compare the original and the evolved version side
  by side at runtime.
- The user wants to run evolution on the same target repeatedly over
  time and accumulate distinct dated artifacts.
- The mode is `algorithm` *and* the symbol's name is part of a public
  API.

Otherwise pick `replace`. When in doubt, ask once.

### Manifest schema

```yaml
artifact_mode: replace          # default — current in-place behaviour
# OR
artifact_mode: sibling
sibling:
  # Pattern for the new symbol's name. Tokens: {original}, {ProblemId},
  # {Date}. Default: "{original}{ProblemId}{Date}". Must produce a
  # unique identifier per run.
  symbol_rename_pattern: "{original}{ProblemId}{Date}"
  # Pattern for the new file's name (without extension). Tokens:
  # {original_stem}, {problem_id}, {date}. Default produces e.g.
  # "ml_regime_strategy_multiasset_2026_04_30".
  file_rename_pattern: "{original_stem}_{problem_id}_{date}"
  # Where the seeded file lives. Default: alongside the original.
  output_dir: ""
```

Token semantics: `{original}` = original symbol's name (e.g.
`MLRegimeStrategy`); `{ProblemId}` = run's problem-id in PascalCase
(e.g. `Multiasset`); `{Date}` = run start date as `YYYYMMDD` (e.g.
`20260430`); `{original_stem}` = original file's stem; `{problem_id}` =
problem-id as-is; `{date}` = `YYYY_MM_DD`.

### Seed step (sibling mode only)

Run this between Phase 0b and round 1 of Phase A. Three obligations,
all load-bearing:

1. **Seed in a worktree and COMMIT.** Candidate trees are materialised
   from the run anchor — an uncommitted seed file in anyone's checkout
   is invisible to every candidate.
2. **Re-anchor the run.** After the seed commit, the run's `anchor` IS
   the seed commit; every candidate worktree created in Phase C
   (`base=anchor`) then inherits the seeded file.
3. **Re-derive the spec with `dataclasses.replace`.** `ScopeSpec` and
   `ProblemSpec` are both frozen — attribute assignment raises
   `FrozenInstanceError`. Build a new spec; never mutate.

```python
from dataclasses import replace
from agent_evolve.artifact import seed_sibling
from agent_evolve.git_utils import current_sha
from agent_evolve.worktree import create_worktree

if spec.artifact_mode == "sibling":
    seed_wt = create_worktree(
        f"evolve/{problem_id}/seed", repo=repo_root, base=anchor,
    )
    # Writes the renamed sibling file into the seed tree. The returned
    # paths are repo-root-relative POSIX — exactly what scope patterns
    # and git diffs speak.
    seed = seed_sibling(spec, problem_id=problem_id, repo_root=seed_wt.path)

    # Commit inside the seed tree, then re-anchor the run to the seed
    # commit (same pseudo-helper convention as git_diff below: add -A +
    # commit in that tree).
    git_commit_all(seed_wt.path, f"seed sibling artifact for {problem_id}")
    anchor = current_sha(f"evolve/{problem_id}/seed", cwd=seed_wt.path)

    # Frozen dataclasses: derive, don't mutate.
    spec = replace(
        spec,
        scope=replace(
            spec.scope,
            target_files=[seed.new_path],
            do_not_touch=[*spec.scope.do_not_touch, seed.original_path],
        ),
    )

    # Phase 0b baseline must reproduce against the seeded file. Re-run
    # the eval with --strategy <new_symbol> (or whatever the eval CLI
    # uses) and compare to the original baseline within tolerance.
    seed_eval = run_eval(
        spec.eval_command_for(seed.new_symbol),
        cwd=spec.resolved_eval_cwd(seed_wt.path),
        scratch=seed_wt.path.with_name(seed_wt.path.name + ".scratch"),
    )
    seed_check = validate_baseline(
        measured=seed_eval.metrics,
        expected=baseline_eval.metrics,
        tolerance=spec.expected_baseline_tolerance,
    )
    if not seed_check.matches:
        raise RuntimeError(
            f"sibling seed disagrees with original baseline beyond tolerance:\n"
            f"{seed_check.message}\n"
            f"this means the symbol rename was not behaviour-preserving — abort."
        )
```

The seed must reproduce the original's metrics within tolerance. If it
does not, the rename was not behaviour-preserving (e.g. a self-reference
to the class name was missed) — abort before burning the search budget.

### Reviewer impact

Reviewers receive the same scope-violation rule as in `replace` mode,
but in `sibling` mode the canonical file is automatically in
`do_not_touch` — so a candidate that mutates the original (e.g. by
re-importing it and patching) is rejected without the reviewer needing
to know which mode is active.

### Finalize impact

At finalize time, the winner PR's diff in `sibling` mode is "added 1
new file"; in `replace` mode it is "modified 1 existing file". Both
modes still target `safety.protected_branch`; both are still left open
for human merge.

## External agent dispatch

The manifest's optional `agents:` block names *which* agent fills each
role. Read it from `spec.agents`:

```python
spec.agents.supervisor       # informational — you are the supervisor
spec.agents.explorer         # str | list[str]; default "claude"
spec.agents.explorer_list()  # always-list view — use for round-robin slot assignment
spec.agents.reviewer         # str; default "claude"
```

The `explorer` value can be either a single agent name (the default,
backwards-compatible case) or a **list** that forms an *ensemble*. For
ensembles, the supervisor distributes a round's
`candidates_per_round` slots round-robin across the list:

```text
explorer = ["claude", "gemini"], candidates_per_round = 3
slot 1 -> claude
slot 2 -> gemini
slot 3 -> claude
```

This mixes exploration heuristics from different model families inside
a single round without changing the rest of the loop. Use
`spec.agents.explorer_list()` to get the always-list form so you do not
have to special-case the singleton.

For any individual slot whose resolved agent is `"claude"`, follow the
in-session dispatch path — spawn an `Agent` subagent. The current Claude
Code session model (Opus 4.7 / latest) does the work.

For any individual slot whose resolved agent is **not** `"claude"`,
dispatch via `Bash` to the named external CLI. You — not Python — are
responsible for the interfacing. The procedure:

1. **Resolve the agent name to a CLI binary.** Common names:
   - `gemini` → `gemini` (Gemini CLI from Google)
   - `codex` → `codex` (Codex CLI)
   - Anything else → ask the user how to invoke it. Do not guess.
   Confirm the binary is on PATH (`Bash("which <name>")`); if not,
   stop and ask the user.
2. **Build the prompt.** Read the role's SKILL.md verbatim — that is the
   system prompt. Append a role-specific assignment block:
   - **Explorer**: candidate id, branch name, operator, parent diff(s)
     and EVOLVE_STATE, the full `agent-evolve.yaml`, and an explicit
     instruction to commit the candidate to its branch and emit the
     completed EVOLVE_STATE block as the last thing on stdout.
   - **Reviewer**: the candidate's branch, the full diff vs. parent and
     vs. `main`, the EVOLVE_STATE block, the eval result and metrics,
     the equivalence report (runtime mode), and the spec. Instruct it to
     emit *only* the `VERDICT/REASON/CHECKLIST/CONFIDENCE` block on
     stdout — no preamble, no commentary.
3. **Invoke via `Bash`.** Single-shot is preferable. For an agentic CLI
   (gemini-cli, codex), pass the prompt as the argument the CLI accepts
   and let it use its own file-edit / git tools. For a one-shot text
   CLI (reviewer only), pipe stdin or use the CLI's prompt flag.
4. **Validate the structured output.**
   - Explorer: confirm the branch exists, the diff is inside scope, and
     an EVOLVE_STATE block is present. If any is missing, retry once
     with a stricter "your output must include the EVOLVE_STATE block"
     reminder. If still missing, mark the candidate failed (`prune` with
     reason `"external explorer produced unparseable output"`) and
     continue.
   - Reviewer: parse `VERDICT: ...`, the checklist, and the confidence.
     If parse fails, retry once with the format reminder. If still
     malformed, record a `REJECT` verdict with reason `"external
     reviewer output unparseable after one retry"` and continue.
5. **Never let an external-agent failure poison the run.** A bad
   external agent should look like a rejected candidate, not a stopped
   loop.

The default in-session path (everything is `"claude"`) is unchanged — the
above only kicks in when the manifest opts out for a specific role.

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

## Telemetry

For every candidate, populate the following fields as the candidate
moves through the round lifecycle. The data is persisted automatically
(it lives on the `Candidate` dataclass — no separate API call needed):

- **`candidate.started_at`** (ISO8601 UTC) — set when the candidate is
  dispatched to its explorer at Phase C.
- **`candidate.completed_at`** (ISO8601 UTC) — set when the reviewer
  verdict is recorded at Phase E.
- **`candidate.phase_durations_ms`** (`dict[str, float]`) — wall-clock
  ms per phase. Keys: `"explorer"`, `"eval"`, `"equivalence"`,
  `"reviewer"`. Capture `time.perf_counter()` deltas around each phase.
- **`candidate.diff_stats`** (`dict[str, int]`) — populated from
  `git_utils.diff_stats(diff_text)` in Phase D after the candidate's
  diff is fetched. Keys: `"files_changed"`, `"additions"`,
  `"deletions"`.
- **`candidate.operator_reason`** (str) — one-line note on why this
  operator was assigned to this slot, mirroring the round-plan note
  attached to the problem root in Phase B (e.g. "frontier had 1
  candidate -> mutate", "stalled 2 rounds -> explore").

Run-level metadata (`run_started_at`, `run_completed_at`,
`protected_branch_sha`) is captured automatically by
`backend.create_problem()` and `backend.finalize()` — you do not need
to populate it.

## Round lifecycle

Repeat this loop for `spec.evolution.rounds` iterations. A round is done
when every candidate in it has been scored, reviewed, and either pruned or
marked active.

### Phase A — Read state

1. Fetch the Trait Matrix via `backend.get_leaderboard()`.
2. Identify the active frontier: candidates whose status is `approved`.
3. If this is round 1 and the frontier is empty, the operator for the
   round is forced to `explore` (baseline).
4. Collect the negative-result ledger: the `hypothesis` and `conclusion`
   of every candidate pruned or rejected so far. Each is an experiment
   the run has already paid for; it earns that cost only if later rounds
   read it.

### Phase B — Choose operators

For each of the `candidates_per_round` slots, pick one operator from
`spec.evolution.operators`. Heuristic:

- If the frontier has only 1 candidate: `mutate` it.
- If the frontier has ≥2 candidates with complementary trait profiles: `crossover`.
- If progress has stalled for 2 consecutive rounds (no metric improvement
  ≥1%): `explore`.
- Otherwise split the slots across all three operators.

Check each planned slot against the negative-result ledger: do not
re-dispatch a disconfirmed hypothesis unless the round plan names what
differs this time (new parent base, different application point, new
combination) — a changed condition makes a new experiment. Entries
pruned as dominated rather than disconfirmed are not barred; their
hypotheses were confirmed, and they remain legitimate crossover
material as the frontier moves. List the ruled-out approaches in the
round plan note.

Write your reasoning into a short "round plan" note and attach it to the
problem root via `backend.update_graph` (as a comment line above the
Mermaid block in the problem description).

### Phase C — Dispatch to explorers

For each slot, assign one parent (or two, for crossover) and the operator.
Attach each slot's disconfirmed-hypothesis list (the disconfirmed
entries of the negative-result ledger relevant to its lineage) so no
explorer re-runs an experiment the ledger has already ruled out.
Spawn an explorer agent per slot — they work in parallel. Each explorer
follows `.claude/skills/explorer/SKILL.md` (invocable as `/explorer` once
registered, or via the `Agent` tool for parallel subagent execution).

Before any slot is dispatched, give its candidate an isolated tree
(prime directive 6):

```python
from agent_evolve.worktree import create_worktree

worktrees = {}   # candidate_id -> Worktree; read again in Phases D and E.5

# `anchor` is the run's fixed base SHA from Phase 0b (the seed commit in
# sibling mode — Phase 0c). Never re-resolve the protected branch here:
# re-resolving each round would let a moving trunk split the run across
# two different baselines.
worktrees[cid] = create_worktree(
    f"evolve/{problem_id}/candidate-{cid}", repo=repo_root, base=anchor,
)
```

`create_worktree` RAISES when isolation cannot be established — do not
catch-and-continue; a candidate without its own tree must not run.
Passing the anchor SHA (not a branch name — the call rejects those)
keeps every candidate rooted at the same baseline commit even if the
protected branch moves mid-run. Re-invocation after a crash is safe: an
existing tree, or a surviving branch whose tree was lost, is re-attached
rather than wiped.

Hand each explorer its tree:

- **`claude` slots (Agent tool)**: pin the subagent to the candidate's
  tree — state `worktrees[cid].path` in the prompt as the ONLY directory
  it may read, write, or run git in. (Alternative: the Agent tool's
  `isolation: "worktree"` gives the subagent a harness-managed tree
  instead; the commit still lands on the candidate branch in the shared
  repository, and Phase D then evals in the supervisor-created tree for
  that branch — `create_worktree` re-attaches it.)
- **external CLIs (gemini, codex, ...)**: invoke with the working
  directory set to `worktrees[cid].path` (`cd` there first) and state in
  the prompt that this tree is theirs alone.

Dispatch path depends on `spec.agents.explorer`:

- `"claude"` (default, single agent): use the `Agent` tool — explorers
  run as parallel Claude subagents in this session.
- single non-`"claude"` string: follow the **External agent dispatch**
  procedure above for each slot. External explorers cannot trivially be
  parallelised by the `Agent` tool, so run them sequentially via `Bash`
  unless the external CLI itself supports concurrent invocation.
- list (**ensemble**, e.g. `["claude", "gemini"]`): build the slot-to-
  agent assignment with `spec.agents.explorer_list()` and round-robin —
  slot `i` goes to `agents[i % len(agents)]`. For each slot, dispatch
  via the appropriate path (`Agent` tool for `claude`, **External agent
  dispatch** otherwise). Group the `claude` slots into a single
  parallel `Agent` invocation for efficiency; run the external-CLI
  slots sequentially alongside.

### Phase D — Collect and score

For each returned candidate:

1. Call `scope.enforce_scope(diff, spec.scope)`. If `in_scope` is false:
   `backend.prune(candidate_id, f"scope violation: {violations}")`. Skip.
2. Call
   `eval.run_eval(spec.eval_command, cwd=spec.resolved_eval_cwd(worktrees[cid].path), scratch=worktrees[cid].path.with_name(worktrees[cid].path.name + ".scratch"))`.
   `resolved_eval_cwd` joins `spec.eval_cwd` (always tree-relative — the
   manifest loader rejects absolute values) onto THIS candidate's tree:
   a benchmark living in `bench/` runs in `<candidate tree>/bench/`,
   never in a directory shared with other candidates. The `scratch` dir
   is likewise per-candidate — see *What isolation does NOT cover*.
3. If `spec.mode == "runtime"` and
   `spec.runtime_mode.equivalence_check != "disabled"`: run
   `equivalence.check_equivalence` on the target function pair.
4. Record metrics + equivalence via `backend.score_candidate(id, metrics,
   equivalence=report)`. If the equivalence report is not
   `equivalent: true`, attach a reviewer verdict of `REJECT` and move on.

#### What isolation does NOT cover

Worktrees isolate REPOSITORY paths only. An eval that writes to a fixed
absolute path (`/tmp/foo`, `C:/tmp/cache`, a hardcoded dataset scratch
dir) hits ONE directory from every candidate in every worktree —
concurrent candidates silently interleave reads and writes, and the
corruption never appears in any diff. The remedy is the scratch
contract:

- pass a fresh per-candidate directory as `run_eval(..., scratch=...)`;
  the runner creates it and exports it to the eval process as
  `AGENT_EVOLVE_SCRATCH`;
- an eval that caches or writes outside its working tree MUST honour
  `AGENT_EVOLVE_SCRATCH`; any fixed absolute scratch path corrupts
  concurrent runs and disqualifies the measurement.

Network resources, databases, GPUs, and port bindings are likewise
shared. If the eval touches them, serialise the slots or parameterise
per candidate — parallel dispatch is only valid when the eval's writes
are confined to its tree and its scratch dir.

### Phase E — Reviewer pass

For every scored candidate that is not already rejected, call the reviewer
agent (see `.claude/skills/reviewer/SKILL.md`; invocable as `/reviewer`).
Attach the verdict with `backend.record_verdict`.

Dispatch path depends on `spec.agents.reviewer`:

- `"claude"` (default): invoke the reviewer SKILL in-session.
- anything else: follow the **External agent dispatch** procedure above,
  treating the reviewer SKILL as the system prompt and emitting the
  `VERDICT/REASON/CHECKLIST/CONFIDENCE` block to stdout. Parse it into a
  `ReviewerVerdict` before calling `backend.record_verdict`.

#### Phase E.5 — production_runner cross-check (when configured)

If `spec.production_runner` is set, run it on every candidate the
reviewer just **APPROVED** (not on REQUEST_CHANGES / REJECT — those are
already out, no point spending the wallclock). The production runner is
the user's higher-fidelity / production-equivalent benchmark; its job
is to detect when `eval_command` has over-fit to a fast-but-incomplete
proxy.

```python
if spec.production_runner and verdict.verdict == "APPROVE":
    prod = run_eval(
        spec.production_runner,
        cwd=spec.resolved_eval_cwd(worktrees[candidate_id].path),
        scratch=worktrees[candidate_id].path.with_name(
            worktrees[candidate_id].path.name + ".scratch"
        ),
    )
    check = validate_baseline(
        measured=prod.metrics,
        expected=candidate.metrics,                 # eval said this
        tolerance=spec.expected_baseline_tolerance,
    )
    if not check.matches:
        # Eval said the metrics moved one way; production_runner
        # disagrees beyond the tolerance. Demote APPROVE to
        # REQUEST_CHANGES so a human (or a re-prompted explorer)
        # decides what to do.
        demoted = ReviewerVerdict(
            verdict="REQUEST_CHANGES",
            reason=(
                f"eval/production drift exceeds tolerance: {check.message}. "
                f"Original reviewer verdict was APPROVE."
            ),
            checklist={**verdict.checklist, "eval_matches_production": False},
            confidence=verdict.confidence,
            informative=verdict.informative,
        )
        backend.record_verdict(candidate_id, demoted)
    elif any(abs(d) > 0.5 * spec.expected_baseline_tolerance for d in check.drifts.values()):
        # Within tolerance, but on the order of measurement noise —
        # surface as INFORMATIVE, do not change the verdict.
        annotated = ReviewerVerdict(
            verdict=verdict.verdict,
            reason=verdict.reason,
            checklist={**verdict.checklist, "eval_matches_production": True},
            confidence=verdict.confidence,
            informative=(
                f"eval/production drift {max(abs(d) for d in check.drifts.values()):.1%} "
                f"is within tolerance but worth a manual look"
            ),
        )
        backend.record_verdict(candidate_id, annotated)
```

When `spec.production_runner` is `None`, skip this phase entirely — the
reviewer's `eval_matches_production` checklist item is also skipped.

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
   `approved` time. For `top_k` pruning, "highest-scoring" is measured
   on `spec.primary_metric()` — the `Metric` flagged `primary=True`, or
   the first metric as fallback.
2. If no winner exists: abort with a clear note in the problem description
   ("all candidates rejected — human intervention required"); **do not**
   open a final PR.
3. Otherwise call `backend.finalize(winner_id)`. The backend:
   - applies `safety.branch_cleanup` to every non-winning branch
     (`keep` / `archive` / `delete`)
   - bundles run artifacts (report HTML, trait matrix, per-candidate
     EVOLVE_STATE) under `<problem>/artifacts/` (local backend) or
     attaches them to the final PR (github / gitlab)
   - opens a new PR from the winner's branch against the protected
     branch (`spec.safety.protected_branch`)
   - attaches the full Trait Matrix, evolution graph, and reviewer
     verdict (including any `INFORMATIVE` note from the reviewer)
   - returns the PR URL
4. **Run the post-hoc ablation report** when
   `spec.safety.run_ablation_report` is `True` (the default):

   ```python
   from agent_evolve.ablation import (
       run_ablation_report, render_ablation_markdown,
   )

   diff_text = git_diff(winner.branch_name(), spec.safety.protected_branch)

   # A dedicated tree for the ablation pass, same API as the candidates:
   ablation_wt = create_worktree(
       f"evolve/{problem_id}/ablation", repo=repo_root, base=anchor,
   )

   def apply_ablation(hunk):
       # Materialise the hunk-stripped tree in the ablation worktree.
       # Return True on success, False if `git apply --reverse` cannot
       # apply the patch cleanly. The supervisor is responsible for
       # restoring the tree between hunks.
       return apply_reverse_patch(hunk.patch, ablation_wt.path)

   report = run_ablation_report(
       winner_id=winner.candidate_id,
       winner_metrics=winner.metrics,
       diff_text=diff_text,
       eval_command=spec.eval_command,
       eval_cwd=spec.resolved_eval_cwd(ablation_wt.path),
       apply_ablation=apply_ablation,
   )
   pr_body_addendum = render_ablation_markdown(report)
   # Append the markdown to the final PR body — backend-specific:
   #   local:  doc["ablation_report_md"] = pr_body_addendum
   #   github: final.edit(body=final.body + pr_body_addendum)
   #   gitlab: same shape via the MR PUT endpoint
   ```

   The pass is *informational*: it does not promote a hunk-stripped
   variant to "new winner" even if such a variant scores better. It
   only flags the discrepancy in the report. A human (or a tighter
   re-run) decides what to do.

   When `spec.safety.run_ablation_report` is `False`, skip this step
   entirely — useful when the eval is expensive and the human reviewer
   does not need the per-hunk breakdown.
5. **Remove the run's worktrees, keep the candidate branches.** For
   every candidate tree, plus the baseline / seed / ablation trees:
   `remove_worktree(wt, repo=repo_root)` (best-effort by design — a
   cleanup failure must not kill a finished run), and delete each tree's
   `.scratch` sibling with a plain directory removal. CANDIDATE branches
   stay, governed by `safety.branch_cleanup` — every candidate commit
   remains reachable for forensics. The utility branches
   (`.../baseline`, `.../seed`, `.../ablation`) may be deleted outright:
   baseline and ablation point at the anchor itself, and the seed commit
   is the base of every candidate branch, so no commit becomes
   unreachable. Trees are disposable; candidate branches are the record.
6. Record the final PR URL in the problem root.
7. **Stop.** Do not monitor the PR. Do not re-run. Do not merge.

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
