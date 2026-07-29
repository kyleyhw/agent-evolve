# Test report — per-candidate worktree isolation

**Date**: 2026-07-29
**Scope**: `worktree.py` (new), `eval_cwd` tree-relative resolution
(`models.py`, `config.py`), `run_eval` scratch export + env merge
(`eval/runner.py`), and the `git_repo` conftest fixture they build on.
**Runtime**: full suite **175 passed in 20.74 s**; the new
`test_worktree.py` alone runs in ≈17 s (each test initialises a real git
repository and spawns several git subprocesses — the cost is real I/O,
not slow assertions).

## What was done and why

Three code changes close one defect — N parallel explorers previously
shared a single working directory — so the tests target the three
isolation layers separately:

1. **Repository paths** (`test_worktree.py`, 13 tests): does each
   candidate get a distinct, correctly anchored tree, and does the
   lifecycle survive crash-resume?
2. **Eval working directory** (`test_config.py`, +4 tests): can a
   manifest still point all candidates at one shared directory through
   `eval_cwd`?
3. **Out-of-tree writes** (`test_eval_runner.py`, +3 tests): does the
   per-candidate scratch contract reach the child process, and does a
   partial `env` no longer destroy the child environment?

All tests run against **real git repositories** built by the new
`git_repo` fixture (no mocking of git): the subject under test is git's
actual worktree behaviour, so a mocked `subprocess.run` would test the
mock. The fixture pins the branch name (`init -b main`), identity, and
`commit.gpgsign false` so machine-global git config cannot alter
outcomes.

## What was specifically tested, and why those inputs

### `tests/test_worktree.py`

| Test | Input rationale |
|---|---|
| `test_create_remove_round_trip` | Production-shaped branch `evolve/p1/candidate-1`; asserts tree content, branch checkout, HEAD == anchor SHA, registration, then full removal. |
| `test_recreate_is_idempotent` | Marker file written between two identical calls — proves resume returns the tree *untouched* rather than re-creating it. |
| `test_two_worktrees_are_isolated` | The defect scenario itself: two candidates; a write and a commit in tree A must be invisible in tree B, and B must remain at the anchor. |
| `test_slashed_branch_names_slugified_flat` | Branch names contain `/` by construction in this system — the norm, not an edge case. Asserts a flat sibling directory and a verbatim branch name. |
| `test_dirty_tree_removal` | Untracked + modified files, because candidate trees are routinely dirty with build artifacts; removal must still succeed (`--force` escalation). |
| `test_submodule_auto_init` | Repo with a file-protocol submodule; `git worktree add` alone yields an *empty* submodule dir, so the test asserts the submodule's file exists in the new tree. `protocol.file.allow=always` is set because git ≥ 2.38.1 blocks file-protocol submodules by default (local git is 2.37.2; the key is ignored there). |
| `test_base_must_be_full_sha` | `"main"` (mutable ref) and a 12-hex prefix (ambiguous) — the two realistic wrong inputs; both must raise. |
| `test_slug_collision_different_branch_raises` | `evolve/p1/candidate-1` vs `evolve/p1-candidate-1` — distinct branches with identical slugs; the lossy slug map must fail loudly rather than share a tree. |
| `test_squatting_directory_raises` | Unregistered directory pre-created at the target path — refuse to adopt a foreign tree. |
| `test_resume_branch_survives_tree_deleted` | Crash shape 1: branch with a commit survives, tree removed. Re-creation must re-attach at the *branch tip* (preserving candidate work), not reset to base. |
| `test_resume_stale_registration_recreated` | Crash shape 2: tree deleted behind git's back (`shutil.rmtree`), registration stale. Must prune and rebuild, not error. |
| `test_list_worktrees_includes_main_checkout` | Documents that the main checkout appears in listings — registration lookups rely on it. |
| `test_default_root_is_repo_sibling` | Pins the layout contract: same filesystem, outside the repo. |

### `tests/test_config.py` (additions)

| Test | Input rationale |
|---|---|
| `test_non_relative_eval_cwd_rejected_at_load` | Parametrised over `/tmp/bench` (POSIX absolute), `C:/bench` (Windows drive), `//server/share` (UNC), `bench/../../etc` (relative but tree-escaping) — one representative per escape flavour, so the cross-platform predicate is exercised on every form regardless of host OS. |
| `test_resolved_eval_cwd_joins_candidate_tree` | Unset → tree root; `bench/perf` resolved against two *different* trees yields two different answers — the property that was previously false. |
| `test_resolved_eval_cwd_rejects_programmatic_absolute` | Spec built via `dataclasses.replace` (bypassing the loader) — the second guard layer. |

### `tests/test_eval_runner.py` (additions)

