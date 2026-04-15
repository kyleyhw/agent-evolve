"""Module-scope enforcement — candidates may only touch declared files."""

from agent_evolve.scope.enforcer import ScopeReport, enforce_scope

__all__ = ["ScopeReport", "enforce_scope"]
