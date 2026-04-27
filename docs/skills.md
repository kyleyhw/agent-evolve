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
| `evolve` | `/evolve` | Entry point. Accepts natural language or a manifest path. Plays the supervisor role — orchestrates rounds, picks operators, gates with the reviewer, opens the final PR. |
| `explorer` | `/explorer` | Produces one candidate per slot. Writes hypothesis, codes inside the scope manifest. |
| `reviewer` | `/reviewer` | APPROVE / REQUEST_CHANGES / REJECT with an itemised checklist. |

All three live under [`.claude/skills/`](../.claude/skills/) as plain Markdown
files with YAML frontmatter — the layout Claude Code expects.

For one-off usage you normally only interact with `/evolve`; it fires the
other two automatically. `/explorer` and `/reviewer` are exposed for
debugging and individual-step testing.

---

## Registering the skills

### One-shot install (recommended)

Open Claude Code in any repo and tell Claude:

> "Install agent-evolve from https://github.com/kyleyhw/agent-evolve"

Claude clones the repo into a sensible location and runs `install.py`,
which symlinks every skill directory into `~/.claude/skills/` (falling
back to a copy on Windows without admin rights) and installs the Python
package as a `uv` tool. After that, `/evolve`, `/explorer`, and
`/reviewer` are available in any repo you open with Claude Code.

If you already have the repo cloned, the equivalent manual command from
its root is:

```bash
uv run python install.py
```

Re-run the prompt with "force overwrite" (or pass `--force` to the manual
command) to overwrite an existing skill directory under
`~/.claude/skills/`.

### Per-project only (no user-scope install)

If you want the skills available only inside this repo — not globally —
skip `install.py` entirely. Claude Code auto-discovers every `SKILL.md`
under `.claude/skills/` when the repo is open, with no extra registration
step.

Confirm discovery:

```
/evolve   <Tab>
```

If the three skills do not autocomplete, tell Claude:

> "The skills under `.claude/skills/` are not showing up as slash commands.
>  Check that I opened Claude Code from the repo root and that my version
>  supports skill discovery. Fix or tell me what to change."

### Collisions with other projects

If your `~/.claude/skills/` already contains a skill named `evolve`,
`explorer`, or `reviewer` from an unrelated project, the installer will
warn and skip rather than overwrite. Re-run with `--force` to take
ownership, or rename the source directory (e.g. `.claude/skills/evolve` →
`.claude/skills/ae-evolve`) and update the `name:` frontmatter to match
before re-installing — the directory name becomes the slash command.

### With another agent runner

The skills are plain prompts. Any runner that can load a system prompt from
a file and expose the Python tooling as tools can drive them. Ask Claude:

> "I want to run agent-evolve from [my custom runner]. Load
>  `.claude/skills/evolve/SKILL.md` as the system prompt, expose
>  `agent_evolve.backends.LocalBackend`, `agent_evolve.eval.*`, and
>  `agent_evolve.scope.*` as tools, and point it at my target."

Claude will adapt the wiring to whatever runner you're using.

---

## Invoking the skills

### Start a run — natural language (recommended for ad-hoc use)

Just describe what you want:

> "Evolve `src/pricing/calculator.py` for runtime. Keep the pricing tests
>  green."

Or:

> "Fix the failing tests in `tests/graphs/` by evolving `src/graphs/dijkstra.py`.
>  Don't touch the tests themselves."

Claude matches the prose to `/evolve`'s `description` and fires without
waiting for a slash command. The skill infers the spec (mode, metrics,
eval command, scope) from your prose and asks only about genuine gaps.
**No YAML required.**

### Start a run — explicit manifest (for CI, reproducibility)

If you have an `agent-evolve.yaml` in the repo:

```
/evolve agent-evolve.yaml
```

Claude loads the manifest with `agent_evolve.config.load_manifest`, skips
inference, and begins the loop.

To capture a spec Claude inferred from NL for later reuse, ask:

> "Save the spec you just inferred as `agent-evolve.yaml`."

### Test an individual skill

Useful when iterating on a SKILL.md edit — no full evolution needed:

> "Invoke `/explorer` directly on candidate-3 with operator=mutate and
>  parent=candidate-1. Don't run the full evolve loop."

Or manually:

```
/explorer candidate-3 operator=mutate parent=candidate-1
/reviewer candidate-3
```

Individual-skill invocation is useful for:

- Sanity-checking a new SKILL.md edit without running a full evolution.
- Reproducing a reviewer verdict outside the evolution loop.
- Hand-running a single explore step when you don't trust `/evolve`'s
  operator heuristic for a particular case.

