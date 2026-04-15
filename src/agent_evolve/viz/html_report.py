"""Interactive HTML report.

Produces a single self-contained ``evolve-report.html`` — D3.js from CDN, no
build step. Open it directly in a browser or commit it to the repo root so the
search tree is visible without running anything.

Features

* Force-directed tree (drag, zoom, pan)
* Click any node to expand its full ``EVOLVE_STATE`` side panel
* Timeline / lineage layout toggle
* Export as PNG button
* Colour coding matches the Mermaid renderer: green winner, blue active,
  grey pruned, red rejected, tan pending
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_evolve.viz.graph import EvolutionGraph, Node


_COLORS: dict[str, str] = {
    "winner": "#2d8a4e",
    "active": "#2c74b3",
    "pruned": "#8a8a8a",
    "rejected": "#b34747",
    "pending": "#d4b483",
}


def render_html(graph: EvolutionGraph, output_path: str | Path) -> Path:
    """Write a standalone HTML report to *output_path*."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_build_html(graph), encoding="utf-8")
    return path


def _build_html(graph: EvolutionGraph) -> str:
    data = _to_payload(graph)
    data_json = _embed_safe_json(data)
    title = _html_escape(graph.title)

    return _TEMPLATE.replace("__TITLE__", title).replace(
        "__DATA_JSON__", data_json
    ).replace("__COLORS__", _embed_safe_json(_COLORS))


def _embed_safe_json(data: Any) -> str:
    """JSON-encode *data* for safe embedding inside a ``<script>`` block.

    ``json.dumps`` does not escape ``/``, so a string like ``"</script>"`` in
    user-supplied content would prematurely close the tag. We escape ``</`` →
    ``<\\/``; JS parses the result identically but the HTML tokenizer no longer
    sees a closing tag. We also escape U+2028 / U+2029 for the same reason.
    """
    raw = json.dumps(data, indent=2)
    return (
        raw.replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _to_payload(graph: EvolutionGraph) -> dict[str, Any]:
    return {
        "title": graph.title,
        "problem_id": graph.problem_id,
        "winner_id": graph.winner_id,
        "nodes": [_serialize_node(n) for n in graph.nodes],
        "edges": [{"source": e.parent_id, "target": e.child_id} for e in graph.edges],
    }


def _serialize_node(n: Node) -> dict[str, Any]:
    return {
        "id": n.id,
        "kind": n.kind,
        "label": n.label,
        "color": n.color,
        "operator": n.operator,
        "round": n.round,
        "status": n.status,
        "metrics": n.metrics,
        "hypothesis": n.hypothesis,
        "conclusion": n.conclusion,
        "verdict": n.verdict,
    }


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>__TITLE__ — agent-evolve report</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
<style>
  :root {
    --bg: #0f1115;
    --bg-panel: #161a22;
    --border: #242a35;
    --text: #e6e8ec;
    --muted: #9aa3b2;
    --accent: #2c74b3;
    --accent-dim: #1b4a72;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text);
    font: 14px/1.45 "Inter", "Segoe UI", -apple-system, sans-serif; }
  header { padding: 14px 20px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 16px; justify-content: space-between; }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; letter-spacing: .01em; }
  header .sub { color: var(--muted); font-size: 12px; margin-left: 8px; }
  .toolbar { display: flex; gap: 8px; align-items: center; }
  button {
    background: var(--bg-panel); color: var(--text); border: 1px solid var(--border);
    padding: 6px 12px; font: inherit; border-radius: 6px; cursor: pointer;
  }
  button:hover { border-color: var(--accent); }
  button.active { background: var(--accent-dim); border-color: var(--accent); }
  main { display: grid; grid-template-columns: 1fr 360px; height: calc(100vh - 55px); }
  #graph { position: relative; overflow: hidden; }
  #graph svg { width: 100%; height: 100%; display: block; cursor: grab; }
  #graph svg:active { cursor: grabbing; }
  .node-circle { cursor: pointer; stroke: #0b0d12; stroke-width: 2px;
    transition: r .15s ease, stroke-width .15s ease; }
  .node-circle:hover { stroke: var(--text); stroke-width: 3px; }
  .node-circle.selected { stroke: var(--text); stroke-width: 3px; }
  .node-label { fill: var(--text); font-size: 11px; pointer-events: none;
    dominant-baseline: middle; }
  .node-sub   { fill: var(--muted); font-size: 10px; pointer-events: none;
    dominant-baseline: middle; }
  .link { stroke: #334154; stroke-width: 1.5px; fill: none; }
  aside {
    background: var(--bg-panel); border-left: 1px solid var(--border);
    padding: 18px 20px; overflow-y: auto;
  }
  aside h2 { margin: 0 0 4px; font-size: 14px; letter-spacing: .02em; }
  aside .badge {
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 11px; margin: 4px 4px 8px 0; background: #222a36; color: var(--text);
    border: 1px solid var(--border);
  }
  aside .section { margin: 16px 0; }
  aside .section h3 { margin: 0 0 6px; font-size: 12px;
    color: var(--muted); text-transform: uppercase; letter-spacing: .08em; }
  aside .metric { display: flex; justify-content: space-between;
    border-bottom: 1px dashed var(--border); padding: 4px 0; }
  aside .metric .val { font-variant-numeric: tabular-nums; color: var(--text); }
  aside pre {
    background: #10141b; border: 1px solid var(--border); border-radius: 6px;
    padding: 10px 12px; white-space: pre-wrap; word-break: break-word;
    font: 12px/1.45 "JetBrains Mono", "Fira Code", Consolas, monospace;
    color: var(--text);
  }
  aside .empty { color: var(--muted); font-style: italic; }
  footer { position: absolute; bottom: 10px; right: 12px; font-size: 11px; color: var(--muted); }
  .legend { display: flex; gap: 12px; align-items: center; font-size: 11px; }
  .legend-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 4px; }
