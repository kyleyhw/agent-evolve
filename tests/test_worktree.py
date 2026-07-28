"""Worktree isolation tests.

Input-data rationale: branch names use the production shape
``evolve/<problem>/candidate-<n>`` throughout — slashed names are the
norm in this system, not an edge case — and every anchor SHA is resolved
from the fixture repo's real ``main`` via ``git_utils.current_sha``,
never hard-coded.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agent_evolve.git_utils import current_sha
from agent_evolve.worktree import (
    WorktreeError,
    create_worktree,
    default_worktree_root,
    list_worktrees,
    remove_worktree,
)
from tests.conftest import init_git_repo, run_git


def _anchor(repo: Path) -> str:
    """The fixture repo's ``main`` tip — the anchor every candidate shares."""
    sha = current_sha("main", cwd=repo)
    assert sha is not None
    return sha


def test_create_remove_round_trip(git_repo: Path) -> None:
    """A created tree is materialised at the anchor, on its branch, and
    registered; removal deletes and deregisters it."""
    sha = _anchor(git_repo)
    wt = create_worktree("evolve/p1/candidate-1", repo=git_repo, base=sha)

    assert wt.path.is_dir()
    assert (wt.path / "README.md").is_file()  # tree content, not an empty dir
    assert wt.branch == "evolve/p1/candidate-1"
    assert run_git(wt.path, "rev-parse", "--abbrev-ref", "HEAD") == "evolve/p1/candidate-1"
    assert run_git(wt.path, "rev-parse", "HEAD") == sha  # anchored at base
    assert any(w.branch == "evolve/p1/candidate-1" for w in list_worktrees(git_repo))

    remove_worktree(wt, repo=git_repo)
    assert not wt.path.exists()
    assert all(w.branch != "evolve/p1/candidate-1" for w in list_worktrees(git_repo))


def test_recreate_is_idempotent(git_repo: Path) -> None:
    """Re-requesting an existing (branch, path) returns it untouched —
    resume after a crashed round must not wipe in-progress work."""
    sha = _anchor(git_repo)
    wt1 = create_worktree("evolve/p1/candidate-1", repo=git_repo, base=sha)
    (wt1.path / "in_progress.py").write_text("x = 1\n", encoding="utf-8")

    wt2 = create_worktree("evolve/p1/candidate-1", repo=git_repo, base=sha)

    assert wt2 == wt1
    assert (wt2.path / "in_progress.py").is_file()  # nothing was re-created


def test_two_worktrees_are_isolated(git_repo: Path) -> None:
    """The defect under repair: two candidates must get distinct directories
    on distinct branches, with writes and commits invisible to each other."""
    sha = _anchor(git_repo)
    wt_a = create_worktree("evolve/p1/candidate-1", repo=git_repo, base=sha)
    wt_b = create_worktree("evolve/p1/candidate-2", repo=git_repo, base=sha)

    assert wt_a.path != wt_b.path
    assert wt_a.branch != wt_b.branch

    (wt_a.path / "only_in_a.py").write_text("A = 1\n", encoding="utf-8")
    assert not (wt_b.path / "only_in_a.py").exists()

    run_git(wt_a.path, "add", "-A")
    run_git(wt_a.path, "commit", "-m", "candidate-1 change")
    assert run_git(wt_a.path, "rev-parse", "HEAD") != run_git(wt_b.path, "rev-parse", "HEAD")
    assert run_git(wt_b.path, "rev-parse", "HEAD") == sha  # b still at the anchor


def test_slashed_branch_names_slugified_flat(git_repo: Path) -> None:
    """``evolve/p1/candidate-3`` maps to one flat directory under the
    sibling root — no nested hierarchy, nothing inside the repo."""
    sha = _anchor(git_repo)
    wt = create_worktree("evolve/p1/candidate-3", repo=git_repo, base=sha)

    assert wt.path.name == "evolve-p1-candidate-3"
    assert wt.path.parent.name == f"{git_repo.name}.worktrees"
    assert git_repo.resolve() not in wt.path.resolve().parents  # sibling, not child
    assert wt.branch == "evolve/p1/candidate-3"  # branch name kept verbatim


def test_dirty_tree_removal(git_repo: Path) -> None:
    """A tree with untracked and modified files still comes off cleanly —
    candidate trees are routinely dirty with build artifacts."""
    sha = _anchor(git_repo)
    wt = create_worktree("evolve/p1/candidate-1", repo=git_repo, base=sha)
    (wt.path / "junk.bin").write_text("dirty", encoding="utf-8")  # untracked
    (wt.path / "README.md").write_text("modified\n", encoding="utf-8")  # modified

    remove_worktree(wt, repo=git_repo)

    assert not wt.path.exists()
    assert all(w.branch != "evolve/p1/candidate-1" for w in list_worktrees(git_repo))