### Auto-invocation by Claude

Each skill's frontmatter contains a `description` that tells Claude when
the skill is relevant. Describing a task in natural language picks the
right skill automatically — you almost never need the explicit slash
command. To disable auto-invocation on a skill you only want triggered
manually, add `disable-model-invocation: true` to its frontmatter.

---

## Frontmatter reference

The three SKILL.md files ship with:

```yaml
---
name: evolve
description: <one-paragraph description of the skill's role>
argument-hint: "<natural-language goal> | path/to/agent-evolve.yaml"
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

This is what Claude does after you type `/evolve` (or after it auto-invokes
from natural language):

```
/evolve "evolve src/foo.py for speed"          (or: /evolve agent-evolve.yaml)
 │
 ▼
evolve skill
 │
 ├─► Phase 0: establish spec
 │     — load agent-evolve.yaml via agent_evolve.config.load_manifest, OR
 │     — infer ProblemSpec from natural language, asking only about gaps
 │       (usually just the eval command)
 │     — offer to save the inferred spec as agent-evolve.yaml
 │
 ├─► spawns /explorer × candidates_per_round (parallel via Agent tool)
 │     each explorer:
 │        1. reads parent candidate + EVOLVE_STATE
 │        2. writes hypothesis
 │        3. codes inside scope.target_files only
 │        4. commits to evolve/<problem>/candidate-<n>
 │
 ├─► for each returned candidate:
 │        scope.enforce_scope(diff, spec.scope)
 │        eval.run_eval(spec.eval_command)
 │        equivalence.check_equivalence(baseline, candidate)  # runtime mode
 │        backend.score_candidate(id, metrics, equivalence=report)
 │
 ├─► spawns /reviewer per scored candidate (sequential — reviewer reads state)
 │     reviewer returns VERDICT + CHECKLIST + CONFIDENCE
 │     backend.record_verdict(id, verdict)
 │
 ├─► prune Pareto-inferior candidates
 │   regenerate Mermaid + HTML report
 │   backend.update_graph(...)
 │
 │  ...repeat for spec.evolution.rounds...
 │
 ▼
backend.finalize(winner_id)
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

- **The "Prime directives" block**: four non-negotiables for `/evolve`
  (never merge, never override scope, never skip reviewer, stop on
  `finalize`). Changing these weakens the safety contract.
- **The reviewer's checklist format**: a fixed list of `pass`/`fail` items.
  `/evolve` parses this to know whether to accept the candidate; adding a
  free-form "verdict" instead breaks the pipeline.
- **Operator semantics**: `mutate` / `crossover` / `explore` map to specific
  behaviours in the explorer skill. Adding a new operator means updating
  `/evolve`'s dispatch logic and the explorer's guidance.

If you fork the skills for a different optimisation target, keep the
frontmatter `name` field — the Python tooling doesn't read the skills
directly, but Claude Code does, and collisions break slash-command
dispatch.

---

## Troubleshooting

**Slash command not found.** Ask Claude: *"The `/evolve` slash command is
not appearing. Check `.claude/skills/evolve/SKILL.md` exists, that the
frontmatter parses as valid YAML, and that my Claude Code version supports
skill discovery."* Claude will diagnose and tell you what to fix.

**Install failed on Windows — symlink refused.** The installer falls back
to a full copy when symlinks require admin rights. That works, but edits
to the SKILL.md in the repo won't propagate to `~/.claude/skills/`. Enable
Windows Developer Mode or run an elevated shell, delete the copied
directories, and re-run `install.py` to get proper symlinks.

**Skill runs but uses wrong tooling.** Each SKILL.md names the Python
functions it invokes (e.g. `backend.score_candidate`). If you or Claude
renames one of those in the Python layer, the skill will reference dead
symbols. Tell Claude: *"I renamed `score_candidate`. Update every SKILL.md
that references it."*

**`/evolve` opens a PR that claims it "merged".** The Python layer prevents
merge by hardcoding `agents_can_merge=False` on the base class and raising
`TypeError` if any subclass overrides. If you see merge *behaviour*, the
skill is mis-reporting in its summary — ask Claude to correct the
summary. The actual merge is blocked by the backend regardless.

**Auto-invocation fires on unrelated tasks.** Tell Claude: *"The `/evolve`
skill is auto-invoking on tasks that aren't evolutionary searches. Tighten
the `description` and `when_to_use` frontmatter to name the conditions more
specifically."*
