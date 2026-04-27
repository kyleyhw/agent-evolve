# The `agent-evolve.yaml` manifest

A full field-by-field reference for the manifest. You only need this when
you want reproducibility (CI, re-runs, fine-tuned knobs) — for one-off
runs, just tell Claude what to evolve in natural language and `/evolve`
builds the spec for you. See [`docs/examples.md`](examples.md) and the
README's "Use cases" section for recipes by shape of problem.

The parser lives in [`src/agent_evolve/config.py`](../src/agent_evolve/config.py);
the typed dataclasses it populates live in
[`src/agent_evolve/models.py`](../src/agent_evolve/models.py). Those are
the authoritative sources — this doc is derived from them.

---

## Complete example

```yaml
version: 1

problem:
  description: "Optimise the order pricing calculator for speed and accuracy"
  mode: runtime                         # algorithm | runtime
  eval_command: "pytest tests/pricing/ --benchmark-json=benchmark.json"
  metrics:
    - name: duration_ms
      optimise: minimize
    - name: test_pass_rate
      optimise: maximize
      minimum: 1.0                      # hard constraint — must be 100%

scope:
  target_files:
    - src/pricing/calculator.py
    - src/pricing/utils.py
  do_not_touch:
    - src/auth/
    - src/db/
    - src/pricing/models.py
  max_diff_files: 3

evolution:
  rounds: 5
  candidates_per_round: 3
  operators: [mutate, crossover, explore]
  prune_strategy: pareto                # pareto | top_k

runtime_mode:
  equivalence_check: required           # required | optional | disabled
  property_test_samples: 500
  regression_tests: "pytest tests/pricing/ -x"

safety:
  protected_branch: main
  agents_can_merge: false               # informational — hardcoded False
  require_human_approval: true
  final_pr_reviewers:
    - kyleyhw

backend:
  type: local                           # local | github | gitlab
  repo: kyleyhw/agent-evolve
  root_dir: evolve-state
```

A *minimal* manifest is just `problem`, `scope`, and `backend` — every
other section has sensible defaults:

```yaml
problem:
  description: "Make fib(n) fast"
  eval_command: "python bench.py"
  metrics:
    - {name: duration_us, optimise: minimize}
scope:
  target_files: [src/fib.py]
backend:
  type: local
```

---

## `problem`

Required. Describes *what* is being optimised and *how* it is measured.

### `problem.description` (string, **required**)

Human-readable one-liner. Shown in the Issue body (GitHub backend), the PR
titles, and the evolution graph header. No functional effect; anchors the
search in the evolution log so you remember *why* the run happened.

### `problem.mode` (string, default `"algorithm"`)

| Value | Meaning |
|---|---|
| `algorithm` | Behaviour is allowed to change. Used for metric optimisation where the "right answer" is a number, not a reference implementation (e.g. Sharpe ratio, win rate, fitting parameters). |
| `runtime` | Behaviour must stay the same. The equivalence checker compares the baseline and candidate on property-based inputs; the reviewer rejects any candidate that disagrees. Used for "make it faster / cleaner without changing what it does". |

### `problem.eval_command` (string, **required**)

A shell command that evaluates the candidate. Run with the candidate's
working directory as `cwd`, so paths are relative to the target repo
root. **Must emit machine-readable metrics on stdout** — see the *How
metrics flow from eval output into the manifest* section below.

Examples:

```yaml
# pytest with benchmark plugin
eval_command: "pytest tests/pricing/ --benchmark-json=benchmark.json"

# custom script emitting JSON
eval_command: "python scripts/bench.py"

# Make target
eval_command: "make bench"

# multi-stage — first test, then measure
eval_command: "pytest tests/billing/ && radon cc src/billing/invoice_processor.py -a --json"
```

The command should exit `0` on success. Non-zero is treated as a failing
run — the supervisor records whatever metrics were emitted before the
failure and lets the reviewer decide.

### `problem.metrics` (list, **required**, ≥ 1 entry)

