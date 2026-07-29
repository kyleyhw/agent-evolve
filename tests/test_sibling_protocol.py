"""Sibling-mode protocol integration test.

Pytest adaptation of the 2026-07-29 sibling-mode dry run: executes the
evolve SKILL's Phase 0b -> 0c -> C -> D -> cleanup sequence literally,
using the real library APIs, against a throwaway git repo. This is the
executable form of the protocol text — if a SKILL-level invariant
(seed visibility, re-anchoring, frozen-spec replacement, scope path
dialect, scratch isolation, branch survival) breaks in the library, it
breaks here, not in a live run.

Input-data rationale: the eval computes a deterministic sum of squares
over [1..5] (= 55), so the baseline/seed comparison is exact and any
drift is a defect rather than noise. The canonical class carries a
class-level self-reference (``Strategy.scale``) so the sibling rename is
non-trivial — a rename that misses it produces a seeded class that
diverges, which the seed-validation gate must catch. Candidate edits set
``scale`` to 2 and 3 (-> 110 and 165), giving each tree a distinguishable
metric; the eval also writes its result into ``AGENT_EVOLVE_SCRATCH`` so
cross-candidate interleaving would be directly observable.
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import replace
from datetime import date
from pathlib import Path

from agent_evolve.artifact import seed_sibling
from agent_evolve.eval import run_eval, validate_baseline
from agent_evolve.git_utils import current_sha
from agent_evolve.models import (
    BackendSpec,
    EvolutionSpec,
    Metric,
    OptimiseDirection,
    ProblemSpec,
    RuntimeModeSpec,
    SafetySpec,
    ScopeSpec,
    SiblingSpec,
)
from agent_evolve.scope import enforce_scope
from agent_evolve.worktree import Worktree, create_worktree, list_worktrees, remove_worktree
from tests.conftest import run_git

PID = "p1"

# Eval input [1..5]: sum of squares = 55 (1 + 4 + 9 + 16 + 25).
EXPECTED_BASELINE_SUM = 55.0

STRATEGY_SRC = textwrap.dedent(
    """\
    class Strategy:
        scale = 1

        def run(self, xs):
            return Strategy.scale * sum(x * x for x in xs)
    """
)

EVAL_SRC = textwrap.dedent(
    """\
    import argparse, importlib, json, os, sys
    from pathlib import Path

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here / "src"))

    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    args = ap.parse_args()

    cls = None
    for py in sorted((here / "src").glob("*.py")):
        mod = importlib.import_module(py.stem)
        if hasattr(mod, args.symbol):
            cls = getattr(mod, args.symbol)
            break
    if cls is None:
        sys.exit(f"symbol {args.symbol!r} not found in src/")

    result = cls().run([1, 2, 3, 4, 5])

    scratch = os.environ.get("AGENT_EVOLVE_SCRATCH")
    if scratch:
        (Path(scratch) / "cache.txt").write_text(str(result), encoding="utf-8")

    print(json.dumps({"result_sum": float(result)}))
    """
)


def _scratch_for(wt: Worktree) -> Path:
    """The SKILL's scratch convention: ``<tree>.scratch`` sibling."""
    return wt.path.with_name(wt.path.name + ".scratch")


