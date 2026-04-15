"""Eval command runner.

Runs the ``problem.eval_command`` from the manifest, captures stdout/stderr,
and extracts metrics. Two parsing strategies, tried in order:

1. **JSON on stdout** (preferred). The last well-formed JSON object seen in
   stdout is treated as the metrics payload.
2. **KEY=VALUE lines** as a fallback (e.g. ``duration_ms=42.1``).

Commands that fail the manifest's hard constraints or return a non-zero exit
status are returned with ``passed=False``. The supervisor decides what to do
with that — typically records the metrics as-is and lets the reviewer reject.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float
    metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = False
    parse_error: str | None = None

    @property
    def score(self) -> float | None:
        """First numeric metric, as a convenience for single-metric problems."""
        for v in self.metrics.values():
            if isinstance(v, (int, float)):
                return float(v)
        return None


def run_eval(
    command: str | list[str],
    *,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> EvalResult:
    """Run an eval command and capture its metrics.

    *command* may be a POSIX-shell-style string or a pre-tokenised ``list[str]``.
    Strings are split with :func:`shlex.split` in POSIX mode on all platforms —
    this means quoted arguments work uniformly and Windows paths should use
    forward slashes (e.g. ``"python C:/app/run.py"``) or be passed as a list.
    """
    argv = list(command) if isinstance(command, list) else shlex.split(command)
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        duration = (time.perf_counter() - start) * 1000.0
        return EvalResult(
            command=_display(command),
            returncode=-1,
            stdout=(e.stdout or "").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
            stderr=f"timeout after {timeout}s",
            duration_ms=duration,
            passed=False,
            parse_error="timeout",
        )

    duration = (time.perf_counter() - start) * 1000.0
    metrics, parse_error = _extract_metrics(proc.stdout)
    passed = proc.returncode == 0 and parse_error is None
    return EvalResult(
        command=_display(command),
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_ms=duration,
        metrics=metrics,
        passed=passed,
        parse_error=parse_error,
    )


def _display(command: str | list[str]) -> str:
    return command if isinstance(command, str) else shlex.join(command)


_KV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$")


def _extract_metrics(stdout: str) -> tuple[dict[str, float], str | None]:
    metrics, err = _extract_json(stdout)
    if metrics:
        return metrics, err

    kv: dict[str, float] = {}
    for line in stdout.splitlines():
        m = _KV_LINE.match(line.strip())
        if m:
            try:
                kv[m.group(1)] = float(m.group(2))
            except ValueError:
                continue
    if kv:
        return kv, None

    return {}, "no metrics found (expected a JSON object or KEY=VALUE lines on stdout)"


def _extract_json(stdout: str) -> tuple[dict[str, float], str | None]:
    """Find the last well-formed top-level JSON object in *stdout*."""
    last_obj: dict[str, Any] | None = None

    for block in _candidate_json_blocks(stdout):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            last_obj = parsed

    if last_obj is None:
        return {}, None

    flat = _flatten_metrics(last_obj)
    if not flat:
        return {}, "JSON payload has no numeric fields"
    return flat, None


def _candidate_json_blocks(text: str) -> list[str]:
    """Yield substrings of *text* that look like top-level JSON objects.

    Naive bracket matcher — good enough for the common cases (pytest benchmark
    json-report, a handwritten ``print(json.dumps(...))``, or a JSON-lines
    tail).
    """
    blocks: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    blocks.append(text[start : i + 1])
                    start = -1
    return blocks


def _flatten_metrics(obj: dict[str, Any], *, prefix: str = "") -> dict[str, float]:
    """Turn a possibly-nested dict into a flat ``dotted.key → float`` mapping."""
    out: dict[str, float] = {}
    for k, v in obj.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, bool):
            out[key] = 1.0 if v else 0.0
        elif isinstance(v, (int, float)):
            out[key] = float(v)
        elif isinstance(v, dict):
            out.update(_flatten_metrics(v, prefix=key))
    return out
