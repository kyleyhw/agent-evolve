"""Eval runner tests."""

from __future__ import annotations

import sys
from pathlib import Path

from agent_evolve.eval import run_eval


def _py(code: str, tmp_path: Path) -> str:
    script = tmp_path / "script.py"
    script.write_text(code, encoding="utf-8")
    # Use forward slashes even on Windows so shlex does not eat backslashes.
    return f'"{sys.executable}" "{script.as_posix()}"'


def test_json_stdout_parsed(tmp_path):
    code = (
        "import json\n"
        "print('some log')\n"
        "print(json.dumps({'duration_ms': 42.1, 'pass_rate': 1.0}))\n"
    )
    r = run_eval(_py(code, tmp_path))
    assert r.passed
    assert r.returncode == 0
    assert r.metrics["duration_ms"] == 42.1
    assert r.metrics["pass_rate"] == 1.0


def test_nested_json_flattened(tmp_path):
    code = (
        "import json\n"
        "print(json.dumps({'perf': {'duration_ms': 88.0}, 'pass_rate': 1.0}))\n"
    )
    r = run_eval(_py(code, tmp_path))
    assert r.metrics["perf.duration_ms"] == 88.0
    assert r.metrics["pass_rate"] == 1.0


def test_kv_fallback(tmp_path):
    code = "print('duration_ms=88.2')\nprint('pass_rate=1.0')\n"
    r = run_eval(_py(code, tmp_path))
    assert r.passed
    assert r.metrics == {"duration_ms": 88.2, "pass_rate": 1.0}


def test_nonzero_exit_marks_failed(tmp_path):
    code = "import sys; print('duration_ms=1.0'); sys.exit(2)"
    r = run_eval(_py(code, tmp_path))
    assert not r.passed
    assert r.returncode == 2
    assert r.metrics == {"duration_ms": 1.0}


def test_no_metrics_marks_parse_error(tmp_path):
    code = "print('no metrics here')"
    r = run_eval(_py(code, tmp_path))
    assert not r.passed
    assert r.parse_error and "no metrics" in r.parse_error


def test_last_json_object_wins(tmp_path):
    code = (
        "import json\n"
        "print(json.dumps({'duration_ms': 1000.0}))\n"
        "print(json.dumps({'duration_ms': 10.0, 'pass_rate': 1.0}))\n"
    )
    r = run_eval(_py(code, tmp_path))
    assert r.metrics["duration_ms"] == 10.0
    assert r.metrics["pass_rate"] == 1.0


def test_timeout_returns_structured_result(tmp_path):
    code = "import time; time.sleep(5)"
    r = run_eval(_py(code, tmp_path), timeout=0.5)
    assert not r.passed
    assert r.parse_error == "timeout"


def test_score_convenience_returns_first_numeric(tmp_path):
    code = "import json; print(json.dumps({'duration_ms': 42.1, 'pass_rate': 1.0}))"
    r = run_eval(_py(code, tmp_path))
    assert r.score == 42.1


def test_scratch_exported_and_distinct_per_call(tmp_path):
    """``AGENT_EVOLVE_SCRATCH`` reaches the child, the directory is created
    by the runner, and two calls with different *scratch* values see
    different paths — the per-candidate guard against absolute-path
    collisions across concurrent worktrees."""
    code = (
        "import json, os\n"
        "print(json.dumps({'scratch_set': 'AGENT_EVOLVE_SCRATCH' in os.environ}))\n"
        "print('SCRATCH=' + os.environ.get('AGENT_EVOLVE_SCRATCH', ''))\n"
    )
    cmd = _py(code, tmp_path)
    s1 = tmp_path / "cand-1.scratch"
    s2 = tmp_path / "cand-2.scratch"
    r1 = run_eval(cmd, scratch=s1)
    r2 = run_eval(cmd, scratch=s2)

    assert r1.metrics["scratch_set"] == 1.0
    assert s1.is_dir() and s2.is_dir()  # created by the runner, not the eval
    seen1 = r1.stdout.strip().splitlines()[-1].removeprefix("SCRATCH=")
    seen2 = r2.stdout.strip().splitlines()[-1].removeprefix("SCRATCH=")
    assert seen1 == str(s1)
    assert seen2 == str(s2)
    assert seen1 != seen2


