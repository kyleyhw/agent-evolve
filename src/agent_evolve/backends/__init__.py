"""Backend adapters (local filesystem, GitHub, GitLab)."""

from agent_evolve.backends.base import EvolveBackend, MergeNotPermittedError
from agent_evolve.backends.local import LocalBackend
from agent_evolve.backends.github import GitHubBackend
from agent_evolve.backends.gitlab import GitLabBackend

__all__ = [
    "EvolveBackend",
    "MergeNotPermittedError",
    "LocalBackend",
    "GitHubBackend",
    "GitLabBackend",
]
