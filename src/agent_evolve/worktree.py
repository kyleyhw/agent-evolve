"""Per-candidate git worktree isolation.

The evolutionary round dispatches N explorers in parallel, each committing
to its own ``evolve/<problem>/candidate-<n>`` branch. Checking out N
branches in ONE working directory makes the explorers overwrite each
other's files — every measurement downstream of that is garbage. This
module gives each candidate a dedicated working tree via ``git worktree``.

Error policy — deliberately opposite to :mod:`agent_evolve.git_utils`
---------------------------------------------------------------------
``git_utils`` never raises: it discovers *optional* signals (default
branch, anchor SHA) where a missing answer degrades gracefully. Worktree
creation is different — isolation is a PRECONDITION of the round. If a
candidate cannot get its own tree, the search must stop: silently
continuing without isolation is exactly the defect this module exists to
fix. Hence :func:`create_worktree` and :func:`list_worktrees` RAISE
:class:`WorktreeError` on any failure. Only :func:`remove_worktree` is
best-effort — cleanup at finalize time must not be able to kill an
otherwise-successful run.

Layout
------
Worktrees default to SIBLINGS of the repository, under
``<repo-parent>/<repo-name>.worktrees/<slug>``:

- same filesystem, so git's object sharing works without cross-device
  copies;
- outside the repository, so candidate trees never appear in
  ``git status`` and are never swept up by an explorer's ``git add -A``.

Scope of the isolation
----------------------
A worktree isolates *repository* paths only. An eval that writes to a
fixed absolute path (``/tmp/foo``, ``C:/tmp/foo``) hits the same
directory from every candidate in every worktree. That hazard is handled
by the ``scratch`` / ``AGENT_EVOLVE_SCRATCH`` contract in
:func:`agent_evolve.eval.runner.run_eval`, not here.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(RuntimeError):
    """A worktree could not be created, listed, or attached.

    Raised eagerly (see the module docstring's error policy): a candidate
    without an isolated tree must not run.
    """


@dataclass(frozen=True)
class Worktree:
    """A materialised working tree: where it lives and which branch it holds."""

    path: Path
    branch: str


# ``git worktree add`` needs a full, immutable anchor. A branch name would
# resolve to whatever the branch points at *when each candidate is
# created* — candidates created late in a round would silently base off a
# moved trunk. ``agent_evolve.git_utils.current_sha`` produces the
# expected input.
_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")

# Characters preserved verbatim when slugifying a branch name into a
# directory name. Everything else (notably ``/`` in
# ``evolve/<problem>/candidate-<n>``) collapses to ``-`` so the worktree
# root stays flat (one directory per candidate, no nested hierarchy).
_SLUG_KEEP = re.compile(r"[^A-Za-z0-9._-]+")


def default_worktree_root(repo: Path) -> Path:
    """The sibling directory that holds a repo's candidate worktrees."""
    return repo.parent / f"{repo.name}.worktrees"


def create_worktree(
    branch: str,
    *,
    repo: str | Path,
    base: str,
    root: str | Path | None = None,
) -> Worktree:
    """Create (or re-attach) an isolated working tree for *branch*.

    *base* must be a full 40-hex commit SHA — the shared anchor for every
    candidate in the run (see ``_FULL_SHA``). *root* defaults to
    :func:`default_worktree_root`; the tree lands at ``<root>/<slug(branch)>``.

    Idempotent for crash-resume, covering all three crash shapes:

    - tree registered on the expected branch and present on disk → returned
      as-is;
    - registration stale (tree deleted on disk) → pruned, then re-created;
    - branch exists but has no tree (e.g. the tree was cleaned up) →
      re-attached with ``git worktree add <path> <branch>`` (no ``-b``),
      preserving the branch's commits.

    Raises :class:`WorktreeError` when the path is already claimed by a
    *different* branch (the slug map is lossy — ``a/b`` and ``a-b``
    collide), when an unregistered directory squats on the path, or when
    any git step fails. If the repository has a ``.gitmodules``, submodules
    are initialised in the new tree — ``git worktree add`` leaves them as
    empty directories, which breaks any eval that imports across them.
    """
    repo_path = Path(repo).resolve()
    if not _FULL_SHA.match(base):
        raise WorktreeError(
            f"base must be a full 40-hex commit SHA, got {base!r}. Anchoring to a "
            f"branch name would let the base move mid-run; resolve it first with "
            f"agent_evolve.git_utils.current_sha(branch)."
        )

    root_path = Path(root).resolve() if root is not None else default_worktree_root(repo_path)
    slug = _slugify(branch)
    path = root_path / slug

    # Consult git's registry before touching the filesystem: an existing
    # registration decides between "resume" and "collision".
    registered = _find_registration(repo_path, path)
    if registered is not None:
        if registered.branch != branch:
            raise WorktreeError(
                f"worktree path {path} is already registered to branch "
                f"{registered.branch!r}, not {branch!r} — branch names that "
                f"slugify identically (e.g. 'a/b' vs 'a-b') cannot share a run."
            )
        if path.is_dir():
            return Worktree(path=path, branch=branch)
        # Registered but gone from disk (a crashed run's tree was deleted
        # manually). Prune the stale registration and fall through to
        # re-creation.
        _git(["worktree", "prune"], cwd=repo_path)
    elif path.exists():
        raise WorktreeError(
            f"{path} exists but is not a registered worktree of {repo_path} — "
            f"refusing to adopt a foreign directory."
        )

    root_path.mkdir(parents=True, exist_ok=True)

    if _branch_exists(repo_path, branch):
        # Resume: the branch survived a crash, its tree did not. ``-b``
        # would fail on the existing branch; attach instead, keeping the
        # branch's commits (the branch tip, not *base*, is what the
        # candidate had built).
        _git(["worktree", "add", str(path), branch], cwd=repo_path)
    else:
        _git(["worktree", "add", "-b", branch, str(path), base], cwd=repo_path)

    if (repo_path / ".gitmodules").exists():
        # Worktrees get EMPTY submodule directories; initialise them so
        # evals that import across submodules see real code.
        _git(["submodule", "update", "--init", "--recursive"], cwd=path)

    return Worktree(path=path, branch=branch)


def remove_worktree(wt: Worktree, *, repo: str | Path) -> None:
    """Remove *wt*'s tree and registration. Best-effort — never raises.

    Escalation: plain removal → ``--force`` (a candidate tree is routinely
    dirty with build artifacts; its commits live on the branch, which
    survives) → ``prune`` to drop any stale registration. Failures are
    swallowed: finalize-time cleanup must not kill a successful run.
    """
    try:
        result = _git(["worktree", "remove", str(wt.path)], cwd=repo, check=False)
        if result.returncode != 0:
            _git(["worktree", "remove", "--force", str(wt.path)], cwd=repo, check=False)
        _git(["worktree", "prune"], cwd=repo, check=False)
    except WorktreeError:
        # _git raises even under check=False when the git binary itself
        # cannot run — nothing to clean up with, so give up quietly.
        pass


def list_worktrees(repo: str | Path) -> list[Worktree]:
    """All worktrees git knows about for *repo*, including the main checkout.

    Parses ``git worktree list --porcelain``. Detached and bare entries
    carry no branch and are omitted — every candidate tree this module
    creates is on a branch, so a branchless entry is never one of ours.
    """
    out = _git(["worktree", "list", "--porcelain"], cwd=repo).stdout
    worktrees: list[Worktree] = []
    current_path: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):])
        elif line.startswith("branch refs/heads/") and current_path is not None:
            worktrees.append(
                Worktree(path=current_path, branch=line[len("branch refs/heads/"):])
            )
            current_path = None
    return worktrees


