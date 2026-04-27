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


def test_agents_default_to_claude_when_block_omitted(tmp_path):
    """Every role defaults to ``"claude"`` when ``agents:`` is absent."""
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        backend: {type: local}
    """))
    spec = load_manifest(manifest)
    assert spec.agents.supervisor == "claude"
    assert spec.agents.explorer == "claude"
    assert spec.agents.reviewer == "claude"


def test_agents_block_assigns_per_role(tmp_path):
    """Specifying ``agents.<role>`` overrides the default for just that role."""
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        backend: {type: local}
        agents:
          reviewer: gemini
          explorer: codex
    """))
    spec = load_manifest(manifest)
    assert spec.agents.supervisor == "claude"
    assert spec.agents.explorer == "codex"
    assert spec.agents.reviewer == "gemini"


def test_agents_explorer_ensemble_parses_as_list(tmp_path):
    """``agents.explorer`` accepts a list — preserved verbatim for round-robin."""
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        backend: {type: local}
        agents:
          explorer: [claude, gemini]
    """))
    spec = load_manifest(manifest)
    assert spec.agents.explorer == ["claude", "gemini"]
    assert spec.agents.explorer_list() == ["claude", "gemini"]


def test_agents_explorer_singleton_list_collapses_to_string(tmp_path):
    """A list with one element is normalised back to the singleton string."""
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        backend: {type: local}
        agents:
          explorer: [gemini]
    """))
    spec = load_manifest(manifest)
    assert spec.agents.explorer == "gemini"
    assert spec.agents.explorer_list() == ["gemini"]


def test_agents_explorer_list_helper_on_default():
    """The default ``"claude"`` string yields ``["claude"]`` from the helper."""
    from agent_evolve.models import AgentsSpec
    assert AgentsSpec().explorer_list() == ["claude"]


def test_agents_explorer_rejects_empty_list(tmp_path):
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        backend: {type: local}
        agents:
          explorer: []
    """))
    with pytest.raises(ManifestError, match="empty list"):
        load_manifest(manifest)


def test_agents_explorer_rejects_non_string_entries(tmp_path):
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        backend: {type: local}
        agents:
          explorer: [claude, 42]
    """))
    with pytest.raises(ManifestError, match="must be strings"):
        load_manifest(manifest)
