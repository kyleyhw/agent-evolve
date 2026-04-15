"""Abstract backend interface.

Every backend (local, GitHub, GitLab) implements this contract. The supervisor
agent calls these methods via tool invocations — it does not know or care which
backend it is talking to.

Safety contract
---------------
``finalize()`` *never* merges. It closes non-winning branches, opens a final PR
from the winner to ``main``, and stops. Merging is strictly a human action.

The ``agents_can_merge`` property is hardcoded to ``False`` on the base class
and cannot be overridden. Any subclass that attempts to merge inside its
``finalize()`` implementation is violating the contract, and tests in
``tests/test_backends.py`` assert this.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, final

from agent_evolve.models import Candidate, EquivalenceReport, ProblemSpec, ReviewerVerdict


class MergeNotPermittedError(RuntimeError):
    """Raised if a backend implementation attempts to merge to the protected branch."""


_PROTECTED_ATTRS = frozenset({"agents_can_merge", "assert_no_merge"})


class EvolveBackend(ABC):
    """Abstract state machine for an evolutionary run.

    Concrete subclasses persist state to GitHub, GitLab, or the local filesystem.
    The supervisor agent drives the state machine; the backend just records
    moves. Concurrency is the backend's responsibility — the GitHub backend gets
    it for free via Issue comments; the local backend uses file locks.

    Safety
    ------
    ``agents_can_merge`` and ``assert_no_merge`` are protected — any subclass
    that redefines them raises :class:`TypeError` at class-creation time.
    ``@typing.final`` is only a static hint, so we enforce at runtime via
    ``__init_subclass__``. Reasonable subclasses never need to touch these.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for name in _PROTECTED_ATTRS:
            if name in cls.__dict__:
                raise TypeError(
                    f"{cls.__name__} cannot override '{name}' — it is a safety invariant "
                    f"of EvolveBackend. Remove the override."
                )

    def __init__(self, spec: ProblemSpec) -> None:
        self.spec = spec

    @property
    @final
    def agents_can_merge(self) -> bool:
        """Hardcoded ``False``. Never override — this is a safety invariant."""
        return False

    @final
    def assert_no_merge(self, action: str) -> None:
        """Helper for subclasses. Call this at the top of any method that could merge."""
        if self.agents_can_merge:
            raise MergeNotPermittedError(
                f"{type(self).__name__}.{action} attempted merge — agents_can_merge is hardcoded False"
            )

    @abstractmethod
    def create_problem(self, spec: ProblemSpec) -> str:
        """Create a new evolutionary problem.

        Returns the problem id (GitHub issue number, local directory name, etc).
        Also installs any protection (GitHub branch protection rule, .git hooks,
        local-backend write guards) needed to enforce the safety contract.
        """

    @abstractmethod
    def submit_candidate(self, candidate: Candidate) -> str:
        """Submit a new candidate attempt. Returns the candidate id assigned by the backend."""

    @abstractmethod
    def score_candidate(
        self,
        candidate_id: str,
        metrics: dict[str, float],
        *,
        equivalence: EquivalenceReport | None = None,
    ) -> None:
        """Record evaluation results for a candidate and move its status to ``scored``.

        In runtime mode the supervisor attaches the :class:`EquivalenceReport`
        here so the reviewer (and the persistent state) can see whether the
        candidate preserves the parent's logic.
        """

    @abstractmethod
    def record_verdict(self, candidate_id: str, verdict: ReviewerVerdict) -> None:
        """Attach a reviewer verdict. Candidate status becomes ``approved`` or ``rejected``."""

    @abstractmethod
    def get_leaderboard(self) -> list[Candidate]:
        """Return every candidate in this problem, newest state first."""

    @abstractmethod
    def prune(self, candidate_id: str, reason: str) -> None:
        """Mark a candidate as pruned (Pareto-inferior) and archive its branch."""

    @abstractmethod
    def update_graph(self, mermaid: str, html_path: str | None = None) -> None:
        """Update the problem root with the latest Mermaid diagram and HTML report link."""

    @abstractmethod
    def finalize(self, winner_id: str) -> str:
        """Close the run and open a final PR — never merge.

        Implementations MUST:

        1. Verify the winner exists and has an ``approved`` verdict.
        2. Close (or archive) every non-winning branch/PR with a summary comment.
        3. Open a new PR from the winner's branch to the protected branch.
        4. Attach the full Trait Matrix, evolution graph, and reviewer verdict.
        5. Return the URL / id of the final PR.

        Implementations MUST NOT:

        - Merge the final PR.
        - Delete or force-push ``main``.
        - Run anything that bypasses the protected-branch rule.
        """
