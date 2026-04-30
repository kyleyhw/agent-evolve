"""Manifest loader — turns ``agent-evolve.yaml`` into a :class:`ProblemSpec`.

Minimal configs are just a handful of lines; everything else has a default
defined on the dataclasses in :mod:`agent_evolve.models`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent_evolve.git_utils import detect_default_branch
from agent_evolve.models import (
    AgentsSpec,
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
    agents = raw.get("agents", {}) or {}

    metrics_raw = problem.get("metrics", [])
    if not metrics_raw:
        raise ManifestError(f"{source}: problem.metrics must list at least one metric")

    metrics = [_parse_metric(m, source) for m in metrics_raw]
    primary_count = sum(1 for m in metrics if m.primary)
    if primary_count > 1:
        primary_names = ", ".join(repr(m.name) for m in metrics if m.primary)
        raise ManifestError(
            f"{source}: at most one metric may set primary: true — "
            f"got {primary_count} ({primary_names})"
        )

    return ProblemSpec(
        version=raw.get("version", 1),
        description=_require(problem, "description", str, source, ctx="problem."),
        mode=problem.get("mode", "algorithm"),
        eval_command=_require(problem, "eval_command", str, source, ctx="problem."),
        eval_cwd=_optional_str(problem.get("eval_cwd")),
        expected_baseline=_parse_expected_baseline(problem.get("expected_baseline"), source),
        expected_baseline_tolerance=_parse_tolerance(
            problem.get("expected_baseline_tolerance", 0.05), source
        ),
        production_runner=_optional_str(problem.get("production_runner")),
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
            protected_branch=_resolve_protected_branch(safety, source.parent),
            agents_can_merge=False,
            require_human_approval=bool(safety.get("require_human_approval", True)),
            final_pr_reviewers=list(safety.get("final_pr_reviewers", []) or []),
            branch_cleanup=_parse_branch_cleanup(safety.get("branch_cleanup", "archive"), source),
            run_ablation_report=bool(safety.get("run_ablation_report", True)),
        ),
        backend=BackendSpec(
            type=_require(backend, "type", str, source, ctx="backend."),
            repo=backend.get("repo"),
            root_dir=backend.get("root_dir"),
        ),
        agents=AgentsSpec(
            supervisor=str(agents.get("supervisor", "claude")),
            explorer=_parse_explorer_value(agents.get("explorer", "claude"), source=source),
            reviewer=str(agents.get("reviewer", "claude")),
        ),
    )


def _optional_str(value: Any) -> str | None:
    """Coerce an optional manifest field to a non-empty string or ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _parse_expected_baseline(value: Any, source: Path) -> dict[str, float] | None:
    """Validate ``problem.expected_baseline`` is a ``{metric: number}`` mapping."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ManifestError(
            f"{source}: problem.expected_baseline must be a mapping of "
            f"metric name -> expected value, got {type(value).__name__}"
        )
    out: dict[str, float] = {}
    for k, v in value.items():
        if not isinstance(k, str):
            raise ManifestError(
                f"{source}: problem.expected_baseline keys must be strings; "
                f"got {type(k).__name__}"
            )
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ManifestError(
                f"{source}: problem.expected_baseline['{k}'] must be a number; "
                f"got {type(v).__name__}"
            )
        out[k] = float(v)
    return out


def _parse_tolerance(value: Any, source: Path) -> float:
    """Validate ``problem.expected_baseline_tolerance`` is a non-negative number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(
            f"{source}: problem.expected_baseline_tolerance must be a number; "
            f"got {type(value).__name__}"
        )
    fv = float(value)
    if fv < 0:
        raise ManifestError(
            f"{source}: problem.expected_baseline_tolerance must be non-negative; "
            f"got {fv}"
        )
    return fv


_BRANCH_CLEANUP_VALUES = ("keep", "archive", "delete")


def _parse_branch_cleanup(value: Any, source: Path) -> str:
    """Validate ``safety.branch_cleanup`` is one of ``keep|archive|delete``."""
    if not isinstance(value, str) or value not in _BRANCH_CLEANUP_VALUES:
        raise ManifestError(
            f"{source}: safety.branch_cleanup must be one of "
            f"{_BRANCH_CLEANUP_VALUES}; got {value!r}"
        )
    return value


def _resolve_protected_branch(safety: dict[str, Any], cwd: Path) -> str:
    """Pick the protected-branch name for a manifest.

    If the YAML explicitly names ``safety.protected_branch``, the user's
    choice wins verbatim (including, deliberately, the case where the
    user names a branch that does not exist locally — surfacing the typo
    is more useful than silently rewriting their intent).

    Otherwise, auto-detect from the manifest directory's git state. The
    detector inspects ``origin/HEAD`` first, then the current branch,
    and finally falls back to ``"main"`` so there is always *some*
    string to populate the dataclass with.
    """
    explicit = safety.get("protected_branch")
    if isinstance(explicit, str) and explicit:
        return explicit
    return detect_default_branch(cwd)


def _parse_explorer_value(value: Any, *, source: Path) -> str | list[str]:
    """Accept either a single agent name or a list (ensemble).

    A list with a single element is normalised back to a string so the
    serialized form stays identical to the singleton case — no semantic
    difference between ``explorer: claude`` and ``explorer: [claude]``.
    Lists with two or more elements are kept as lists; the supervisor
    SKILL distributes round slots round-robin across them.
    """
    if value is None:
        return "claude"
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not value:
            raise ManifestError(
                f"{source}: agents.explorer is an empty list — must be a string or a non-empty list of strings"
            )
        for item in value:
            if not isinstance(item, str):
                raise ManifestError(
                    f"{source}: agents.explorer entries must be strings; got {type(item).__name__}"
                )
        if len(value) == 1:
            return value[0]
        return list(value)
    raise ManifestError(
        f"{source}: agents.explorer must be a string or list of strings; got {type(value).__name__}"
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
    primary = raw.get("primary", False)
    if not isinstance(primary, bool):
        raise ManifestError(
            f"{source}: metric '{name}' has non-boolean primary {primary!r} — must be true or false"
        )
    return Metric(
        name=name,
        optimise=optimise,
        minimum=raw.get("minimum"),
        maximum=raw.get("maximum"),
        primary=primary,
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
