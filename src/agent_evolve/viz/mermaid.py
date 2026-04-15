"""Mermaid renderer for the evolution graph.

GitHub renders Mermaid natively inside Issue and PR bodies, so we emit plain
Mermaid source. Node colors follow the plan spec:

* green — winner
* grey  — pruned
* blue  — active
* red   — rejected
* tan   — pending
"""

from __future__ import annotations

from agent_evolve.viz.graph import EvolutionGraph, Node, NodeColor


_STYLE: dict[NodeColor, str] = {
    "winner": "fill:#2d8a4e,color:#fff,stroke:#1b5e37,stroke-width:2px",
    "pruned": "fill:#888,color:#fff,stroke:#555",
    "active": "fill:#2c74b3,color:#fff,stroke:#174978",
    "rejected": "fill:#b34747,color:#fff,stroke:#7a2a2a",
    "pending": "fill:#d4b483,color:#111,stroke:#8c7148",
}


def render_mermaid(graph: EvolutionGraph) -> str:
    """Produce a Mermaid ``graph TD`` source string for *graph*."""
    lines = ["```mermaid", "graph TD"]

    for node in graph.nodes:
        lines.append(f'    {node.id}["{_escape(node.label)}"]')

    lines.append("")
    for edge in graph.edges:
        lines.append(f"    {edge.parent_id} --> {edge.child_id}")

    lines.append("")
    for node in graph.nodes:
        lines.append(f"    style {node.id} {_STYLE[node.color]}")

    lines.append("```")
    return "\n".join(lines) + "\n"


def _escape(label: str) -> str:
    """Make a label safe inside Mermaid quoted node text."""
    return label.replace('"', "\\\"").replace("\n", "<br/>")


def render_legend() -> str:
    """Small standalone Mermaid snippet explaining the colour code."""
    return "\n".join(
        [
            "```mermaid",
            "graph LR",
            '    W["winner"]',
            '    A["active / approved"]',
            '    P["pending / in review"]',
            '    R["rejected"]',
            '    X["pruned"]',
            f"    style W {_STYLE['winner']}",
            f"    style A {_STYLE['active']}",
            f"    style P {_STYLE['pending']}",
            f"    style R {_STYLE['rejected']}",
            f"    style X {_STYLE['pruned']}",
            "```",
            "",
        ]
    )


def _node_signature(node: Node) -> str:  # kept for future use by diff views
    return f"{node.id}:{node.color}"
