"""Visualization tests."""

from __future__ import annotations

from pathlib import Path

from agent_evolve.models import Candidate, ReviewerVerdict
from agent_evolve.viz import build_graph, render_html, render_mermaid


def _candidates() -> list[Candidate]:
    return [
        Candidate(problem_id="1", candidate_id="1", operator="explore", round=1,
                  status="pruned", metrics={"duration_ms": 120.0}),
        Candidate(problem_id="1", candidate_id="2", operator="mutate", round=2,
                  parent_ids=["1"], status="approved",
                  metrics={"duration_ms": 88.0},
                  reviewer_verdict=ReviewerVerdict(verdict="APPROVE", reason="", checklist={}, confidence="high")),
        Candidate(problem_id="1", candidate_id="3", operator="crossover", round=3,
                  parent_ids=["2"], status="approved",
                  metrics={"duration_ms": 61.0},
                  reviewer_verdict=ReviewerVerdict(verdict="APPROVE", reason="", checklist={}, confidence="high")),
    ]


def test_build_graph_connects_edges():
    graph = build_graph(_candidates())
    ids = {n.id for n in graph.nodes}
    assert ids == {"ROOT", "c1", "c2", "c3"}
    edges = {(e.parent_id, e.child_id) for e in graph.edges}
    assert ("ROOT", "c1") in edges
    assert ("c1", "c2") in edges
    assert ("c2", "c3") in edges


def test_winner_is_latest_approved_round():
    graph = build_graph(_candidates())
    assert graph.winner_id == "c3"


def test_no_winner_when_nothing_approved():
    pending = [
        Candidate(problem_id="1", candidate_id="1", operator="explore", round=1,
                  status="pending"),
    ]
    graph = build_graph(pending)
    assert graph.winner_id is None


def test_mermaid_output_contains_styles():
    graph = build_graph(_candidates())
    mmd = render_mermaid(graph)
    assert "```mermaid" in mmd
    assert "graph TD" in mmd
    assert "c1 --> c2" in mmd
    assert "c2 --> c3" in mmd
    assert "style c3" in mmd
    assert "fill:#2d8a4e" in mmd  # winner green


def test_html_report_is_self_contained(tmp_path):
    graph = build_graph(_candidates())
    out = render_html(graph, tmp_path / "report.html")
    content = Path(out).read_text(encoding="utf-8")
    assert "<!doctype html>" in content.lower()
    assert "d3.min.js" in content
    assert '"c3"' in content  # node data
    assert "candidate-3" in content or "c3" in content


def test_html_report_encodes_unsafe_strings(tmp_path):
    candidates = [
        Candidate(problem_id="1", candidate_id="1", operator="explore", round=1,
                  hypothesis='<script>alert("xss")</script>',
                  status="pending"),
    ]
    graph = build_graph(candidates)
    out = render_html(graph, tmp_path / "report.html")
    content = Path(out).read_text(encoding="utf-8")
    # Inside a <script> block the HTML tokenizer only ends on "</script".
    # An opening "<script" inside a JS string literal is harmless; we just need
    # to ensure the closing form is escaped.
    payload_region = content.split("<script>", 2)[-1].split("</script>", 1)[0]
    assert "</script" not in payload_region