Tells the supervisor what to optimise for and what hard constraints are
inviolable. Entries look like:

```yaml
metrics:
  - name: duration_ms
    optimise: minimize
    minimum: 0                # optional floor
    maximum: 5000             # optional ceiling
  - name: test_pass_rate
    optimise: maximize
    minimum: 1.0              # this metric must equal 1.0 or rejection
```

Fields per entry:

| Field | Type | Notes |
|---|---|---|
| `name` | string, required | The key the eval command emits. **Not a pre-existing variable in your code** — just a label that must match the stdout output. See below. |
| `optimise` | `minimize` or `maximize`, required | Direction. Used for Pareto pruning + "metrics_improved" check. |
| `minimum` | number, optional | Hard floor. Candidate is rejected if this metric falls below `minimum`. |
| `maximum` | number, optional | Hard ceiling. Candidate is rejected if this metric exceeds `maximum`. |

**Important:** metric `name`s are matched *verbatim* against the keys in
the eval command's output (JSON or `KEY=VALUE` lines). A typo in either
place silently produces empty metrics. Test your eval command once by
hand before running a full evolution.

---

## How metrics flow from eval output into the manifest

The eval runner at
[`src/agent_evolve/eval/runner.py`](../src/agent_evolve/eval/runner.py)
parses the candidate's stdout in two passes, in order:

### 1. JSON on stdout (preferred)

The runner finds the **last top-level JSON object** in stdout. Nested
dicts are flattened with dots, and booleans become `1.0` / `0.0`.

Your script does this:

```python
import json
print(json.dumps({
    "duration_ms": 42.1,
    "perf": {"cache_hits": 128, "cache_misses": 4},
    "test_pass_rate": 1.0,
}))
```

The runner extracts:

```python
{
    "duration_ms": 42.1,
    "perf.cache_hits": 128.0,
    "perf.cache_misses": 4.0,
    "test_pass_rate": 1.0,
}
```

Your manifest then references any of those keys (including dotted paths)
in `problem.metrics`:

```yaml
metrics:
  - {name: duration_ms,       optimise: minimize}
  - {name: perf.cache_misses, optimise: minimize}
  - {name: test_pass_rate,    optimise: maximize, minimum: 1.0}
```

### 2. `KEY=VALUE` fallback

If no JSON is found, the runner looks for lines shaped like
`identifier=number`:

```
duration_ms=42.1
test_pass_rate=1.0
```

Same metric-name matching rules apply.

### Your `eval_command` is responsible for the format

agent-evolve does not know what your command does; it just reads stdout.
If your test runner prints pytest's usual human-readable summary and
nothing else, the runner reports *no metrics found*. Two fixes:

- **Wrap the command** so it prints a JSON blob at the end:
  ```yaml
  eval_command: "pytest tests/ && python scripts/emit_metrics.py"
  ```
- **Use a plugin** that writes JSON and parse it:
  ```yaml
  eval_command: "pytest tests/pricing/ --benchmark-json=out.json && cat out.json"
  ```

---

## `scope`

Required. Defines the boundary of what a candidate is allowed to touch.
The scope enforcer validates every candidate's diff against this before
the reviewer even looks at it.

### `scope.target_files` (list, **required**)

Glob patterns for files the candidate *may* modify. Anything outside this
list is a scope violation that auto-rejects the candidate.

```yaml
target_files:
  - src/pricing/calculator.py          # single file
  - src/pricing/utils.py
  - src/pricing/**/*.py                # whole subtree
  - src/adapters/*.py                  # all direct children
```

Supported pattern syntax: POSIX glob (`*`, `**`, `?`, `[abc]`) via
`fnmatch` + our own `**` walker. Windows paths are normalised — you can
write either `src/a.py` or `src\a.py` in the diff; it matches the same
pattern.

A trailing `/` on a directory pattern is expanded to `<dir>/**`:

```yaml
target_files:
  - src/pricing/                       # same as src/pricing/**
```