def test_partial_env_merges_over_parent(tmp_path):
    """A partial *env* dict must not wipe the inherited environment: PATH
    survives alongside the extra variable. (Under the old replace
    semantics a child Python on Windows would not even start — no
    SYSTEMROOT — so this pins the merge behaviour.)"""
    code = (
        "import json, os\n"
        "print(json.dumps({\n"
        "    'has_path': bool(os.environ.get('PATH')),\n"
        "    'marker_ok': os.environ.get('AGENT_EVOLVE_MARKER') == 'x',\n"
        "}))\n"
    )
    r = run_eval(_py(code, tmp_path), env={"AGENT_EVOLVE_MARKER": "x"})
    assert r.passed
    assert r.metrics["has_path"] == 1.0
    assert r.metrics["marker_ok"] == 1.0


def test_no_scratch_means_no_env_var(tmp_path, monkeypatch):
    """Without *scratch* the runner must not invent ``AGENT_EVOLVE_SCRATCH``
    (delenv guards against one inherited from the test session itself)."""
    monkeypatch.delenv("AGENT_EVOLVE_SCRATCH", raising=False)
    code = (
        "import json, os\n"
        "print(json.dumps({'scratch_set': 'AGENT_EVOLVE_SCRATCH' in os.environ}))\n"
    )
    r = run_eval(_py(code, tmp_path))
    assert r.metrics["scratch_set"] == 0.0


def test_validate_baseline_passes_within_tolerance():
    """Drift inside tolerance yields ``matches=True``."""
    from agent_evolve.eval import validate_baseline

    check = validate_baseline(
        measured={"sharpe": 1.05, "drawdown": -0.12},
        expected={"sharpe": 1.0, "drawdown": -0.12},
        tolerance=0.10,
    )
    assert check.matches is True
    assert "sharpe" in check.drifts and abs(check.drifts["sharpe"] - 0.05) < 1e-9
    assert "matches" in check.message.lower()


def test_validate_baseline_fails_when_drift_exceeds_tolerance():
    """A drift outside tolerance yields ``matches=False`` with a per-metric line."""
    from agent_evolve.eval import validate_baseline

    check = validate_baseline(
        measured={"sharpe": 0.5},
        expected={"sharpe": 1.0},
        tolerance=0.10,
    )
    assert check.matches is False
    assert "sharpe" in check.message
    assert check.drifts["sharpe"] < 0


def test_validate_baseline_records_missing_metrics():
    """A measured set missing an expected metric is a fail with an explanation."""
    from agent_evolve.eval import validate_baseline

    check = validate_baseline(
        measured={"sharpe": 1.0},
        expected={"sharpe": 1.0, "drawdown": -0.10},
        tolerance=0.05,
    )
    assert check.matches is False
    assert "drawdown" in check.missing
    assert "drawdown" in check.message


def test_validate_baseline_zero_expected_handled_safely():
    """``expected == 0`` matches ``measured == 0`` and rejects anything else."""
    import math
    from agent_evolve.eval import validate_baseline

    same = validate_baseline(
        measured={"x": 0.0}, expected={"x": 0.0}, tolerance=0.0,
    )
    assert same.matches is True

    diff = validate_baseline(
        measured={"x": 1e-9}, expected={"x": 0.0}, tolerance=0.5,
    )
    assert diff.matches is False
    assert math.isinf(diff.drifts["x"])


def test_validate_baseline_skipped_when_expected_is_none():
    """A ``None`` expected baseline returns ``matches=True`` with a skip message."""
    from agent_evolve.eval import validate_baseline

    check = validate_baseline(
        measured={"a": 1.0}, expected=None, tolerance=0.05,
    )
    assert check.matches is True
    assert "no expected_baseline" in check.message
