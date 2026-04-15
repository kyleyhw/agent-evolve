"""Manifest parser tests."""

from __future__ import annotations

import textwrap

import pytest

from agent_evolve.config import ManifestError, load_manifest


def test_parses_example_manifest():
    spec = load_manifest("examples/agent-evolve.yaml")
    assert spec.mode == "runtime"
    assert [m.name for m in spec.metrics] == ["duration_ms", "test_pass_rate"]
    assert spec.scope.max_diff_files == 3
    assert spec.backend.type == "local"


def test_agents_can_merge_forced_false_even_if_yaml_says_true(tmp_path):
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        version: 1
        problem:
          description: x
          mode: algorithm
          eval_command: "echo hi"
          metrics:
            - {name: duration_ms, optimise: minimize}
        scope:
          target_files: ["src/a.py"]
        safety:
          agents_can_merge: true
        backend:
          type: local
    """))
    spec = load_manifest(manifest)
    assert spec.safety.agents_can_merge is False


def test_missing_required_field_raises(tmp_path):
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text("problem:\n  description: no metrics\n  mode: algorithm\n  eval_command: x\nscope: {target_files: [a]}\nbackend: {type: local}\n")
    with pytest.raises(ManifestError):
        load_manifest(manifest)


def test_invalid_optimise_direction(tmp_path):
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: wibble}
        scope: {target_files: [a]}
        backend: {type: local}
    """))
    with pytest.raises(ManifestError, match="invalid optimise"):
        load_manifest(manifest)


def test_metric_hard_constraint_satisfies():
    from agent_evolve.models import Metric, OptimiseDirection
    m = Metric(name="rate", optimise=OptimiseDirection.MAXIMIZE, minimum=1.0)
    assert m.satisfies(1.0)
    assert not m.satisfies(0.99)


def test_missing_manifest_file_raises(tmp_path):
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(tmp_path / "does-not-exist.yaml")