### `scope.do_not_touch` (list, default `[]`)

Glob patterns that are **always** off-limits, even when they match
`target_files`. `do_not_touch` wins on conflict.

```yaml
scope:
  target_files: [src/**]
  do_not_touch:
    - src/auth/                        # never touch auth code
    - src/**/migrations/               # never modify DB migrations
    - src/pricing/models.py            # data model is contract — do not change
```

Classic `do_not_touch` entry: `tests/`. Without this the scope enforcer
will happily accept a candidate that "fixes" failing tests by deleting
them.

### `scope.max_diff_files` (int, optional)

Cap on the total number of files a candidate's diff can touch. No cap if
omitted. Useful for keeping changes reviewable.

```yaml
max_diff_files: 3
```

---

## `evolution`

Optional. Controls the shape of the search itself. Sensible defaults
if omitted.

### `evolution.rounds` (int, default `5`)

How many rounds of supervisor dispatch to run before `finalize()`. Each
round produces up to `candidates_per_round` candidates.

### `evolution.candidates_per_round` (int, default `3`)

How many slots the supervisor fills each round. Each slot becomes one
explorer invocation; explorers run in parallel when the agent runner
supports it (via the `Agent` tool).

### `evolution.operators` (list, default `[mutate, crossover, explore]`)

Which operators the supervisor is allowed to assign. Subsetting is
useful when you want a narrower search:

| Value | Meaning |
|---|---|
| `mutate` | Small targeted change to a single parent. The default when the active frontier has one strong candidate. |
| `crossover` | Combine structural elements from two parents. Default when the frontier has ≥2 candidates with complementary trait profiles. |
| `explore` | Start a new line of attack, ignoring the current frontier. Default when progress has stalled. |

Example: restrict to pure mutation (minimal architectural change)

```yaml
operators: [mutate]
```

### `evolution.prune_strategy` (string, default `"pareto"`)

How the supervisor prunes at the end of each round. Only Pareto-inferior
/ below-top-K candidates are pruned; anything rejected by the reviewer is
already out of the leaderboard.

| Value | Meaning |
|---|---|
| `pareto` | Keep every candidate on the Pareto front across all `problem.metrics`. Good when you have competing objectives (e.g. speed *and* test coverage). |
| `top_k` | Keep the top K candidates by the first metric, where K defaults to `candidates_per_round`. Good when there is a single dominant metric and the search is narrow. |

---

## `runtime_mode`

Optional. Only consulted when `problem.mode == "runtime"`. Controls the
equivalence-checking gate.

### `runtime_mode.equivalence_check` (string, default `"required"`)

| Value | Meaning |
|---|---|
| `required` | Run the property-based equivalence checker on every candidate. The reviewer rejects if `equivalent == False` or if the check cannot run. This is the default and the intended setting for any serious runtime-mode run. |
| `optional` | Run the check; surface the result to the reviewer but don't auto-reject. The reviewer may still reject on mismatch as part of its checklist. |
| `disabled` | Skip the check entirely. Necessary if your function has external dependencies (DB, HTTP, global state) that can't be mocked under `hypothesis`. **Think carefully before setting this** — you lose the guarantee that a "faster" candidate computes the right answer. |

### `runtime_mode.property_test_samples` (int, default `500`)

Number of random inputs the equivalence checker runs through both the
baseline and the candidate. Higher = stronger guarantee, slower eval.

Rule of thumb:

- 100 samples: quick sanity check, a dev might skip
- 500 samples (default): solid guarantee for numeric / list functions
- 2000+ samples: use when the input space is large or you suspect rare
  edge cases

### `runtime_mode.regression_tests` (string, optional)

A broader test command the reviewer runs as a smoke check — separate
from `problem.eval_command`. Useful when the eval command is a narrow
benchmark and you want to confirm no adjacent functionality regressed.

```yaml
runtime_mode:
  regression_tests: "pytest -x"        # whole suite, fail fast
```

If omitted, the reviewer uses `problem.eval_command` for the regression
smoke check (which is often the same command).

