# Skills — registration and usage

> **What this guide is for.** agent-evolve is driven by Claude, not by you.
> The three `SKILL.md` files under [`.claude/skills/`](../.claude/skills/)
> are prompts that Claude reads when you ask it to run the evolutionary
> loop. This guide tells you (a human) what to say to Claude so those
> skills load, fire, and cooperate correctly. Commands shown as
> ````bash ...```` blocks can be run directly, but the idiomatic path is
> to ask Claude to run them — that way Claude sees the output and can
> react.

agent-evolve ships three skills:

| Skill | Slash command | Role when Claude runs it |
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
live edits — no `settings.json` entry required.

Confirm discovery by typing in Claude Code:

```
/supervisor   <Tab>
```

If the three skills do not autocomplete, tell Claude:

> "The skills under `.claude/skills/` are not showing up as slash commands.
>  Check that I opened Claude Code from the repo root and that my version
>  supports skill discovery. Fix or tell me what to change."

### Making them available globally

If you want `/supervisor` available in any project you open with Claude
Code, ask Claude to install the skills into your user-level directory:

> "Install the agent-evolve skills globally by symlinking `.claude/skills/*`
>  into `~/.claude/skills/` with an `agent-evolve-` prefix."

Claude will run something like:

```bash
ln -s "$(pwd)/.claude/skills/supervisor" ~/.claude/skills/agent-evolve-supervisor
ln -s "$(pwd)/.claude/skills/explorer"   ~/.claude/skills/agent-evolve-explorer
ln -s "$(pwd)/.claude/skills/reviewer"   ~/.claude/skills/agent-evolve-reviewer
```

On Windows, Claude will use `New-Item -ItemType SymbolicLink` from an
elevated PowerShell. The directory *name* becomes the slash command, so the
`agent-evolve-` prefix avoids collisions with per-project `/supervisor`
skills in unrelated repos.

### With another agent runner

The skills are plain prompts. Any runner that can load a system prompt from
a file and expose the Python tooling as tools can drive them. Ask Claude:

> "I want to run the agent-evolve supervisor from [my custom runner]. Load
>  `.claude/skills/supervisor/SKILL.md` as the system prompt, expose
>  `agent_evolve.backends.LocalBackend`, `agent_evolve.eval.*`, and
>  `agent_evolve.scope.*` as tools, and point it at `agent-evolve.yaml`."

Claude will adapt the wiring to whatever runner you're using.

---

## Invoking the skills

### Start a run

Tell Claude in natural language:

> "Run the evolutionary search on my manifest at `agent-evolve.yaml`."

Or invoke the slash command directly:

```
/supervisor agent-evolve.yaml
```

Claude loads the supervisor skill, reads the manifest, and begins the loop.
It will invoke `/explorer` and `/reviewer` internally — or spawn them as
subagents via the `Agent` tool when multiple candidates in a round can run
in parallel.

### Test an individual skill

Useful for iterating on a SKILL.md edit without running a full evolution.
Tell Claude:

> "Invoke `/explorer` directly on candidate-3 with operator=mutate and
>  parent=candidate-1. Don't run the full supervisor."

Or manually:

```
/explorer candidate-3 operator=mutate parent=candidate-1
/reviewer candidate-3
```

Individual-skill invocation is useful for:

- Sanity-checking a new SKILL.md edit without running a full evolution.
- Reproducing a reviewer verdict outside the evolution loop.
- Hand-running a single explore step when you don't trust the supervisor's
  operator heuristic for a particular case.

### Auto-invocation by Claude

Each skill's frontmatter contains a `description` that tells Claude when the
skill is relevant. You can describe a task in natural language and Claude
will choose the right skill automatically:

> "Make `src/pricing/calculator.py` faster while keeping all pricing tests
>  green. Run a bounded evolutionary search — three rounds, two candidates
>  per round."

Claude matches this to `/supervisor`'s description and fires without waiting
for an explicit slash command.

To disable auto-invocation on a skill you only want triggered manually, add
`disable-model-invocation: true` to its frontmatter.

---

## Frontmatter reference

The three SKILL.md files ship with the minimal Claude Code frontmatter:

```yaml
---
name: supervisor
description: <one-paragraph description of the skill's role>
---
```

Optional fields you (or Claude, on your instruction) may add:

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

This is what Claude does after you type `/supervisor agent-evolve.yaml`:

```
/supervisor agent-evolve.yaml
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

Claude never merges the final PR — that invariant is enforced by the Python
layer (`assert_no_merge` + `__init_subclass__` guard on `EvolveBackend`),
not by a rule in the skill prompt. You review the PR and merge it yourself.

---

## Editing and extending the skills

SKILL.md files are plain Markdown — ask Claude to edit them like any other
prompt:

> "In `.claude/skills/reviewer/SKILL.md`, add a new checklist item for
>  `dependency_unchanged`. The reviewer should fail if the candidate's diff
>  changes `pyproject.toml` or `uv.lock`."

A few project-specific conventions to preserve when forking:

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

**Slash command not found.** Ask Claude: *"The `/supervisor` slash command
is not appearing. Check `.claude/skills/supervisor/SKILL.md` exists, that
the frontmatter parses as valid YAML, and that my Claude Code version
supports skill discovery."* Claude will diagnose and tell you what to fix.

**Skill runs but uses wrong tooling.** Each SKILL.md names the Python
functions it invokes (e.g. `backend.score_candidate`). If you or Claude
renames one of those in the Python layer, the skill will reference dead
symbols. Tell Claude: *"I renamed `score_candidate`. Update every SKILL.md
that references it."*

**Supervisor opens a PR that claims it "merged".** The Python layer
prevents merge by hardcoding `agents_can_merge=False` on the base class and
raising `TypeError` if any subclass overrides. If you see merge *behaviour*,
the skill is mis-reporting in its summary — ask Claude to correct the
summary. The actual merge is blocked by the backend regardless.

**Auto-invocation fires on unrelated tasks.** Tell Claude: *"The supervisor
skill is auto-invoking on tasks that aren't evolutionary searches. Tighten
the `description` and `when_to_use` frontmatter to name the conditions more
specifically."*
