"""Git introspection helpers.

Used by :mod:`agent_evolve.config` and the supervisor SKILL to discover the
target repository's default branch at manifest-load time, so
``safety.protected_branch`` defaults to *whatever the repo actually calls
its trunk* (``main``, ``master``, ``trunk``, ``develop``, ...) rather than
the hardcoded literal ``"main"``.

These helpers never raise. They return a sane fallback when git is
missing, the cwd is outside any repository, the remote is unreachable, or
``HEAD`` is detached. Callers can rely on getting *some* string back.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# A short timeout is enough — every git invocation here is a local ref
# read (no network). 2.0 s is conservative against a slow filesystem
# (e.g. an AV scan stalling the binary) while preventing a misbehaving
# git installation from indefinitely blocking manifest loading.
_GIT_TIMEOUT_S: float = 2.0

# Conservative last-resort fallback when no other signal is obtainable.
# Matches the upstream default that GitHub, GitLab, and Bitbucket have
# all used for new repositories since 2020.
_FALLBACK_BRANCH: str = "main"


def detect_default_branch(cwd: str | Path | None = None) -> str:
    """Return the repository's default branch name.

    Resolution order:

    1. ``git symbolic-ref --short refs/remotes/origin/HEAD`` — the most
       authoritative source when the repo has a remote. Returns
       ``origin/<branch>``; we strip the prefix.
    2. ``git rev-parse --abbrev-ref HEAD`` — the currently checked-out
       branch. Used when there is no remote, or the remote's HEAD is
       not set. The literal string ``"HEAD"`` is returned for a
       detached HEAD, which we treat as no signal.
    3. ``"main"`` — last-resort fallback (see ``_FALLBACK_BRANCH``).

    The function never raises. Failures (no git binary, non-repo cwd,
    permissions, timeout) silently fall through to the next strategy.
    """
    workdir = str(cwd) if cwd is not None else None

    out = _run_git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], workdir)
    if out:
        # Output shape: ``origin/main``. Tolerate non-``origin`` remotes
        # by partitioning on the first ``/`` rather than requiring a
        # specific prefix.
        _, sep, branch = out.partition("/")
        if sep and branch:
            return branch

    out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], workdir)
    # rev-parse returns the literal ``"HEAD"`` when the working tree is
    # in a detached state — no useful branch to surface in that case.
    if out and out != "HEAD":
        return out

    return _FALLBACK_BRANCH


def _run_git(args: list[str], cwd: str | None) -> str | None:
    """Run ``git`` with *args*; return stripped stdout, or ``None`` on any failure.

    ``None`` is used (rather than the empty string) so callers can
    distinguish "no signal" from "explicit empty answer".
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None