---

## `safety`

Optional. Hard constraints that the supervisor cannot override at
runtime. Most fields exist to document intent — the real enforcement
happens in the Python layer (`EvolveBackend.__init_subclass__` +
`assert_no_merge`).

### `safety.protected_branch` (string, default `"main"`)

The branch agents may never merge into. The final PR is opened *against*
this branch; a human merges. The GitHub backend additionally installs a
branch-protection rule on this branch during `create_problem()`.

### `safety.agents_can_merge` (bool, ignored — hardcoded `False`)

This field exists for surface parity with the example manifests. **The
code forces it to False regardless of the YAML value.** Attempting to
flip it in a backend subclass raises `TypeError` at class-creation time.

### `safety.require_human_approval` (bool, default `true`)

Documentary. Any backend you add must refuse to merge without a human
approval signal; see the GitHub backend's `finalize()` for the reference
implementation (it opens the final PR and returns — never merges).

### `safety.final_pr_reviewers` (list of strings, default `[]`)

GitHub / GitLab handles. Anyone listed is added as a requested reviewer
on the final PR. No effect on the local backend beyond being recorded in
`final_pr.json`.

```yaml
final_pr_reviewers:
  - kyleyhw
  - senior-reviewer
```

---

## `backend`

Required. Selects the backend adapter + its config.

### `backend.type` (string, **required**)

| Value | Meaning |
|---|---|
| `local` | `LocalBackend`. State lives under `evolve-state/<problem_id>/` as JSON files. No remote calls; useful for CI, testing, and offline runs. |
| `github` | `GitHubBackend`. Issues play the role of problem roots; PRs are candidates. Installs a branch-protection rule on `safety.protected_branch` during `create_problem()`. Requires a `GH_TOKEN` or `GITHUB_TOKEN` with `repo` scope. |
| `gitlab` | `GitLabBackend`. Mirrors the GitHub one against the GitLab REST API. Requires a `GL_TOKEN` or `GITLAB_TOKEN`. Honours `GITLAB_URL` for self-hosted instances. |

### `backend.repo` (string, optional — required for `github` / `gitlab`)

The `owner/name` slug for the remote backend.

```yaml
backend:
  type: github
  repo: kyleyhw/my-project
```

### `backend.root_dir` (string, optional — local backend only)

Where `LocalBackend` stores state. Defaults to `evolve-state/` in the
repo root. Usually left alone.

```yaml
backend:
  type: local
  root_dir: .cache/evolve-state        # keep runs under a cache dir
```

---

## `agents`

Optional. Per-role agent assignment. Every role defaults to `"claude"` —
the in-session Claude Code model (Opus 4.7 / latest), dispatched via the
`Agent` subagent tool. Any other value is a bare agent name (e.g.
`gemini`, `codex`); the supervisor SKILL — not Python — resolves it to a
concrete CLI invocation, builds the role's prompt from the role's
`SKILL.md`, runs it via `Bash`, and parses the structured output.