| Test | Input rationale |
|---|---|
| `test_scratch_exported_and_distinct_per_call` | Two calls with two scratch dirs; the child prints its own environment, so the assertion is on what the *eval process* observed, not on runner internals. Also asserts the runner created the dirs. |
| `test_partial_env_merges_over_parent` | A single extra variable — the minimal input that, under the old replace semantics, wiped `PATH`/`SYSTEMROOT` (on Windows the child Python would not even start). Asserts both survival of `PATH` and arrival of the extra var. |
| `test_no_scratch_means_no_env_var` | Control case: no `scratch` → no synthetic variable. `monkeypatch.delenv` guards against inheriting one from the surrounding session. |

## Failures

None — all 20 new tests passed on their first complete run, and the full
suite (175 tests) passes with no regressions. No fixes were required
during test development.

## Incidental findings

All of the findings below were fixed in the follow-up coherence commit
(same day), after the initial report flagged them as out of scope:

- Three pre-existing ruff `F401` unused imports in `ablation.py:45` and
  `backends/gitlab.py:28-29` — removed (verified genuinely unused: no
  in-file usage, no external importer).
- `SKILL.md` Phase 0c assigned into frozen dataclasses
  (`spec.scope.target_files = ...`); both `ScopeSpec` and `ProblemSpec`
  are frozen, so the documented sibling-mode flow would raise
  `FrozenInstanceError` — rewritten with `dataclasses.replace` on both
  levels.

The coherence sweep for that commit surfaced three further defects, all
fixed and covered by the amended `test_artifact.py` assertions:

- **`SeedResult` returned absolute paths** while `enforce_scope` matches
  repo-relative POSIX paths from `git diff --name-only` — in sibling
  mode every candidate would have been pruned as out-of-scope, and the
  canonical file's `do_not_touch` seal would silently not seal. Paths
  are now repo-root-relative POSIX (`_repo_rel`); tests assert the exact
  relative form.
- **Phase 0c seed visibility**: the seed file was written uncommitted,
  but candidate worktrees materialise from the run anchor — no candidate
  would ever have seen the sibling. The seed step now runs in a seed
  worktree, commits, and re-anchors the run to the seed commit. (The
  SKILL's `seed_sibling(spec)` call was also stale against the real
  signature — `problem_id` is keyword-required.)
- **Phase C re-resolved `current_sha` each round** (introduced in the
  same day's Phase-4 edit) — a trunk moving between rounds would have
  split the run across two baselines. Phase C now reuses the single
  anchor resolved in Phase 0b.

## Sibling-mode dry run (protocol execution) — same day

**What**: the evolve SKILL's Phase 0b → 0c → C → D → cleanup sequence,
executed *literally* with the real library APIs against a throwaway git
repo — the empirical check of the protocol-text fixes above, which the
unit suite cannot reach (markdown does not execute). Preserved as the
permanent integration test `tests/test_sibling_protocol.py`.

**Why these inputs**: the eval computes a deterministic
$\sum_{i=1}^{5} i^2 = 55$, so the baseline/seed comparison is exact —
any drift is a defect, not noise. The canonical class carries a
class-level self-reference (`Strategy.scale`), making the sibling rename
non-trivial: a rename that missed it would diverge and must be caught by
the seed-validation gate. Candidate edits set `scale` to 2 and 3
(→ 110 and 165), giving each tree a distinguishable metric; the eval
writes its result into `AGENT_EVOLVE_SCRATCH`, making cross-candidate
interleaving directly observable.

**Result**: all stages passed — one-off script in 3.8 s; pytest form
3.77 s (176 total suite tests, 21.29 s).

| Stage | Verified |
|---|---|
| 0b | anchor resolved once; baseline measured in its own worktree; `expected_baseline` gate passes at exactly 55.0 |
| 0c | seed written in seed worktree, committed; run re-anchored (SHA moved); spec re-derived via `dataclasses.replace`; seed eval reproduces baseline exactly |
| C | both candidate trees **contain the committed seed** (the visibility fix); trees distinct |
| explorers | edit in candidate 1 invisible in candidate 2 until its own edit; commits land per-branch |
| D | real `git diff --name-only` output passes `enforce_scope` against replaced `target_files` (path-dialect fix); canonical file sealed via `do_not_touch`; per-tree metrics 110.0 / 165.0; each scratch dir holds only its own candidate's result |
| cleanup | trees removed; candidate branches survive; utility branches deleted without orphaning any candidate commit (seed remains each candidate's ancestor) |

**Finding F1 (fixed)**: Phase 0b ran `spec.eval_command` verbatim, but a
sibling-mode command carries a literal `{symbol}` token — the baseline
run would have failed to resolve any symbol. The SKILL now substitutes
the canonical symbol (`eval_command_for`) in sibling mode; in replace
mode the command has no token and is used verbatim, so the change is a
no-op there.