def test_sibling_mode_protocol_end_to_end(tmp_path: Path) -> None:
    """Phases 0b, 0c, C, D and cleanup, exactly as the SKILL documents them."""
    # ---- Target repo -----------------------------------------------------
    repo = tmp_path / "target"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "strategy.py").write_text(STRATEGY_SRC, encoding="utf-8")
    (repo / "eval.py").write_text(EVAL_SRC, encoding="utf-8")
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test User")
    run_git(repo, "config", "commit.gpgsign", "false")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "seed target repo")

    py = Path(sys.executable).as_posix()
    spec = ProblemSpec(
        description="sibling-mode protocol integration test",
        mode="algorithm",  # equivalence machinery is not under test here
        eval_command=f'"{py}" eval.py --symbol {{symbol}}',
        metrics=[Metric(name="result_sum", optimise=OptimiseDirection.MAXIMIZE, primary=True)],
        scope=ScopeSpec(target_files=["src/strategy.py"]),
        evolution=EvolutionSpec(rounds=1, candidates_per_round=2),
        runtime_mode=RuntimeModeSpec(equivalence_check="disabled"),
        safety=SafetySpec(protected_branch="main"),
        backend=BackendSpec(type="local", root_dir=str(tmp_path / "evolve-state")),
        artifact_mode="sibling",
        sibling=SiblingSpec(symbol_name="Strategy"),
        expected_baseline={"result_sum": EXPECTED_BASELINE_SUM},
        expected_baseline_tolerance=0.05,
    )

    # ---- Phase 0b: baseline in its own worktree, anchor resolved ONCE ----
    anchor = current_sha(spec.safety.protected_branch, cwd=repo)
    assert anchor is not None
    baseline_wt = create_worktree(f"evolve/{PID}/baseline", repo=repo, base=anchor)

    # Sibling mode substitutes the canonical symbol for the baseline run —
    # eval_command verbatim would pass a literal "{symbol}" (SKILL Phase 0b).
    assert spec.sibling is not None and spec.sibling.symbol_name is not None
    baseline_eval = run_eval(
        spec.eval_command_for(spec.sibling.symbol_name),
        cwd=spec.resolved_eval_cwd(baseline_wt.path),
        scratch=_scratch_for(baseline_wt),
    )
    assert baseline_eval.passed, baseline_eval.stderr
    assert validate_baseline(
        baseline_eval.metrics, spec.expected_baseline, spec.expected_baseline_tolerance
    ).matches

    # ---- Phase 0c: seed worktree -> commit -> re-anchor -> replace() -----
    seed_wt = create_worktree(f"evolve/{PID}/seed", repo=repo, base=anchor)
    seed = seed_sibling(spec, problem_id=PID, repo_root=seed_wt.path)

    assert seed.new_path == f"src/strategy_{PID}_{date.today():%Y_%m_%d}.py"
    assert seed.original_path == "src/strategy.py"
    assert not Path(seed.new_path).is_absolute()

    run_git(seed_wt.path, "add", "-A")
    run_git(seed_wt.path, "commit", "-m", f"seed sibling artifact for {PID}")
    seed_anchor = current_sha(f"evolve/{PID}/seed", cwd=seed_wt.path)
    assert seed_anchor is not None and seed_anchor != anchor  # re-anchor must move
    anchor = seed_anchor

    spec = replace(
        spec,
        scope=replace(
            spec.scope,
            target_files=[seed.new_path],
            do_not_touch=[*spec.scope.do_not_touch, seed.original_path],
        ),
    )

    seed_eval = run_eval(
        spec.eval_command_for(seed.new_symbol),
        cwd=spec.resolved_eval_cwd(seed_wt.path),
        scratch=_scratch_for(seed_wt),
    )
    assert seed_eval.passed, seed_eval.stderr
    assert validate_baseline(
        seed_eval.metrics, baseline_eval.metrics, spec.expected_baseline_tolerance
    ).matches  # behaviour-preserving rename reproduces the baseline exactly

    # ---- Phase C: candidate worktrees from the re-anchor -----------------
    worktrees: dict[str, Worktree] = {}
    for cid in ("1", "2"):
        worktrees[cid] = create_worktree(f"evolve/{PID}/candidate-{cid}", repo=repo, base=anchor)

    for wt in worktrees.values():
        # THE seed-visibility property: committed seed present in every tree.
        assert (wt.path / seed.new_path).is_file()
        assert (wt.path / seed.original_path).is_file()
    assert worktrees["1"].path != worktrees["2"].path

    # ---- simulated explorers: distinct in-scope edits, then commit -------
    for cid, scale in (("1", 2), ("2", 3)):
        wt = worktrees[cid]
        seeded = wt.path / seed.new_path
        seeded.write_text(
            seeded.read_text(encoding="utf-8").replace("scale = 1", f"scale = {scale}"),
            encoding="utf-8",
        )
        if cid == "1":  # isolation probe: candidate 2 must still be pristine
            other = worktrees["2"].path / seed.new_path
            assert "scale = 1" in other.read_text(encoding="utf-8")
        run_git(wt.path, "add", "-A")
        run_git(wt.path, "commit", "-m", f"candidate {cid}: scale = {scale}")

    # ---- Phase D: scope on real diffs, per-tree evals, scratch -----------
    expected_metric = {"1": 110.0, "2": 165.0}  # 2x and 3x the sum of squares
    for cid, wt in worktrees.items():
        changed = run_git(wt.path, "diff", "--name-only", anchor, "HEAD").splitlines()
        report = enforce_scope(changed, spec.scope)
        assert report.in_scope, report.violations  # path-dialect fix, end to end

        res = run_eval(
            spec.eval_command_for(seed.new_symbol),
            cwd=spec.resolved_eval_cwd(wt.path),
            scratch=_scratch_for(wt),
        )
        assert res.passed, res.stderr
        assert res.metrics["result_sum"] == expected_metric[cid]

    # The do_not_touch seal, in the repo-relative dialect:
    sealed = enforce_scope([seed.original_path], spec.scope)
    assert not sealed.in_scope
    assert any("do_not_touch" in v for v in sealed.violations)

    # Scratch non-interleaving: each candidate's scratch holds ITS result.
    for cid, wt in worktrees.items():
        cached = (_scratch_for(wt) / "cache.txt").read_text(encoding="utf-8")
        assert float(cached) == expected_metric[cid]

    # ---- Cleanup: trees go, candidate branches stay ----------------------
    for wt in (*worktrees.values(), baseline_wt, seed_wt):
        remove_worktree(wt, repo=repo)
        assert not wt.path.exists()
    assert {w.branch for w in list_worktrees(repo)} == {"main"}

    branches = set(run_git(repo, "branch", "--format=%(refname:short)").splitlines())
    assert {f"evolve/{PID}/candidate-1", f"evolve/{PID}/candidate-2"} <= branches
    # Utility branches deletable without orphaning candidate commits:
    run_git(repo, "branch", "-D", f"evolve/{PID}/baseline", f"evolve/{PID}/seed")
    for cid in ("1", "2"):
        assert len(run_git(repo, "rev-parse", f"evolve/{PID}/candidate-{cid}")) == 40
        # ...and the seed commit remains each candidate's ancestor:
        run_git(repo, "merge-base", "--is-ancestor", anchor, f"evolve/{PID}/candidate-{cid}")