```yaml
agents:
  reviewer: gemini                  # use Gemini CLI for the reviewer role
  explorer: [claude, gemini]        # ensemble — slots round-robin'd across both
  # supervisor: claude              # informational — supervisor stays in-session
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `agents.supervisor` | string | `"claude"` | Informational. The supervisor *is* the running Claude Code session; this version cannot swap it. To run the whole loop under another model, you need a headless CLI runner (not yet implemented). |
| `agents.explorer` | string **or** list of strings | `"claude"` | A single name uses one model for every slot. A **list** is an *ensemble* — the supervisor distributes the round's `candidates_per_round` slots round-robin across the list, mixing exploration heuristics from multiple models inside a single round. Each non-`"claude"` name in the list must resolve to an agentic CLI (`gemini`, `codex`, ...) capable of editing files and committing on a branch. |
| `agents.reviewer` | string | `"claude"` | The simplest role to swap — text in, structured verdict out. Any LLM CLI that takes a prompt and returns text on stdout works, provided it can be coaxed into the `VERDICT/REASON/CHECKLIST/CONFIDENCE` block. |

### Explorer ensemble worked example

With `candidates_per_round: 3` and `explorer: [claude, gemini]`, slots
1, 2, 3 are dispatched to `claude`, `gemini`, `claude` respectively.
Round 2 starts the cycle over: slots 1, 2, 3 → `claude`, `gemini`,
`claude` again. The `claude` slots are batched into a single parallel
`Agent` subagent invocation; the `gemini` slots run sequentially via
`Bash` to the Gemini CLI.

The supervisor SKILL handles parse failures by re-invoking the CLI once
with a "respond in this exact format" reminder; if the second attempt
still fails, the candidate is treated as REJECTed (reviewer) or marked
failed (explorer) and the loop continues. A flaky external agent
degrades gracefully rather than corrupting the run.

### When to use this

- **Reviewer swap** — cheapest and safest first step. Lets you cross-check
  Claude's verdicts against a different model's judgement, or use a
  cheaper model for the high-volume reviewer role.
- **Explorer swap (single agent)** — useful when you want a different
  family's exploration biases for a specific run.
- **Explorer ensemble** — useful for *covering* the search space.
  Different model families bias toward different refactors; round-robin
  dispatch across an ensemble surfaces candidates that none of the
  models would have proposed alone.
- **Supervisor swap** — not yet supported in-session; on the roadmap as
  a separate `agent-evolve run` headless CLI.

---

## `version`

Optional. Currently only `version: 1` is recognised. The parser ignores
the value — it exists so future schema changes can gate compatibility
without breaking old manifests.

---

## Common shapes by problem

Short recipes — not different schemas, just different knob settings for
the same manifest. These mirror the "What the skills are good at"
section of the README.

### Runtime optimisation ("faster, same behaviour")

```yaml
problem:
  mode: runtime
  eval_command: "pytest tests/pricing/ --benchmark-json=out.json"
  metrics:
    - {name: duration_ms,    optimise: minimize}
    - {name: test_pass_rate, optimise: maximize, minimum: 1.0}
runtime_mode:
  equivalence_check: required
  property_test_samples: 500
```

### Correctness ("make the failing tests pass")

```yaml
problem:
  mode: algorithm
  eval_command: "pytest tests/graphs/ --tb=short"
  metrics:
    - {name: test_pass_rate, optimise: maximize, minimum: 1.0}
scope:
  target_files: [src/graphs/]
  do_not_touch: [tests/]                 # can't pass by deleting tests
evolution:
  rounds: 8                              # wider search for correctness
```

### Statistical / metric optimisation ("higher Sharpe")

```yaml
problem:
  mode: algorithm
  eval_command: "python scripts/backtest.py --years 2020-2024"
  metrics:
    - {name: sharpe,       optimise: maximize}
    - {name: max_drawdown, optimise: minimize}
    - {name: win_rate,     optimise: maximize, minimum: 0.4}
evolution:
  prune_strategy: pareto                 # multiple competing objectives
```

### Clarity refactor ("cut complexity, keep behaviour")

```yaml
problem:
  mode: runtime
  eval_command: "pytest tests/billing/ && radon cc src/billing/invoice_processor.py -a --json"
  metrics:
    - {name: average_complexity, optimise: minimize}
    - {name: test_pass_rate,     optimise: maximize, minimum: 1.0}
runtime_mode:
  equivalence_check: required