def _slugify(branch: str) -> str:
    """Directory-safe name for *branch* (``evolve/p/candidate-3`` → ``evolve-p-candidate-3``)."""
    slug = _SLUG_KEEP.sub("-", branch).strip("-")
    if not slug:
        raise WorktreeError(f"branch name {branch!r} slugifies to nothing")
    return slug


def _find_registration(repo: Path, path: Path) -> Worktree | None:
    """The registered worktree at *path*, or ``None``.

    Comparison uses ``normcase`` on resolved paths: git porcelain prints
    absolute paths (forward slashes on Windows), and NTFS is
    case-insensitive while pathlib equality is not.
    """
    wanted = _norm(path)
    for wt in list_worktrees(repo):
        if _norm(wt.path) == wanted:
            return wt
    return None


def _norm(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _branch_exists(repo: Path, branch: str) -> bool:
    result = _git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        check=False,
    )
    return result.returncode == 0


def _git(
    args: list[str],
    *,
    cwd: str | Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run git, raising :class:`WorktreeError` on failure when *check*.

    Not :func:`agent_evolve.git_utils._run_git` — that helper's contract
    (never raise, 2 s timeout) is the wrong policy here: creation must
    fail loudly, and ``submodule update`` legitimately does network I/O
    that a short timeout would kill. No timeout is imposed;
    ``GIT_TERMINAL_PROMPT=0`` makes an interactive credential prompt fail
    immediately instead of hanging the run.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError) as e:
        raise WorktreeError(f"could not invoke git {' '.join(args)}: {e}") from e
    if check and result.returncode != 0:
        raise WorktreeError(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result
