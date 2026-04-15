"""Manifest loader — turns ``agent-evolve.yaml`` into a :class:`ProblemSpec`.

Minimal configs are just a handful of lines; everything else has a default
defined on the dataclasses in :mod:`agent_evolve.models`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent_evolve.models import (
    BackendSpec,
    EvolutionSpec,
    Metric,
    OptimiseDirection,
    ProblemSpec,
    RuntimeModeSpec,
    SafetySpec,
    ScopeSpec,
)


class ManifestError(ValueError):
    """Raised when ``agent-evolve.yaml`` is malformed or missing required fields."""


def load_manifest(path: str | Path) -> ProblemSpec:
    """Parse the manifest at *path* into a :class:`ProblemSpec`."""
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"manifest not found: {p}")

    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ManifestError(f"manifest must be a YAML mapping, got {type(raw).__name__}")

    return _parse(raw, source=p)


def _parse(raw: dict[str, Any], *, source: Path) -> ProblemSpec:
    problem = _require(raw, "problem", dict, source)
    scope = _require(raw, "scope", dict, source)
    backend = _require(raw, "backend", dict, source)
    evolution = raw.get("evolution", {}) or {}
    runtime_mode = raw.get("runtime_mode", {}) or {}
    safety = raw.get("safety", {}) or {}

    metrics_raw = problem.get("metrics", [])
    if not metrics_raw:
        raise ManifestError(f"{source}: problem.metrics must list at least one metric")

    metrics = [_parse_metric(m, source) for m in metrics_raw]

    return ProblemSpec(
        version=raw.get("version", 1),
        description=_require(problem, "description", str, source, ctx="problem."),
        mode=problem.get("mode", "algorithm"),
        eval_command=_require(problem, "eval_command", str, source, ctx="problem."),
        metrics=metrics,
        scope=ScopeSpec(
            target_files=list(_require(scope, "target_files", list, source, ctx="scope.")),
            do_not_touch=list(scope.get("do_not_touch", []) or []),
            max_diff_files=scope.get("max_diff_files"),
        ),
        evolution=EvolutionSpec(
            rounds=int(evolution.get("rounds", 5)),
            candidates_per_round=int(evolution.get("candidates_per_round", 3)),
            operators=list(evolution.get("operators") or ["mutate", "crossover", "explore"]),
            prune_strategy=evolution.get("prune_strategy", "pareto"),
        ),
        runtime_mode=RuntimeModeSpec(
            equivalence_check=runtime_mode.get("equivalence_check", "required"),
            property_test_samples=int(runtime_mode.get("property_test_samples", 500)),
            regression_tests=runtime_mode.get("regression_tests"),
        ),
        safety=SafetySpec(
            protected_branch=safety.get("protected_branch", "main"),
            agents_can_merge=False,
            require_human_approval=bool(safety.get("require_human_approval", True)),
            final_pr_reviewers=list(safety.get("final_pr_reviewers", []) or []),
        ),
        backend=BackendSpec(
            type=_require(backend, "type", str, source, ctx="backend."),
            repo=backend.get("repo"),
            root_dir=backend.get("root_dir"),
        ),
    )


def _parse_metric(raw: dict[str, Any], source: Path) -> Metric:
    name = _require(raw, "name", str, source, ctx="metric.")
    direction = _require(raw, "optimise", str, source, ctx="metric.")
    try:
        optimise = OptimiseDirection(direction)
    except ValueError as e:
        raise ManifestError(
            f"{source}: metric '{name}' has invalid optimise '{direction}' — must be 'minimize' or 'maximize'"
        ) from e
    return Metric(
        name=name,
        optimise=optimise,
        minimum=raw.get("minimum"),
        maximum=raw.get("maximum"),
    )


def _require(d: dict[str, Any], key: str, expected: type, source: Path, *, ctx: str = "") -> Any:
    if key not in d:
        raise ManifestError(f"{source}: missing required field '{ctx}{key}'")
    value = d[key]
    if not isinstance(value, expected):
        raise ManifestError(
            f"{source}: field '{ctx}{key}' must be {expected.__name__}, got {type(value).__name__}"
        )
    return value
