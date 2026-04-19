# Skills — registration and usage

agent-evolve ships three **skills** that drive the evolutionary loop:

| Skill | Slash command | Role |
|---|---|---|
| `supervisor` | `/supervisor` | Orchestrates rounds, picks operators, gates with the reviewer, opens the final PR. |
| `explorer` | `/explorer` | Produces one candidate per slot. Writes hypothesis, codes inside the scope manifest. |
| `reviewer` | `/reviewer` | APPROVE / REQUEST_CHANGES / REJECT with an itemised checklist. |

All three live under [`.claude/skills/`](../.claude/skills/) as plain Markdown
files with YAML frontmatter — the layout Claude Code expects.

---

## Registering the skills

### With Claude Code (zero config)

Open the repo in Claude Code. That's it. Claude Code auto-discovers every
`SKILL.md` under `.claude/skills/` on startup and watches the directory for
live edits. No `settings.json` entry is required.

Confirm discovery:

```
/supervisor   <Tab>   # should autocomplete the three agent-evolve skills
```

If it doesn't, run Claude Code from the repo root so it picks up the project
`.claude/` directory, or check that you are on a version that supports
skill discovery.

### Making them available globally

If you want the skills available outside this repo:

```bash
# Symlink (Linux / macOS / WSL)
ln -s "$(pwd)/.claude/skills/supervisor" ~/.claude/skills/agent-evolve-supervisor
ln -s "$(pwd)/.claude/skills/explorer"   ~/.claude/skills/agent-evolve-explorer
ln -s "$(pwd)/.claude/skills/reviewer"   ~/.claude/skills/agent-evolve-reviewer

# Windows (PowerShell, run as admin)
New-Item -ItemType SymbolicLink -Path "$HOME\.claude\skills\agent-evolve-supervisor" -Target "$PWD\.claude\skills\supervisor"
# ...one per skill
```

The directory *name* under `~/.claude/skills/` becomes the slash command, so
renaming to `agent-evolve-supervisor` gives you `/agent-evolve-supervisor`
without colliding with any per-project skill of the same name.

### With another agent runner

The skills are plain prompts — nothing Claude-Code-specific except the
frontmatter format. Any runner that can load a system prompt from a file
and expose the Python tooling as tools can drive them:

```python
supervisor_prompt = Path(".claude/skills/supervisor/SKILL.md").read_text()
runner.run(system=supervisor_prompt, manifest="agent-evolve.yaml")
```

---

## Invoking the skills

### Start a run

```
/supervisor examples/agent-evolve.yaml
```

The supervisor skill loads its `SKILL.md` into context, reads the manifest,
and starts driving rounds. It will invoke `/explorer` and `/reviewer`
internally (or spawn them as subagents via the `Agent` tool for parallel
execution — this is the preferred mode when multiple candidates per round
would otherwise run sequentially).

### Test an individual skill

You can invoke each skill directly for iteration:

```
/explorer candidate-3 operator=mutate parent=candidate-1
/reviewer candidate-3
```

These are useful for:
- Sanity-checking a new SKILL.md edit without running a full evolution.
- Reproducing a reviewer verdict outside the evolution loop.
- Hand-running a single explore step when you don't trust the supervisor's
  operator heuristic for a particular situation.

### Auto-invocation by Claude

Each skill's frontmatter contains a `description` that tells Claude when the
skill is relevant. If you describe a task that matches — "help me optimise
this calculator under a logic-equivalence constraint" — Claude may invoke
`/supervisor` itself rather than waiting for the explicit slash command.
Disable with `disable-model-invocation: true` in the frontmatter if you want
only manual invocation.

---

## Frontmatter reference

The three SKILL.md files use the minimal Claude Code frontmatter:

```yaml
---
name: supervisor
description: <one-paragraph description of the skill's role>
---
```

Optional fields you may add if you fork the skills:

| Field | Purpose |
|---|---|
| `when_to_use` | Extra trigger phrases / context for auto-invocation. |
| `user-invocable` | `false` hides the skill from the `/` menu. Default `true`. |
| `disable-model-invocation` | `true` prevents Claude from auto-invoking. Default `false`. |
| `allowed-tools` | Space-separated whitelist, e.g. `"Read Grep Bash"`. |
| `argument-hint` | Shown in the slash-command autocomplete. |

See the [Claude Code skills docs](https://code.claude.com/docs/en/skills.md)
for the full list.

---

## How the three skills cooperate

```
user: /supervisor agent-evolve.yaml
 │
 ▼
supervisor (reads manifest, opens Issue or local problem, round 1 begins)
 │
 ├─► spawns /explorer × candidates_per_round (parallel via Agent tool)
 │     each explorer:
 │        1. reads parent candidate + EVOLVE_STATE
 │        2. writes hypothesis
 │        3. codes inside scope.target_files only
 │        4. commits to evolve/<problem>/candidate-<n>
 │
 ├─► supervisor: for each returned candidate
 │        scope.enforce_scope(diff, spec.scope)
 │        eval.run_eval(spec.eval_command)
 │        equivalence.check_equivalence(baseline, candidate)  # runtime mode
 │        backend.score_candidate(id, metrics, equivalence=report)
 │
 ├─► spawns /reviewer per scored candidate (sequential — reviewer reads state)
 │     reviewer returns VERDICT + CHECKLIST + CONFIDENCE
 │     backend.record_verdict(id, verdict)
 │
 ├─► supervisor: prune Pareto-inferior candidates
 │               regenerate Mermaid + HTML report
 │               backend.update_graph(...)
 │
 │  ...repeat for spec.evolution.rounds...
 │
 ▼
supervisor: backend.finalize(winner_id)
            → opens PR against main, NEVER merges
            → returns PR URL
stop
```

The supervisor never merges — that invariant is enforced by the Python layer
(`assert_no_merge` + `__init_subclass__` guard on `EvolveBackend`). A human
reviews and merges the final PR.

---

## Editing and extending the skills

SKILL.md files are plain Markdown — edit them like any other prompt. A few
project-specific conventions to preserve:

- **The "Prime directives" block**: four non-negotiables for the supervisor
  (never merge, never override scope, never skip reviewer, stop on
  `finalize`). Changing these weakens the safety contract.
- **The reviewer's checklist format**: a fixed list of `pass`/`fail` items.
  The supervisor parses this to know whether to accept the candidate; adding
  a free-form "verdict" instead breaks the pipeline.
- **Operator semantics**: `mutate` / `crossover` / `explore` map to specific
  behaviours in the explorer skill. Adding a new operator means updating the
  supervisor's dispatch logic and the explorer's guidance.

If you fork the skills for a different optimisation target, keep the
frontmatter `name` field — the Python tooling doesn't read the skills
directly, but Claude Code does, and collisions break slash-command
dispatch.

---

## Troubleshooting

**Slash command not found.** Check `.claude/skills/<name>/SKILL.md` exists,
restart Claude Code from the repo root, and confirm your Claude Code version
supports skill discovery.

**Skill runs but uses wrong tooling.** Each SKILL.md names the Python
functions it invokes (e.g. `backend.score_candidate`). If your fork renames
them, update the SKILL.md to match or the skill will reference dead symbols.

**Supervisor opens a PR that claims it "merged".** The Python layer prevents
merge by hardcoding `agents_can_merge=False` on the base class and raising
`TypeError` if any subclass overrides. If you see merge behaviour, the skill
is mis-reporting — the actual merge is blocked by the backend.

**Auto-invocation fires on unrelated tasks.** Tighten the
`description` / `when_to_use` in the frontmatter to name the *conditions*
under which the skill should trigger, not just what it does.
