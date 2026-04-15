# agent-evolve — Project Plan

## What This Is

`agent-evolve` is an AI-powered evolutionary optimization system for codebases. It lets AI agents
cooperate to iteratively explore and evolve better solutions to a defined problem — whether that
means improving algorithmic correctness, reducing runtime, or refactoring for clarity.

Inspired by [`gh-evolve`](https://github.com/kaiwong-sapiens/gh-evolve), which pioneered the idea
of using GitHub Issues and Pull Requests as a shared state machine for multi-agent evolutionary
search. `agent-evolve` extends this with:

- A platform-agnostic backend (not locked to GitHub)
- Module-scoped deployments (evolve one part of a codebase without touching others)
- Supervisor + reviewer agents (automated oversight before any change is accepted)
- Runtime optimization mode (make code faster while verifying logic is unchanged)
- A structured skill layer (SKILL.md files) + a lightweight execution layer (Python tooling)
- Branch-based workflow — agents never touch `main`; all changes require human approval to merge
- Visual evolution graph — auto-generated after each round showing the full search tree

---

## Objectives

1. **Platform agnostic** — work with GitHub, GitLab, or a local filesystem backend
2. **Module scoped** — each evolution run targets a specific file or directory; nothing outside that
   scope can be modified
3. **Fully automated** — a supervisor agent orchestrates all rounds without manual intervention;
   a reviewer agent gates every candidate before it is accepted
4. **Dual optimization modes**:
   - *Algorithm mode*: optimise for correctness, performance, or any user-defined metric
   - *Runtime mode*: make code faster while guaranteeing logic equivalence via property-based testing
5. **Human approval gate** — agents never merge to `main`; evolution runs on isolated branches and
   the winning candidate is surfaced as a PR for human review and manual merge
6. **Visual evolution graph** — after each round, generate a visual representation of the search
   tree so the human reviewer can understand what was tried and why the winner was selected
7. **CV-quality code** — clean architecture, well documented, something you'd be proud to show

---

## Repository Structure

```
agent-evolve/
│
├── PLAN.md                        ← this file
├── README.md                      ← usage guide (write last)
├── agent-evolve.yaml              ← example per-module config
│
├── skills/                        ← SKILL.md prompt files (the "brain")
│   ├── supervisor/SKILL.md        ← orchestrates rounds
│   ├── explorer/SKILL.md          ← generates candidate solutions
│   └── reviewer/SKILL.md          ← reviews and approves/rejects candidates
│
├── src/                           ← Python execution layer (the "hands")
│   ├── backends/
│   │   ├── base.py                ← abstract Backend interface
│   │   ├── github.py              ← GitHub Issues + PRs backend
│   │   ├── gitlab.py              ← GitLab Issues + MRs backend
│   │   └── local.py               ← local filesystem backend (no remote needed)
│   ├── eval/
│   │   ├── runner.py              ← runs eval commands, captures metrics
│   │   └── equivalence.py         ← property-based logic equivalence checker
│   ├── sandbox/
│   │   └── docker_runner.py       ← isolated execution environment
│   ├── scope/
│   │   └── enforcer.py            ← validates candidates stay within manifest scope
│   └── viz/
│       ├── graph.py               ← builds graph data structure from Trait Matrix
│       ├── mermaid.py             ← renders Mermaid diagram (embeds in Issue/PR body)
│       └── html_report.py         ← generates standalone HTML report with D3.js tree
│
└── tests/
    ├── test_backends.py
    ├── test_equivalence.py
    └── test_scope.py
```

---

## The Two Layers

### Layer 1 — Skill files (markdown)

These are instruction documents that AI agents read at runtime. They define *how the agent thinks*
and *what protocol it follows*. No code execution happens inside a SKILL.md — they are pure
reasoning prompts.

Reference: [`gh-evolve`'s SKILL.md approach](https://github.com/kaiwong-sapiens/gh-evolve) is the
canonical example of this pattern. Read it before writing these.

**`skills/supervisor/SKILL.md`** — tells the supervisor agent how to:
- Read the current leaderboard and Trait Matrix
- Decide which operator to apply this round (mutate / crossover / explore)
- Dispatch tasks to explorer agents
- Collect scored results
- Trigger the reviewer agent
- Apply pruning (Pareto-inferior candidates are closed/archived)
- Call `src/viz/` to regenerate the Mermaid diagram and HTML report after each round
- Know when to finalize: open the final PR against `main` with full context, then stop — do not merge

**`skills/explorer/SKILL.md`** — tells explorer agents how to:
- Read the parent candidate and understand what made it good or bad
- Apply the assigned operator faithfully
- Write a structured hypothesis before generating code
- Embed the `EVOLVE_STATE` metadata block in the candidate
- Respect the module scope manifest (never touch out-of-scope files)

**`skills/reviewer/SKILL.md`** — tells the reviewer agent how to:
- Read the original and candidate versions side by side
- In algorithm mode: check for scope violations, test coverage, metric improvement
- In runtime mode: check control flow, return values, side effects, edge cases
- Produce a structured verdict: `APPROVE / REQUEST_CHANGES / REJECT` with explicit reasoning
- In runtime mode: confirm property-based tests passed before approving

### Layer 2 — Python tooling (code)

These are real executable scripts that the agents invoke via tool calls. They handle things that
can't be done by reasoning alone.

**`src/backends/base.py`**

Define the abstract interface every backend must implement:

```python
class EvolveBackend(ABC):
    def create_problem(self, spec: ProblemSpec) -> str: ...
    def submit_candidate(self, candidate: Candidate) -> str: ...
    def score_candidate(self, candidate_id: str, metrics: dict) -> None: ...
    def get_leaderboard(self) -> list[Candidate]: ...
    def prune(self, candidate_id: str, reason: str) -> None: ...
    def finalize(self, winner_id: str) -> None: ...
    # finalize() does NOT merge. It:
    #   1. Closes all non-winning branches/PRs with a summary comment
    #   2. Opens a final PR from the winning branch → main
    #   3. Attaches the full Trait Matrix, evolution graph, and reviewer verdict
    #   4. Leaves the PR open — a human must approve and merge it
```

**`src/backends/github.py`**

Implement the above using:
- GitHub Issues as the problem root + leaderboard (same as `gh-evolve`)
- PRs as candidates with `EVOLVE_STATE` JSON in a hidden `<details>` block
- GitHub API via the `gh` CLI or `PyGithub`

**`src/backends/local.py`**

Implement the above using:
- A local `evolve-state/` directory with JSON files
- No remote needed — useful for offline runs and testing
- Structure: `evolve-state/{problem_id}/candidates/{id}.json`

**`src/eval/runner.py`**

- Accepts an eval command string from the manifest
- Executes it in the sandbox
- Parses stdout for structured metrics (JSON format preferred)
- Returns `{ score, metrics, passed, stdout, duration_ms }`

**`src/eval/equivalence.py`**

Used in runtime optimization mode only. Given an original function and an optimized function:
- Auto-generates property-based tests using `hypothesis`
- Runs hundreds of random inputs through both versions
- Asserts outputs are identical
- Returns a structured report: `{ equivalent: bool, counterexample, coverage }`

```python
def check_equivalence(original_fn, optimized_fn, strategy) -> EquivalenceReport: ...
```

**`src/scope/enforcer.py`**

- Reads the `agent-evolve.yaml` manifest
- Diffs a candidate against the current codebase
- Rejects the candidate if any modified file is outside the declared scope
- Returns `{ in_scope: bool, violations: list[str] }`

---

## Branching Model

Agents are **never permitted to commit to or merge into `main`**. All evolution work happens on
isolated branches. `main` is only ever changed by a human manually approving and merging the final
PR.

### Branch naming convention

```
evolve/{problem-id}/{candidate-id}

e.g.
evolve/42/candidate-1      ← round 1 baseline
evolve/42/candidate-2      ← round 2 mutate of candidate-1
evolve/42/candidate-3      ← round 2 explore (new direction)
evolve/42/candidate-4      ← round 3 crossover of 2 + 3  ← winner
```

### Lifecycle

```
main (protected — agents cannot touch this)
│
│  agents work here only ↓
├── evolve/42/candidate-1    scored, pruned (Pareto-inferior), branch archived
├── evolve/42/candidate-2    scored, pruned
├── evolve/42/candidate-3    scored, approved by reviewer
└── evolve/42/candidate-4    scored, approved by reviewer ← WINNER
                                      ↓
                             finalize() opens:
                             evolve/42/candidate-4 → main   (PR #99)
                             PR is left OPEN, awaiting human approval
```

### Branch protection config (add to `agent-evolve.yaml`)

```yaml
safety:
  protected_branch: main
  agents_can_merge: false          # hard no — enforced in all backend implementations
  require_human_approval: true
  final_pr_reviewers:
    - your-github-username         # who gets pinged when finalize() opens the PR
```

The `agents_can_merge: false` constraint must be enforced in code in every backend adapter, not
just relied on as a policy. The GitHub backend should additionally configure a branch protection
rule via the API to ensure this holds even if the code is modified.

### What the final PR contains

When `finalize()` is called, the winning PR opened against `main` must include:

- A plain-English summary of what was changed and why it was selected as the winner
- The full Trait Matrix showing all candidates tried across all rounds
- The reviewer's verdict with its full checklist
- The evolution graph (as a Mermaid diagram inline, plus a link to the HTML report)
- A diff summary showing exactly what files changed versus `main`

The human reviewer reads this, understands the full evolution history, and decides whether to
merge, request further changes, or abandon the run entirely.

---

## Visualization

After every round, the supervisor generates two representations of the search tree. These give the
human reviewer (and your CV audience) a clear picture of the evolutionary process.

### 1. Mermaid diagram (embedded in Issue / problem.json)

Rendered natively by GitHub in Issue bodies. Updated after every round by the supervisor.

```
graph TD
    ROOT["Problem #42\nOptimise pricing calculator"]

    C1["candidate-1\noperator: explore\nR1 | 120ms ✓\nstatus: pruned"]
    C2["candidate-2\noperator: mutate\nR2 | 88ms ✓\nstatus: active"]
    C3["candidate-3\noperator: explore\nR2 | 95ms ✓\nstatus: active"]
    C4["candidate-4 ⭐\noperator: crossover\nR3 | 61ms ✓\nstatus: WINNER"]

    ROOT --> C1
    C1 --> C2
    C1 --> C3
    C2 --> C4
    C3 --> C4

    style C1 fill:#888,color:#fff
    style C4 fill:#2d8a4e,color:#fff
```

Node colour codes: green = winner, grey = pruned, blue = active, red = rejected.

Implemented in `src/viz/mermaid.py`. The supervisor calls this after scoring each round and
updates the Issue body (or `problem.json` for the local backend).

### 2. Static HTML report (standalone file)

Generated by `src/viz/html_report.py` at the end of each round. Produces a single self-contained
`evolve-report.html` file using D3.js (loaded from CDN — no build step).

Features:
- Interactive force-directed tree — drag nodes, zoom in/out
- Click any node to expand its full `EVOLVE_STATE` (metrics, hypothesis, conclusion, verdict)
- Metric sparklines on each node showing score over generations
- Timeline view toggle — see the search tree laid out by round instead of by lineage
- Export as PNG button (for CVs, presentations, README screenshots)

The report is committed to the repo root on each round so it's always viewable without running
anything. For the GitHub backend it is also attached as a PR comment on the final winning PR.

### Visualization module — `src/viz/`

```python
# graph.py — backend-agnostic; builds the graph from Trait Matrix data
def build_graph(trait_matrix: list[Candidate]) -> EvolutionGraph: ...

# mermaid.py — renders Mermaid markdown string
def render_mermaid(graph: EvolutionGraph) -> str: ...

# html_report.py — renders standalone HTML file with embedded D3.js
def render_html(graph: EvolutionGraph, output_path: str) -> None: ...
```

The graph data structure is backend-agnostic — both renderers consume the same `EvolutionGraph`
object, so adding new output formats (e.g. a Discord embed, a PNG via matplotlib) is just another
renderer.

---



Each module or subsection of the codebase gets its own manifest. Place this file in the directory
you want to evolve.

```yaml
# agent-evolve.yaml
version: 1

problem:
  description: "Optimise the order pricing calculator for speed and accuracy"
  mode: runtime              # or: algorithm
  eval_command: "pytest tests/pricing/ --benchmark-json=benchmark.json"
  metrics:
    - name: duration_ms
      optimise: minimize
    - name: test_pass_rate
      optimise: maximize
      minimum: 1.0           # hard constraint — must be 100%

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
  prune_strategy: pareto

runtime_mode:
  equivalence_check: required          # must pass before reviewer sees candidate
  property_test_samples: 500
  regression_tests: "pytest tests/pricing/ -x"

backend:
  type: github                         # github | gitlab | local
  repo: your-org/your-repo            # for github/gitlab backends
```

---

## EVOLVE_STATE Block

Every candidate embeds this hidden JSON block (inside an HTML `<details>` tag in a PR description
or a JSON file in the local backend). The supervisor and reviewer read this to understand lineage.

```json
{
  "evolve_version": "1",
  "problem_id": "42",
  "candidate_id": "7",
  "parent_ids": ["3", "5"],
  "operator": "crossover",
  "round": 3,
  "status": "pending",
  "metrics": {
    "duration_ms": 42.1,
    "test_pass_rate": 1.0
  },
  "hypothesis": "Combining the vectorised loop from #3 with the cache layer from #5",
  "conclusion": null,
  "equivalence_report": {
    "equivalent": true,
    "samples_tested": 500,
    "counterexample": null
  },
  "reviewer_verdict": null
}
```

Status lifecycle: `pending → scored → reviewing → approved | rejected | pruned`

---

## Trait Matrix

The supervisor maintains this table in the problem root (Issue body or `problem.json`). It is the
shared memory across all agents and rounds.

| ID | Parent | Operator | Round | duration_ms | test_pass_rate | Status |
|----|--------|----------|-------|-------------|----------------|--------|
| 1  | —      | explore  | 1     | 120.4       | 1.0            | pruned |
| 2  | 1      | mutate   | 2     | 88.2        | 1.0            | active |
| 3  | 1      | explore  | 2     | 95.1        | 1.0            | active |
| 4  | 2,3    | crossover| 3     | 61.0        | 1.0            | active ← best |

---

## Reviewer Agent Protocol

The reviewer is the most critical component. It is the last gate before a candidate is accepted.

**For algorithm mode**, the reviewer checks:
- [ ] All files modified are within the declared scope
- [ ] Eval command passes with equal or better metrics than the current best
- [ ] Test coverage has not decreased
- [ ] No regressions in adjacent modules (run a broader test suite as a smoke check)
- [ ] The candidate's hypothesis and conclusion are coherent and honest

**For runtime mode**, the reviewer additionally checks:
- [ ] Equivalence report is present and shows `equivalent: true`
- [ ] At least 500 property-based test samples were run
- [ ] Control flow is structurally identical (no new branches, no removed error handling)
- [ ] No new side effects introduced (no new writes, network calls, or global state mutations)
- [ ] The performance improvement is real and not a measurement artifact

Reviewer verdict format:
```
VERDICT: APPROVE | REQUEST_CHANGES | REJECT
REASON: <one paragraph>
CHECKLIST: <itemised pass/fail for each check above>
CONFIDENCE: high | medium | low
```

---

## Build Order

Build in this sequence. Each phase is usable before the next begins.

### Phase 1 — Foundations
- [ ] Write `src/backends/base.py` (abstract interface, including `agents_can_merge: false` enforcement)
- [ ] Write `src/backends/local.py` (local filesystem backend — no API keys needed to test)
- [ ] Write `src/scope/enforcer.py` (scope validation)
- [ ] Write `agent-evolve.yaml` example config (include `safety:` block)
- [ ] Test: run scope enforcer against a sample diff

### Phase 2 — Skill files
- [ ] Write `skills/explorer/SKILL.md` (reference [`gh-evolve` SKILL.md](https://github.com/kaiwong-sapiens/gh-evolve) for format and protocol)
- [ ] Write `skills/reviewer/SKILL.md`
- [ ] Write `skills/supervisor/SKILL.md` (include branching protocol: create `evolve/` branches, never commit to `main`)
- [ ] Test: manually run a single round using the local backend and the explorer skill

### Phase 3 — Eval + Equivalence
- [ ] Write `src/eval/runner.py`
- [ ] Write `src/eval/equivalence.py` using `hypothesis`
- [ ] Test: run equivalence check on a known-correct and known-broken optimisation pair

### Phase 4 — Visualization
- [ ] Write `src/viz/graph.py` (EvolutionGraph data structure)
- [ ] Write `src/viz/mermaid.py` (Mermaid renderer)
- [ ] Write `src/viz/html_report.py` (D3.js HTML report)
- [ ] Test: generate a report from a mock Trait Matrix; verify Mermaid renders on GitHub

### Phase 5 — GitHub Backend
- [ ] Write `src/backends/github.py`
- [ ] Configure branch protection rule via GitHub API in `create_problem()`
- [ ] Implement `finalize()` to open PR against `main` (not merge it) with full evolution context
- [ ] Test: run a full round end-to-end on a real GitHub repo; confirm `main` is untouched

### Phase 6 — Sandbox
- [ ] Write `src/sandbox/docker_runner.py`
- [ ] Test: run eval command inside Docker, confirm isolation

### Phase 7 — Polish
- [ ] Write `README.md` (include a screenshot of the HTML evolution graph — it's a great CV asset)
- [ ] Add `tests/` coverage for all `src/` modules
- [ ] Add GitLab backend (`src/backends/gitlab.py`)

---

## Key References

- [`gh-evolve`](https://github.com/kaiwong-sapiens/gh-evolve) — the original inspiration; read the
  SKILL.md and README carefully before writing any skill files here
- [`hypothesis`](https://hypothesis.readthedocs.io/) — property-based testing library for the
  equivalence checker
- [AlphaEvolve paper](https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)
  — the academic inspiration behind the evolutionary search approach
- [`PyGithub`](https://pygithub.readthedocs.io/) — Python GitHub API client for the GitHub backend
- [D3.js](https://d3js.org/) — used in `html_report.py` for the interactive evolution tree; load
  from CDN so the output HTML is self-contained with no build step
- [Mermaid](https://mermaid.js.org/) — diagram syntax that renders natively in GitHub Issue and PR
  bodies; used for the inline search graph

---

## Notes for the Agent

- Start with Phase 1. Do not skip ahead to the GitHub backend before the local backend works.
- Every `src/` file should have a corresponding test in `tests/`.
- The SKILL.md files are the most important deliverable — spend time on them. A well-written
  SKILL.md is worth more than 300 lines of Python.
- **Branching is non-negotiable.** Every backend must enforce `agents_can_merge: false`. This is
  not a config option that can be overridden at runtime. Hardcode the check in `finalize()`.
- **`main` is sacred.** The supervisor SKILL.md must explicitly state: "You do not have permission
  to commit to, push to, or merge into `main` under any circumstances. Your job ends when the final
  PR is open."
- In runtime mode, equivalence checking is **non-negotiable**. If the equivalence check cannot
  run (e.g. the function has external dependencies that can't be mocked), the reviewer must
  REJECT the candidate regardless of performance gains.
- The HTML evolution graph is a first-class deliverable — it should look polished. It will appear
  in the README and on a CV. Use D3.js loaded from CDN so the file is self-contained with no build
  step required.
- Keep the `agent-evolve.yaml` schema simple. Every field should have a sensible default so that
  a minimal config is just 5 lines.