</style>
</head>
<body>
<header>
  <div style="display: flex; align-items: baseline; gap: 10px;">
    <h1 id="title">__TITLE__</h1>
    <span class="sub" id="subtitle"></span>
  </div>
  <div class="toolbar">
    <div class="legend" id="legend"></div>
    <button id="btn-lineage" class="active">Lineage</button>
    <button id="btn-timeline">Timeline</button>
    <button id="btn-export">Export PNG</button>
  </div>
</header>
<main>
  <div id="graph"><svg></svg><footer id="status">click a node to inspect</footer></div>
  <aside id="panel">
    <h2>Inspector</h2>
    <p class="empty">Select a candidate on the left to see its EVOLVE_STATE.</p>
  </aside>
</main>

<script>
const DATA = __DATA_JSON__;
const COLORS = __COLORS__;

(function init() {
  document.getElementById("subtitle").textContent =
    `${DATA.nodes.filter(n => n.kind === 'candidate').length} candidates  ·  problem ${DATA.problem_id}`;

  const legend = document.getElementById("legend");
  for (const [name, color] of Object.entries(COLORS)) {
    const el = document.createElement("span");
    el.innerHTML = `<span class="legend-dot" style="background:${color}"></span>${name}`;
    legend.appendChild(el);
  }

  const svg = d3.select("#graph svg");
  const { clientWidth: w, clientHeight: h } = document.getElementById("graph");
  svg.attr("viewBox", [0, 0, w, h]);

  const zoomG = svg.append("g");
  svg.call(d3.zoom().scaleExtent([0.3, 3]).on("zoom", (ev) => zoomG.attr("transform", ev.transform)));

  const linkG = zoomG.append("g").attr("class", "links");
  const nodeG = zoomG.append("g").attr("class", "nodes");

  let mode = "lineage";
  let selectedId = null;

  function render() {
    linkG.selectAll("*").remove();
    nodeG.selectAll("*").remove();

    if (mode === "timeline") renderTimeline(w, h);
    else renderLineage(w, h);
  }

  function renderLineage(width, height) {
    const simulation = d3.forceSimulation(DATA.nodes)
      .force("link", d3.forceLink(DATA.edges).id(d => d.id).distance(90).strength(0.7))
      .force("charge", d3.forceManyBody().strength(-450))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius(42));

    const links = linkG.selectAll("line").data(DATA.edges).join("line").attr("class", "link");
    drawNodes(simulation);

    simulation.on("tick", () => {
      links.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
           .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      nodeG.selectAll("g.node").attr("transform", d => `translate(${d.x},${d.y})`);
    });
  }

  function renderTimeline(width, height) {
    const rounds = DATA.nodes.filter(n => n.kind === 'candidate').map(n => n.round).filter(r => r != null);
    const maxRound = rounds.length ? Math.max(...rounds) : 1;
    const laneCount = {};
    for (const n of DATA.nodes) {
      const r = n.kind === 'root' ? 0 : (n.round ?? 1);
      laneCount[r] = (laneCount[r] || 0) + 1;
    }
    const placed = {};
    const yStep = (width - 160) / (maxRound + 1);

    DATA.nodes.forEach(n => {
      const r = n.kind === 'root' ? 0 : (n.round ?? 1);
      const i = placed[r] = (placed[r] || 0);
      placed[r]++;
      const total = laneCount[r];
      n.fx = 100 + r * yStep;
      n.fy = height * (i + 1) / (total + 1);
    });

    const sim = d3.forceSimulation(DATA.nodes)
      .force("link", d3.forceLink(DATA.edges).id(d => d.id).distance(90).strength(0.6))
      .force("collision", d3.forceCollide().radius(36))
      .alphaDecay(0.1);

    const links = linkG.selectAll("path").data(DATA.edges).join("path").attr("class", "link");
    drawNodes(sim);

    sim.on("tick", () => {
      links.attr("d", d => {
        const mx = (d.source.x + d.target.x) / 2;
        return `M${d.source.x},${d.source.y} C${mx},${d.source.y} ${mx},${d.target.y} ${d.target.x},${d.target.y}`;
      });
      nodeG.selectAll("g.node").attr("transform", d => `translate(${d.x},${d.y})`);
    });
  }

  function drawNodes(simulation) {
    const g = nodeG.selectAll("g.node").data(DATA.nodes).join("g")
      .attr("class", "node")
      .call(d3.drag()
        .on("start", (ev, d) => { if (!ev.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
        .on("end",  (ev, d) => { if (!ev.active) simulation.alphaTarget(0); if (mode === 'lineage') { d.fx = null; d.fy = null; } }));

    g.append("circle")
      .attr("class", "node-circle")
      .attr("r", d => d.kind === 'root' ? 26 : (d.id === DATA.winner_id ? 22 : 18))
      .attr("fill", d => d.kind === 'root' ? "#3b4358" : COLORS[d.color] || "#888")
      .on("click", (_ev, d) => selectNode(d));

    g.append("text")
      .attr("class", "node-label")
      .attr("text-anchor", "middle").attr("dy", -2)
      .text(d => d.kind === 'root' ? 'ROOT' : `#${d.id.replace('c','')}`);

    g.append("text")
      .attr("class", "node-sub")
      .attr("text-anchor", "middle").attr("dy", 12)
      .text(d => {
        if (d.kind === 'root') return '';
        const m = d.metrics || {};
        const keys = Object.keys(m);
        if (!keys.length) return d.status || '';
        const k = keys[0];
        const v = m[k];
        return (k.includes('ms') || k.includes('duration')) ? `${Math.round(v)}ms` : `${v}`;
      });
  }

  function selectNode(d) {
    selectedId = d.id;
    nodeG.selectAll(".node-circle").classed("selected", n => n.id === selectedId);
    const panel = document.getElementById("panel");
    if (d.kind === 'root') {
      panel.innerHTML = `<h2>${DATA.title}</h2><p class="empty">Root node — click a candidate for details.</p>`;
      return;
    }
    const metrics = d.metrics || {};
    const metricRows = Object.keys(metrics).length
      ? Object.entries(metrics).map(([k, v]) =>
          `<div class="metric"><span>${esc(k)}</span><span class="val">${esc(String(v))}</span></div>`).join("")
      : '<p class="empty">No metrics recorded.</p>';

    const verdictBadge = d.verdict
      ? `<span class="badge" style="background:${verdictColor(d.verdict)}">${esc(d.verdict)}</span>`
      : '';

    panel.innerHTML = `
      <h2>candidate-${esc(d.id.replace('c',''))}</h2>
      <div>
        <span class="badge">${esc(d.operator || '—')}</span>
        <span class="badge">round ${esc(String(d.round ?? '—'))}</span>
        <span class="badge" style="background:${COLORS[d.color]}">${esc(d.status || d.color)}</span>
        ${verdictBadge}
      </div>
      <div class="section">
        <h3>Metrics</h3>
        ${metricRows}
      </div>
      <div class="section">
        <h3>Hypothesis</h3>
        ${d.hypothesis ? `<pre>${esc(d.hypothesis)}</pre>` : '<p class="empty">none</p>'}
      </div>
      <div class="section">
        <h3>Conclusion</h3>
        ${d.conclusion ? `<pre>${esc(d.conclusion)}</pre>` : '<p class="empty">none</p>'}
      </div>
    `;
  }

  function verdictColor(v) {
    if (v === 'APPROVE') return '#2d8a4e';
    if (v === 'REJECT') return '#b34747';
    return '#6a6a6a';
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  document.getElementById("btn-lineage").addEventListener("click", () => {
    mode = "lineage";
    document.getElementById("btn-lineage").classList.add("active");
    document.getElementById("btn-timeline").classList.remove("active");
    DATA.nodes.forEach(n => { n.fx = null; n.fy = null; });
    render();
  });
  document.getElementById("btn-timeline").addEventListener("click", () => {
    mode = "timeline";
    document.getElementById("btn-timeline").classList.add("active");
    document.getElementById("btn-lineage").classList.remove("active");
    render();
  });

  document.getElementById("btn-export").addEventListener("click", exportPng);

  function exportPng() {
    const svgEl = document.querySelector("#graph svg");
    const clone = svgEl.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    const style = document.createElement("style");
    style.textContent = [...document.styleSheets]
      .flatMap(s => { try { return [...s.cssRules].map(r => r.cssText); } catch(e) { return []; }})
      .join("\n");
    clone.insertBefore(style, clone.firstChild);
    const serialized = new XMLSerializer().serializeToString(clone);
    const blob = new Blob([serialized], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      const bbox = svgEl.getBoundingClientRect();
      canvas.width = bbox.width * 2;
      canvas.height = bbox.height * 2;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#0f1115";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      canvas.toBlob(b => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(b);
        a.download = `evolve-report-${DATA.problem_id}.png`;
        a.click();
      });
    };
    img.src = url;
  }

  render();
})();
</script>
</body>
</html>
"""