```

---

## Editing the manifest with Claude

Every knob in this document can be set with a plain-English prompt —
nothing needs to be typed into the YAML by hand if you don't want to.
Claude edits the file and re-runs
`agent-evolve validate agent-evolve.yaml` to confirm the result parses.

### Per-field prompts

Grouped by manifest section so you can grab the one closest to what you
want and reword.

#### `problem.mode`

> "Switch the manifest to runtime mode — I want the behaviour preserved
>  while we optimise speed."

> "Change mode to algorithm. I want the reviewer to allow behavioural
>  changes as long as the metrics improve."

#### `problem.eval_command`

> "Change the eval command to `pytest tests/pricing/ --benchmark-json=out.json`
>  and make sure the metrics the reviewer expects match what that command
>  emits."

> "My eval script prints metrics as `KEY=VALUE` lines, not JSON. Confirm
>  the current `eval_command` works with that format."

#### `problem.metrics`

> "Add a `memory_mb` metric (minimize) alongside `duration_ms`. Update my
>  eval script to emit it too."

> "Tighten the `test_pass_rate` constraint — it should be a hard minimum
>  of 1.0, not just a preference."

> "Remove the `duration_ms` maximum — I don't want an upper ceiling, just
>  a minimise direction."

#### `scope.target_files` and `scope.do_not_touch`

> "Add `src/pricing/v2/` to `target_files` and put `src/pricing/v1/` into
>  `do_not_touch`."

> "Broaden the scope to anything under `src/pricing/**` but keep
>  `src/pricing/models.py` off-limits."

> "Add `tests/` to `do_not_touch` so no candidate can 'fix' failing tests
>  by deleting them."

#### `scope.max_diff_files`

> "Cap the diff at 2 files per candidate — the reviewer should reject
>  anything broader."

> "Remove the `max_diff_files` limit; I want to allow wider refactors."

#### `evolution.rounds` and `evolution.candidates_per_round`

> "Run 8 rounds instead of 5, with 4 candidates per round."

#### `evolution.operators`

> "Restrict the operators to `mutate` only — I want minimal architectural
>  change."

> "Drop `explore` from the operator list. Keep mutation and crossover."

#### `evolution.prune_strategy`

> "Switch `prune_strategy` to `top_k` — I only have one metric that
>  matters."

> "Use `pareto` pruning. I have three competing objectives (Sharpe,
>  drawdown, win rate)."

#### `runtime_mode.equivalence_check`

> "Set `equivalence_check` to `required`. Do not accept any candidate that
>  fails the property-based check."

> "Disable the equivalence check for this run — the target function hits
>  a database and can't be tested under hypothesis."

#### `runtime_mode.property_test_samples`

> "Bump `property_test_samples` to 2000 — the input space is large and I
>  want a stronger guarantee."

#### `runtime_mode.regression_tests`

> "Add a regression_tests command: `pytest -x` across the whole suite.
>  The reviewer should run it as a smoke check on every candidate."

#### `safety.final_pr_reviewers`

> "Add `kyleyhw` and `senior-reviewer` as required reviewers on the final
>  PR."

#### `backend.type`

> "Switch the backend to GitHub using `kyleyhw/my-project`. Set
>  `GH_TOKEN` from my `.env`."

> "Change to the GitLab backend pointing at `myorg/myrepo` on the
>  self-hosted instance at `https://gitlab.internal`. Set `GITLAB_URL`
>  accordingly."

#### `agents`

> "Use Gemini for the reviewer role this run; keep Claude for everything
>  else."

> "Set `agents.explorer` to `codex` so the explorer slots are filled by
>  the Codex CLI."

> "Make `agents.explorer` an ensemble of Claude and Gemini — distribute
>  the explorer slots round-robin between them so each round mixes their
>  exploration heuristics."

> "Reset all roles back to Claude — drop the `agents:` block from the
>  manifest."

### Compound edits

You can bundle multiple knob changes into one prompt — Claude will edit
all of them and validate once:

> "Make three changes to the manifest: switch to runtime mode, add
>  `memory_mb` (minimize) as a second metric, and cap `max_diff_files`
>  at 2."

### Starting from scratch

> "Create an `agent-evolve.yaml` for optimising `src/graphs/dijkstra.py`
>  to fix the three failing tests in `tests/graphs/`. Block candidates
>  from touching the tests. Use 8 rounds. Local backend."

Claude produces a full manifest, validates it, and tells you what it
chose for any fields you didn't specify.
