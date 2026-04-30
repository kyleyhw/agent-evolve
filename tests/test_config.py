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


def test_metric_primary_flag_parses(tmp_path):
    """``primary: true`` on a single metric is preserved through the parser."""
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: duration_ms, optimise: minimize, primary: true}
            - {name: pass_rate,   optimise: maximize}
        scope: {target_files: [a]}
        backend: {type: local}
    """))
    spec = load_manifest(manifest)
    assert spec.metrics[0].primary is True
    assert spec.metrics[1].primary is False
    assert spec.primary_metric().name == "duration_ms"


def test_metric_primary_defaults_to_first_when_unmarked(tmp_path):
    """No metric marked primary → first metric wins by fallback."""
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: a, optimise: minimize}
            - {name: b, optimise: maximize}
        scope: {target_files: [a]}
        backend: {type: local}
    """))
    spec = load_manifest(manifest)
    assert spec.primary_metric().name == "a"
    assert all(not m.primary for m in spec.metrics)


def test_metric_primary_rejects_multiple_primaries(tmp_path):
    """At most one metric may set primary: true — two raises ManifestError."""
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: a, optimise: minimize, primary: true}
            - {name: b, optimise: minimize, primary: true}
        scope: {target_files: [a]}
        backend: {type: local}
    """))
    with pytest.raises(ManifestError, match="at most one metric may set primary"):
        load_manifest(manifest)


def test_eval_cwd_round_trips(tmp_path):
    """``problem.eval_cwd`` is preserved as a string field."""
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          eval_cwd: bench/
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        backend: {type: local}
    """))
    spec = load_manifest(manifest)
    assert spec.eval_cwd == "bench/"


def test_eval_cwd_defaults_to_none(tmp_path):
    """Omitted ``eval_cwd`` is ``None`` (supervisor falls back to candidate cwd)."""
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
    assert spec.eval_cwd is None


def test_branch_cleanup_defaults_to_archive(tmp_path):
    """The default ``safety.branch_cleanup`` is ``"archive"``."""
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
    assert spec.safety.branch_cleanup == "archive"


def test_branch_cleanup_explicit_keep(tmp_path):
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        safety:
          branch_cleanup: keep
        backend: {type: local}
    """))
    spec = load_manifest(manifest)
    assert spec.safety.branch_cleanup == "keep"


def test_branch_cleanup_rejects_invalid_value(tmp_path):
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        safety:
          branch_cleanup: nuke
        backend: {type: local}
    """))
    with pytest.raises(ManifestError, match="branch_cleanup must be one of"):
        load_manifest(manifest)


def test_expected_baseline_parses_and_validates_types(tmp_path):
    """``expected_baseline`` round-trips as ``dict[str, float]``."""
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          expected_baseline:
            duration_ms: 100
            pass_rate: 1.0
          expected_baseline_tolerance: 0.10
          metrics:
            - {name: duration_ms, optimise: minimize}
        scope: {target_files: [a]}
        backend: {type: local}
    """))
    spec = load_manifest(manifest)
    assert spec.expected_baseline == {"duration_ms": 100.0, "pass_rate": 1.0}
    assert spec.expected_baseline_tolerance == 0.10


def test_expected_baseline_rejects_non_numeric_value(tmp_path):
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          expected_baseline:
            duration_ms: "fast"
          metrics:
            - {name: duration_ms, optimise: minimize}
        scope: {target_files: [a]}
        backend: {type: local}
    """))
    with pytest.raises(ManifestError, match="must be a number"):
        load_manifest(manifest)


def test_expected_baseline_tolerance_must_be_non_negative(tmp_path):
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          expected_baseline_tolerance: -0.1
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        backend: {type: local}
    """))
    with pytest.raises(ManifestError, match="non-negative"):
        load_manifest(manifest)


def test_production_runner_round_trips(tmp_path):
    """``production_runner`` is preserved as a string when set."""
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo fast
          production_runner: echo full
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        backend: {type: local}
    """))
    spec = load_manifest(manifest)
    assert spec.production_runner == "echo full"


def test_run_ablation_report_defaults_to_true(tmp_path):
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
    assert spec.safety.run_ablation_report is True


def test_run_ablation_report_can_be_disabled(tmp_path):
    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        safety:
          run_ablation_report: false
        backend: {type: local}
    """))
    spec = load_manifest(manifest)
    assert spec.safety.run_ablation_report is False


def test_protected_branch_auto_detected_when_yaml_omits_field(tmp_path, monkeypatch):
    """Omitting ``safety.protected_branch`` triggers ``git_utils.detect_default_branch``.

    The loader passes the manifest's directory as ``cwd`` so detection is
    scoped to the target repo. Stub the helper to assert the call shape
    without depending on ``git`` actually being installed in CI.
    """
    seen: dict[str, object] = {}

    def fake_detect(cwd):
        seen["cwd"] = cwd
        return "trunk"

    monkeypatch.setattr("agent_evolve.config.detect_default_branch", fake_detect)

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

    assert spec.safety.protected_branch == "trunk"
    assert seen["cwd"] == manifest.parent


def test_protected_branch_explicit_value_overrides_detection(tmp_path, monkeypatch):
    """An explicit ``safety.protected_branch`` is honoured verbatim — detection is skipped."""

    def fail_if_called(cwd):  # pragma: no cover — must not run
        raise AssertionError("detect_default_branch should not be called when YAML names the branch")

    monkeypatch.setattr("agent_evolve.config.detect_default_branch", fail_if_called)

    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        safety:
          protected_branch: develop
        backend: {type: local}
    """))

    spec = load_manifest(manifest)
    assert spec.safety.protected_branch == "develop"


def test_protected_branch_empty_string_triggers_detection(tmp_path, monkeypatch):
    """An empty string for ``safety.protected_branch`` is treated as "not set"."""

    monkeypatch.setattr("agent_evolve.config.detect_default_branch", lambda cwd: "master")

    manifest = tmp_path / "agent-evolve.yaml"
    manifest.write_text(textwrap.dedent("""
        problem:
          description: x
          mode: algorithm
          eval_command: echo
          metrics:
            - {name: m, optimise: minimize}
        scope: {target_files: [a]}
        safety:
          protected_branch: ""
        backend: {type: local}
    """))

    spec = load_manifest(manifest)
    assert spec.safety.protected_branch == "master"


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