def test_submodule_auto_init(git_repo: Path, tmp_path: Path) -> None:
    """A repo with .gitmodules gets populated submodules in the new tree —
    ``git worktree add`` alone leaves them as empty directories."""
    sub = init_git_repo(tmp_path / "subrepo")
    (sub / "mod.py").write_text("VALUE = 42\n", encoding="utf-8")
    run_git(sub, "add", "-A")
    run_git(sub, "commit", "-m", "module content")

    # File-protocol submodules are blocked by default since git 2.38.1;
    # the key is ignored by older gits, so setting it is always safe.
    run_git(git_repo, "config", "protocol.file.allow", "always")
    run_git(git_repo, "submodule", "add", sub.as_posix(), "vendor/sub")
    run_git(git_repo, "commit", "-m", "add submodule")

    wt = create_worktree("evolve/p1/candidate-1", repo=git_repo, base=_anchor(git_repo))

    assert (wt.path / "vendor" / "sub" / "mod.py").is_file()


def test_base_must_be_full_sha(git_repo: Path) -> None:
    """Branch names and abbreviated SHAs are rejected: a mutable or
    ambiguous base would let candidates anchor to different commits."""
    with pytest.raises(WorktreeError, match="40-hex"):
        create_worktree("evolve/p1/candidate-1", repo=git_repo, base="main")
    with pytest.raises(WorktreeError, match="40-hex"):
        create_worktree("evolve/p1/candidate-1", repo=git_repo, base=_anchor(git_repo)[:12])


def test_slug_collision_different_branch_raises(git_repo: Path) -> None:
    """The slug map is lossy (``a/b`` and ``a-b`` collide); a second branch
    claiming an occupied path must fail loudly, not share the tree."""
    sha = _anchor(git_repo)
    create_worktree("evolve/p1/candidate-1", repo=git_repo, base=sha)
    with pytest.raises(WorktreeError, match="already registered"):
        create_worktree("evolve/p1-candidate-1", repo=git_repo, base=sha)


def test_squatting_directory_raises(git_repo: Path, tmp_path: Path) -> None:
    """An unregistered directory at the target path is foreign — refuse to
    adopt it rather than run a candidate in an unknown tree."""
    root = tmp_path / "trees"
    (root / "evolve-p1-candidate-9").mkdir(parents=True)
    with pytest.raises(WorktreeError, match="not a registered worktree"):
        create_worktree("evolve/p1/candidate-9", repo=git_repo, base=_anchor(git_repo), root=root)


def test_resume_branch_survives_tree_deleted(git_repo: Path) -> None:
    """Crash shape: branch (with commits) survived, tree was removed.
    Re-creation must re-attach at the branch tip, not reset to base."""
    sha = _anchor(git_repo)
    wt = create_worktree("evolve/p1/candidate-1", repo=git_repo, base=sha)
    (wt.path / "work.py").write_text("W = 1\n", encoding="utf-8")
    run_git(wt.path, "add", "-A")
    run_git(wt.path, "commit", "-m", "candidate work")
    tip = run_git(wt.path, "rev-parse", "HEAD")
    run_git(git_repo, "worktree", "remove", "--force", str(wt.path))

    wt2 = create_worktree("evolve/p1/candidate-1", repo=git_repo, base=sha)

    assert run_git(wt2.path, "rev-parse", "HEAD") == tip
    assert (wt2.path / "work.py").is_file()


def test_resume_stale_registration_recreated(git_repo: Path) -> None:
    """Crash shape: tree deleted behind git's back (registration stale).
    Re-creation must prune and rebuild rather than error out."""
    sha = _anchor(git_repo)
    wt = create_worktree("evolve/p1/candidate-1", repo=git_repo, base=sha)
    shutil.rmtree(wt.path)  # e.g. a cleanup script wiped the .worktrees dir

    wt2 = create_worktree("evolve/p1/candidate-1", repo=git_repo, base=sha)

    assert wt2.path.is_dir()
    assert run_git(wt2.path, "rev-parse", "HEAD") == sha


def test_list_worktrees_includes_main_checkout(git_repo: Path) -> None:
    """The main checkout appears alongside candidate trees (documented
    behaviour callers rely on for registration lookups)."""
    sha = _anchor(git_repo)
    create_worktree("evolve/p1/candidate-1", repo=git_repo, base=sha)

    branches = {w.branch for w in list_worktrees(git_repo)}
    assert branches == {"main", "evolve/p1/candidate-1"}


def test_default_root_is_repo_sibling(git_repo: Path) -> None:
    """The default root sits NEXT TO the repo: same filesystem (object
    sharing) but outside it (invisible to git status / git add -A)."""
    root = default_worktree_root(git_repo)
    assert root.parent == git_repo.parent
    assert root.name == f"{git_repo.name}.worktrees"
