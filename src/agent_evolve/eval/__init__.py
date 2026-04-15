"""Eval command runner and logic-equivalence checker."""

from agent_evolve.eval.runner import EvalResult, run_eval
from agent_evolve.eval.equivalence import EquivalenceReport, check_equivalence

__all__ = ["EvalResult", "run_eval", "EquivalenceReport", "check_equivalence"]
