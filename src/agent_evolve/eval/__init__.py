"""Eval command runner and logic-equivalence checker."""

from agent_evolve.eval.runner import BaselineCheck, EvalResult, run_eval, validate_baseline
from agent_evolve.eval.equivalence import EquivalenceReport, check_equivalence

__all__ = [
    "BaselineCheck",
    "EvalResult",
    "run_eval",
    "validate_baseline",
    "EquivalenceReport",
    "check_equivalence",
]
