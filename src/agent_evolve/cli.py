"""Minimal CLI entry point.

The heavy lifting is done by agents reading the SKILL.md files and invoking the
Python tooling directly. This CLI exposes three utilities a human operator
needs: ``init`` (scaffold a manifest), ``validate`` (check the manifest + scope),
and ``report`` (rebuild the HTML report from existing state).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_evolve.config import ManifestError, load_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-evolve", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Validate agent-evolve.yaml")
    p_validate.add_argument("manifest", type=Path)

    p_report = sub.add_parser("report", help="Rebuild evolve-report.html from state")
    p_report.add_argument("state_dir", type=Path)
    p_report.add_argument("--output", type=Path, default=Path("evolve-report.html"))

    args = parser.parse_args(argv)

    if args.command == "validate":
        return _cmd_validate(args.manifest)
    if args.command == "report":
        return _cmd_report(args.state_dir, args.output)
    parser.error(f"unknown command {args.command}")
    return 2


def _cmd_validate(manifest: Path) -> int:
    try:
        spec = load_manifest(manifest)
    except ManifestError as e:
        print(f"invalid manifest: {e}", file=sys.stderr)
        return 1
    print(f"ok — {spec.description!r} ({spec.mode} mode, backend={spec.backend.type})")
    return 0


def _cmd_report(state_dir: Path, output: Path) -> int:
    from agent_evolve.viz import build_graph, render_html
    from agent_evolve.models import Candidate
    import json

    candidates_dir = state_dir / "candidates"
    if not candidates_dir.exists():
        print(f"no candidates directory at {candidates_dir}", file=sys.stderr)
        return 1
    candidates = [
        Candidate.from_dict(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(candidates_dir.glob("*.json"))
    ]
    graph = build_graph(candidates)
    render_html(graph, str(output))
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
