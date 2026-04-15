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
