# agent-evolve

> **This repo is a skills bundle you install once and use across any number
> of other projects.** It is not a project you evolve in place. The three
> skills under `.claude/skills/` are meant to live at user scope
> (`~/.claude/skills/`) so the `/evolve`, `/explorer`, `/reviewer`
> commands are available in every repo you open with Claude Code.
>
> **Read this as instructions you give to Claude, not as commands you run
> yourself.** Claude is the one that runs the skills, calls the Python
> tooling, opens branches, and produces the final PR. You, the human, drive
> it by telling Claude what to optimise in your target project and then
> reviewing the PR Claude leaves open against `main`.

An evolutionary search system for codebases using cooperating language-model
agents on isolated git branches. Written with the goal of producing
performance, correctness, or clarity improvements surfaced as pull requests
for human approval.

Inspired by [`gh-evolve`](https://github.com/kaiwong-sapiens/gh-evolve),
extended with a platform-agnostic backend, module-scoped runs, supervisor +
reviewer orchestration, a runtime-optimization mode with property-based
equivalence checking, and an interactive D3.js evolution graph.

## Highlights

- **Claude drives everything.** The three `SKILL.md` files under
  `.claude/skills/` define the protocol; Claude Code auto-discovers them
  and exposes them as `/evolve`, `/explorer`, `/reviewer`. You tell
  Claude to optimise something; Claude runs the loop.
- **Platform-agnostic backend.** Local filesystem, GitHub (Issues + PRs),
  GitLab (Issues + MRs). A single abstract `EvolveBackend` interface; the
  supervisor doesn't know or care which one it is talking to.
- **Module-scoped runs.** Each evolution targets a specific file or directory;
  the scope enforcer rejects any candidate whose diff strays outside
  `target_files` or touches `do_not_touch`.
- **Runtime mode with logic equivalence.** Property-based testing via
  `hypothesis` — hundreds of random inputs through both the original and the
  optimised function; no claimed speedup is accepted without proof of
  equivalence.
- **Human approval gate.** Agents are architecturally forbidden from merging.
  `agents_can_merge` is `False` on the abstract base class; `__init_subclass__`
  raises `TypeError` if a subclass tries to override it. The final PR is
  opened against `main` but **left open** — Claude never merges, by
  construction.
- **Visual evolution graph.** Mermaid diagrams embedded in Issues for native
  GitHub rendering, plus a standalone interactive D3.js HTML report with
  lineage and timeline views.

## Install (once per machine)

```bash
git clone https://github.com/kyleyhw/agent-evolve.git
cd agent-evolve
uv run python install.py
```

[`install.py`](install.py) installs the `agent-evolve` Python package as a
`uv` tool (exposing the `agent-evolve` CLI) and symlinks every skill under
`.claude/skills/` into `~/.claude/skills/` so `/evolve`, `/explorer`, and
`/reviewer` are available in every repo you open with Claude Code.

Or ask Claude:

> "Install agent-evolve."

Claude will run the installer. Requires Python 3.12+ and
[uv](https://docs.astral.sh/uv/). On Windows, symlinking requires Developer
Mode or an elevated shell — the installer falls back to a full copy if
symlinking fails. Re-run with `--force` to overwrite existing skill links.

### Verify

Open any repo in Claude Code and type `/e` — you should see `/evolve` in
the completion list. If not, ask Claude:

> "`/evolve` is not registering from `~/.claude/skills/`. Diagnose."

See [`docs/skills.md`](docs/skills.md) for the full registration guide.

---

## Quickstart — what to tell Claude

Two paths: run the bundled example to see the loop work end-to-end, or
point Claude at your own project.

### A. See it work — run the example

Inside the `agent-evolve` repo, tell Claude:

> "Run `examples/demo_run.py` and show me the result."

Or directly:

```bash
uv run python examples/demo_run.py
```

This plays the supervisor / explorer / reviewer roles manually against a
toy Fibonacci optimisation target — four hardcoded candidate variants flow
through the real eval runner, scope enforcer, equivalence checker,
reviewer, and visualiser. It produces `examples/demo-report.html` — open it
to see the interactive evolution tree.

See [`docs/examples.md`](docs/examples.md) for a step-by-step walkthrough
of what the demo run shows, what each candidate demonstrates, and how to
modify the example to target your own function.

### B. Use it on your own project

Open your target repo in Claude Code (with agent-evolve installed per the
steps above) and just say what you want:

> "Evolve `src/pricing/calculator.py` for runtime. Keep the pricing tests
>  green."

Claude matches this to `/evolve` automatically (or you can type it
directly). The skill then infers the spec — mode, metrics, eval command,
scope — from your prose. It asks only about the gaps it can't guess
(typically just the eval command if your repo doesn't use `pytest`).
**No YAML required for one-off runs.**

Claude spawns `/explorer` subagents for each candidate slot (in parallel
via the `Agent` tool), runs eval + scope + equivalence on every candidate,
invokes `/reviewer` for each scored candidate, and regenerates the Mermaid
+ HTML evolution graph after every round. It leaves the winning PR open
against `main` for you to review and merge — it will not merge itself.

**For reproducibility (CI, repeated runs, fine tuning):** write the spec
to `agent-evolve.yaml` and invoke `/evolve agent-evolve.yaml`. Claude can
do this for you:

> "Save the spec you just inferred as `agent-evolve.yaml` so I can
>  re-run it in CI."

Or copy [`examples/agent-evolve.yaml`](examples/agent-evolve.yaml) as a
template and edit in place. The full field-by-field reference can be
found at [`docs/manifest.md`](docs/manifest.md).

**After the run:**

> "Summarise the evolution run for me. What did each operator try, and why
>  did the winner beat its parents?"

is a reasonable follow-up to ask Claude before you hit merge.

## What the skills are good at

Four concrete things Claude can do with the three skills. Each is just a
different shape of manifest — the same supervisor / explorer / reviewer
machinery drives all of them.

### 1. Runtime optimization — "make this faster without changing behaviour"

When you need a function to be fast and the current behaviour is
authoritative. Claude explores memoisation, vectorisation, smarter data
structures, algorithmic rewrites — and the equivalence checker rejects any
candidate that disagrees with the baseline on 500 random inputs.

> "Optimise `src/pricing/calculator.py` for runtime. Treat the current
>  behaviour as authoritative — any candidate that fails the equivalence
>  check at 500 samples is rejected."

```yaml
problem:
  mode: runtime
  eval_command: "pytest tests/pricing/ --benchmark-json=out.json"
  metrics:
    - {name: duration_ms,     optimise: minimize}
    - {name: test_pass_rate,  optimise: maximize, minimum: 1.0}
runtime_mode:
  equivalence_check: required
  property_test_samples: 500
```

This is the shape of [`examples/demo_run.py`](examples/demo_run.py) — see
the naive-recursive → memoised → iterative Fibonacci walkthrough further
down.

### 2. Statistical / metric optimization — "maximize Sharpe, minimize drawdown"

When behaviour *should* change and the goal is a better number. Trading
strategies, hyperparameter search, heuristic tuning, any scenario where
"the right answer" is defined by a benchmark and not by a reference
implementation.

> "Evolve `src/strategies/momentum.py` to maximize Sharpe ratio and
>  minimize max drawdown on the 2020-2024 backtest. Keep win rate above
>  40% as a hard constraint. Don't touch `src/strategies/risk.py`."

```yaml
problem:
  mode: algorithm                 # equivalence check disabled by default
  eval_command: "python scripts/backtest.py --years 2020-2024"
  metrics:
    - {name: sharpe,        optimise: maximize}
    - {name: max_drawdown,  optimise: minimize}
    - {name: win_rate,      optimise: maximize, minimum: 0.4}
evolution:
  operators: [mutate, crossover, explore]
  prune_strategy: pareto          # Pareto-front across the three metrics
```

This is the canonical [`gh-evolve`](https://github.com/kaiwong-sapiens/gh-evolve)
use case and what the Pareto pruning strategy was built for.

### 3. Algorithm correctness — "make the failing tests pass"

When a test suite is red and you want Claude to iterate until it's green.
The reviewer checklist's hard constraint (`test_pass_rate minimum: 1.0`)
rejects any candidate that doesn't hit 100%.

> "`src/graphs/dijkstra.py` is failing three tests in `tests/graphs/`. Run
>  the evolutionary loop with `test_pass_rate` as the only metric
>  (minimum 1.0). Don't modify tests or anything outside `src/graphs/`."

```yaml
problem:
  mode: algorithm
  eval_command: "pytest tests/graphs/ --tb=short"
  metrics:
    - {name: test_pass_rate, optimise: maximize, minimum: 1.0}
scope:
  target_files: [src/graphs/]
  do_not_touch: [tests/]          # can't pass tests by deleting them
evolution:
  rounds: 8                       # more rounds — correctness search is wider
  candidates_per_round: 3
```

The `do_not_touch: [tests/]` line is the critical one — it prevents the
scope enforcer from accepting the classic "I fixed the tests by deleting
the assertions" failure mode.

### 4. Clarity refactor — "keep the behaviour, cut the complexity"

When the code works but is unreadable. Runtime mode preserves behaviour;
the metric is some complexity measure. Claude mutates toward simpler
control flow, shorter functions, fewer nested conditionals — and the
equivalence check stops it from "simplifying" by breaking behaviour.

> "Refactor `src/billing/invoice_processor.py` for readability. Treat the
>  current behaviour as authoritative. Optimise for cyclomatic complexity
>  via radon, and keep all existing tests passing."

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

This one reads a complexity number out of `radon` — any eval command that
emits numeric metrics on stdout (JSON object or `KEY=VALUE` lines) works.

---

These four are not a fixed menu — they are just different manifests. You
describe the problem to Claude; Claude writes (or asks you to confirm) a
manifest in one of these shapes, then drives the loop.

## Using the GitHub backend

Tell Claude to switch:

> "Change the manifest's `backend.type` to `github` with `repo:
>  kyleyhw/my-project`, and set `GH_TOKEN` from my `.env`."

Claude will update the YAML and confirm the token is readable. With the
GitHub backend selected, `create_problem()` opens an evolutionary Issue and
installs a branch protection rule on `main`; candidates become draft PRs
with an `EVOLVE_STATE` JSON block embedded in their bodies; `finalize()`
opens a non-draft PR from the winner's branch against `main` and **stops**
— a human reviewer merges.

The token needs `repo` scope. You can supply it via `GH_TOKEN` or
`GITHUB_TOKEN` in the environment, or pass it to `GitHubBackend(..., github_token=...)`
explicitly if Claude is constructing the backend directly.

## The three skills

The skills are the canonical drivers — everything else (Python, CLI, the
demo script) is scaffolding so Claude has something concrete to call.

| Skill | Slash command | What Claude does when invoked |
|---|---|---|
| `evolve` | `/evolve [manifest \| natural language]` | Entry point. Infers a spec from prose or loads `agent-evolve.yaml`. Drives rounds, picks operators, gates with reviewer, opens the final PR. Never merges. Plays the supervisor role. |
| `explorer` | `/explorer <candidate-id> <operator> <parents>` | Produces one candidate — writes hypothesis, codes inside scope, commits to `evolve/<problem>/candidate-<n>`. |
| `reviewer` | `/reviewer <candidate-id>` | APPROVE / REQUEST_CHANGES / REJECT with an itemised checklist. |

All three are plain Markdown with YAML frontmatter. See
[`docs/skills.md`](docs/skills.md) for the full guide — how Claude Code
discovers them, how to make them available globally, how to tell Claude to
drive them from a custom agent runner, the frontmatter reference, and how
to fork the skills for a different optimisation target.

## How it works

```mermaid
flowchart TD
    S["<b>/evolve (supervisor role)</b><br/><i>.claude/skills/evolve/SKILL.md</i><br/>orchestrates rounds · picks operators<br/>prunes · regenerates graph · finalizes"]
    E["<b>Explorer</b><br/><i>.claude/skills/explorer/SKILL.md</i><br/>one per candidate slot<br/>writes hypothesis · codes inside scope"]
    R["<b>Eval Runner</b><br/>runs <code>eval_command</code><br/>parses JSON / KEY=VALUE metrics"]
    Q["<b>Equivalence</b><br/>property-based tests via <code>hypothesis</code><br/>runtime mode only"]
    V["<b>Reviewer</b><br/><i>.claude/skills/reviewer/SKILL.md</i><br/>APPROVE / REQUEST_CHANGES / REJECT<br/>with itemised checklist"]
    B[("<b>Backend</b><br/>local · github · gitlab")]
    Z["<b>Visualizer</b><br/>Mermaid diagram · D3.js HTML"]
    H["evolve-report.html"]

    S -- "dispatches" --> E
    E --> R
    R --> Q
    Q --> V
    S -- "gates" --> V
    S <--> B
    V --> B
    B --> Z
    Z --> H

    style S fill:#2c74b3,color:#fff,stroke:#174978,stroke-width:2px
    style V fill:#2d8a4e,color:#fff,stroke:#1b5e37,stroke-width:2px
    style E fill:#d4b483,color:#111,stroke:#8c7148
    style R fill:#d4b483,color:#111,stroke:#8c7148
    style Q fill:#d4b483,color:#111,stroke:#8c7148
    style B fill:#3b4358,color:#fff,stroke:#242a35
    style Z fill:#3b4358,color:#fff,stroke:#242a35
    style H fill:#10141b,color:#e6e8ec,stroke:#242a35
```

Every round:

1. Supervisor reads the Trait Matrix.
2. Picks one of `mutate` / `crossover` / `explore` for each of
   `candidates_per_round` slots.
3. Explorers produce candidates on `evolve/<problem>/candidate-<n>` branches.
4. Each candidate runs through the eval runner, the scope enforcer, and (in
   runtime mode) the equivalence checker.
5. The reviewer gates every scored candidate.
6. Pareto-inferior candidates are pruned.
7. The Mermaid graph + HTML report are regenerated and attached to the
   problem root.

After the last round, the supervisor calls `backend.finalize(winner_id)` —
closing losers, opening the final PR, and stopping. A human merges.

### Example: 4-candidate run over 3 rounds

A snapshot of the Mermaid graph that the supervisor attaches to the problem
Issue after each round (regenerated from [`examples/evolve-graph.mmd`](examples/evolve-graph.mmd)):

```mermaid
graph TD
    ROOT["Problem #42 — pricing calculator"]
    c1["candidate-1<br/>operator: explore<br/>R1 · 120ms<br/>status: pruned"]
    c2["candidate-2<br/>operator: mutate<br/>R2 · 88ms<br/>status: approved"]
    c3["candidate-3<br/>operator: explore<br/>R2 · 95ms<br/>status: approved"]
    c4["candidate-4 ⭐<br/>operator: crossover<br/>R3 · 61ms<br/>status: WINNER"]

    ROOT --> c1
    c1 --> c2
    c1 --> c3
    c2 --> c4
    c3 --> c4

    style ROOT fill:#3b4358,color:#fff,stroke:#242a35
    style c1 fill:#888,color:#fff,stroke:#555
    style c2 fill:#2c74b3,color:#fff,stroke:#174978
    style c3 fill:#2c74b3,color:#fff,stroke:#174978
    style c4 fill:#2d8a4e,color:#fff,stroke:#1b5e37,stroke-width:2px
```

The same data renders as an interactive D3 tree in
[`examples/evolve-report.html`](examples/evolve-report.html) with click-through
inspectors, a timeline view, and PNG export.

### The bundled example

Reference output from `examples/demo_run.py` (see Quickstart path A above
and [`docs/examples.md`](docs/examples.md) for the full walkthrough):

```
  #1  R1 explore    2598.02µs   REQUEST_CHANGES
  #2  R2 mutate        0.10µs   APPROVE          ← winner
  #3  R2 mutate        0.56µs   REJECT           (non-equivalent)
  #4  R3 crossover     0.58µs   APPROVE
```

The buggy candidate is rejected when the equivalence checker finds
`fib(0)` returning `1` instead of `0`. The demo writes
[`examples/demo-report.html`](examples/demo-report.html) — the D3 report
for this exact run.

## CLI utilities

Two small utilities for humans who want to inspect state directly, or that
Claude invokes when asked to validate / report:

```bash
# Validate a manifest
uv run agent-evolve validate examples/agent-evolve.yaml

# Rebuild the HTML report from an existing run's state
uv run agent-evolve report evolve-state/1 --output evolve-report.html
```

## Safety invariants

The `EvolveBackend` base class enforces these at class-creation time:

- `agents_can_merge` is hardcoded `False`. Subclasses redefining it raise
  `TypeError` before they can be instantiated.
- `assert_no_merge(action)` is called at the top of every `finalize()`
  implementation. It raises `MergeNotPermittedError` if the flag were ever
  flipped.
- `finalize()` opens a PR; it never merges. Closing losers, yes. Merging the
  winner, no — that's a human's job.

The GitHub backend additionally installs a branch protection rule on the
protected branch during `create_problem()`.

## Running the tests

Tell Claude:

> "Run the test suite."

Or directly:

```bash
uv run pytest -q
```

The suite covers scope enforcement, the local backend, merge-safety
invariants, the equivalence checker (including divergent exception
behaviour), the eval runner's JSON + KV parsing, and the visualization
pipeline.

## Project structure

```
install.py                      ← one-shot installer
src/agent_evolve/
    backends/     base.py  local.py  github.py  gitlab.py
    eval/         runner.py  equivalence.py
    sandbox/      docker_runner.py
    scope/        enforcer.py
    viz/          graph.py  mermaid.py  html_report.py
    models.py     config.py  cli.py
.claude/skills/                 ← auto-discovered by Claude Code
    evolve/SKILL.md             ← the /evolve entry point (supervisor role)
    explorer/SKILL.md
    reviewer/SKILL.md
docs/
    skills.md                   ← registration + invocation guide
    examples.md                 ← walkthrough of the bundled demo + how to modify it
    manifest.md                 ← field-by-field agent-evolve.yaml reference
examples/
    agent-evolve.yaml
    evolve-graph.mmd            ← sample Mermaid output
    evolve-report.html          ← sample interactive D3 report
    demo_run.py                 ← end-to-end pipeline demo
    demo-report.html            ← report generated by demo_run.py
tests/
    test_backends.py  test_equivalence.py  test_eval_runner.py
    test_scope.py     test_viz.py          test_config.py
```

## References

- [`gh-evolve`](https://github.com/kaiwong-sapiens/gh-evolve) — original
  inspiration; this project extends its protocol with a platform-agnostic
  backend and supervisor/reviewer agents
- [AlphaEvolve](https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)
  — the academic inspiration for evolutionary code search
- [`hypothesis`](https://hypothesis.readthedocs.io/) — property-based testing
  powering the equivalence checker
