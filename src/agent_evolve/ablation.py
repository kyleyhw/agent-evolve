"""Post-hoc ablation report.

After ``finalize()`` selects a winner, the supervisor optionally runs an
ablation pass: split the winner's diff into git hunks, build N variants
each missing one hunk, run the eval on each, and report the per-hunk
contribution. Output is a structured :class:`AblationReport` the
supervisor renders into the final PR body.

The pass answers a question the search itself isn't structured to ask
("which sub-changes in the winner are actually load-bearing?") without
spending search-budget on it. It runs *after* the winner is chosen and
the search is over, so its cost is bounded by the number of hunks in
one diff (typically 3-8 evals) — small compared to the search.

The post-hoc shape is deliberate: it does not promote a hunk-stripped
variant to "new winner" even if such a variant scores better. It only
flags the discrepancy. A human (or a tighter re-run) decides what to do.
This keeps the ablation pass *informational* — it never changes the
selection that ``finalize()`` already committed to.

Implementation notes
--------------------
The hunk-splitting heuristic is git's own. ``git diff`` already chunks
changes into hunks (``@@ -<from>,<count> +<from>,<count> @@``); we use
the line-mode parser :class:`_HunkSpec` to round-trip those into
"remove this hunk" patches.

Inverting a hunk to construct the ablated variant is straightforward:
swap the ``-`` and ``+`` markers on each line, swap the ``@@`` ranges
accordingly, and apply the result with ``git apply -R`` *to the winner's
tree*. The stripped variant is then evaluated like any other candidate.

The pass is best-effort. If a particular hunk cannot be ablated cleanly
(applies-back fails, e.g. because the hunk depends on a previous one),
that hunk is recorded as ``status = "could_not_ablate"`` rather than
aborting the whole report — partial signal beats no signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agent_evolve.eval.runner import run_eval


AblationStatus = Literal["measured", "could_not_ablate", "eval_failed"]


@dataclass
class HunkAblation:
    """Result of measuring a single hunk's contribution.

    ``hunk_id`` is a stable label of the form ``"<path>:<start_line>"`` so
    the same hunk maps to the same row across re-runs (assuming the file
    structure has not shifted under it).

    ``measured_metrics`` is the eval result *with this hunk removed*.
    Compare to the baseline metrics on :class:`AblationReport` to read
    the contribution: a metric that improves when the hunk is removed
    means the hunk hurts that metric; a metric that degrades means the
    hunk carries the gain.
    """

    hunk_id: str
    file: str
    header: str
    lines_added: int
    lines_removed: int
    status: AblationStatus
    measured_metrics: dict[str, float] = field(default_factory=dict)
    error: str | None = None


@dataclass
class AblationReport:
    """Container for the per-hunk ablation results on a single winner.

    The supervisor reads ``rows`` and renders a Markdown table into the
    final PR body. Each row carries enough context for a human reviewer
    to find the hunk in the diff without re-deriving anything.
    """

    winner_id: str
    baseline_metrics: dict[str, float]
    rows: list[HunkAblation] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None


def run_ablation_report(
    *,
    winner_id: str,
    winner_metrics: dict[str, float],
    diff_text: str,
    eval_command: str,
    eval_cwd: str | Path | None,
    apply_ablation: "callable[[HunkSpec], bool] | None" = None,
    eval_timeout_s: float | None = None,
) -> AblationReport:
    """Build an :class:`AblationReport` for the winner's diff.

    The signature is deliberately verbose — the function does not assume
    a backend is wired in, so any caller (tests, supervisor) can supply
    its own ``apply_ablation`` callback that materialises the
    hunk-stripped tree before each eval. The default callback (``None``)
    raises ``NotImplementedError`` and is intended to be replaced by the
    supervisor with a concrete git-apply harness.

    Returning an empty-rows report (rather than raising) is the
    contract for "no hunks found in diff" so the supervisor can render
    a polite "no ablations applicable" line instead of a stack trace.
    """
    hunks = parse_hunks(diff_text)
    report = AblationReport(
        winner_id=winner_id,
        baseline_metrics=dict(winner_metrics),
    )
    if not hunks:
        report.skipped = True
        report.skip_reason = "no hunks parsed from winner diff"
        return report

    if apply_ablation is None:
        report.skipped = True
        report.skip_reason = (
            "no apply_ablation callback supplied — caller must provide a "
            "function that materialises the hunk-stripped tree before "
            "each eval"
        )
        return report

    for hunk in hunks:
        row = HunkAblation(
            hunk_id=f"{hunk.path}:{hunk.new_start}",
            file=hunk.path,
            header=hunk.header,
            lines_added=hunk.lines_added,
            lines_removed=hunk.lines_removed,
            status="measured",
        )
        applied = False
        try:
            applied = apply_ablation(hunk)
        except Exception as e:
            row.status = "could_not_ablate"
            row.error = f"apply_ablation raised: {e}"
        if not applied and row.status == "measured":
            row.status = "could_not_ablate"
            row.error = "apply_ablation returned False"
        if row.status == "measured":
            try:
                eval_result = run_eval(
                    eval_command,
                    cwd=eval_cwd,
                    timeout=eval_timeout_s,
                )
                row.measured_metrics = dict(eval_result.metrics)
                if not eval_result.passed:
                    row.status = "eval_failed"
                    row.error = (
                        eval_result.parse_error
                        or f"eval returncode {eval_result.returncode}"
                    )
            except Exception as e:
                row.status = "eval_failed"
                row.error = f"run_eval raised: {e}"
        report.rows.append(row)

    return report


@dataclass
class HunkSpec:
    """A single hunk parsed out of a unified diff.

    Carries enough metadata for a caller to (a) write a one-row table
    cell describing which hunk this is and (b) construct an ``apply -R``
    patch that strips just this hunk from the working tree.

    The raw ``patch`` text is the full mini-diff for one hunk —
    including the file header and the ``@@ ... @@`` line — so it can be
    fed directly to ``git apply --reverse``.
    """

    path: str
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines_added: int
    lines_removed: int
    patch: str


def parse_hunks(diff_text: str) -> list[HunkSpec]:
    """Parse a unified diff into hunks.

    Recognises the standard ``diff --git a/<path> b/<path>`` header
    followed by zero or more ``@@ -N,M +N,M @@`` hunk markers. Files
    with no hunks (e.g. binary diffs, mode-only changes) are skipped —
    we cannot ablate a binary blob in any useful way.

    The parser is intentionally permissive: it expects the input to
    come from ``git diff`` (or a tool that emits the same shape) and
    bails on anything it does not recognise rather than throwing. The
    caller treats an empty result as "nothing to ablate".
    """
    import re

    hunks: list[HunkSpec] = []
    current_path: str | None = None
    current_file_header: list[str] = []

    lines = diff_text.splitlines(keepends=True)
    i = 0
    file_header_pattern = re.compile(r"^diff --git a/(.*?) b/.*$")
    hunk_pattern = re.compile(
        r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
    )

    while i < len(lines):
        line = lines[i]
        m = file_header_pattern.match(line.rstrip("\n"))
        if m:
            current_path = m.group(1)
            current_file_header = [line]
            i += 1
            # Capture the rest of the file header (index, ---, +++ lines)
            # until the first hunk marker or the next file header.
            while i < len(lines) and not lines[i].startswith("@@") and not lines[i].startswith("diff --git"):
                current_file_header.append(lines[i])
                i += 1
            continue

        hm = hunk_pattern.match(line.rstrip("\n"))
        if hm and current_path is not None:
            old_start = int(hm.group(1))
            old_count = int(hm.group(2)) if hm.group(2) is not None else 1
            new_start = int(hm.group(3))
            new_count = int(hm.group(4)) if hm.group(4) is not None else 1
            tail = hm.group(5) or ""

            body: list[str] = [line]
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.startswith("@@") or nxt.startswith("diff --git"):
                    break
                body.append(nxt)
                j += 1

            lines_added = sum(
                1 for raw in body[1:]
                if raw.startswith("+") and not raw.startswith("+++")
            )
            lines_removed = sum(
                1 for raw in body[1:]
                if raw.startswith("-") and not raw.startswith("---")
            )

            patch = "".join(current_file_header + body)
            hunks.append(
                HunkSpec(
                    path=current_path,
                    header=tail.strip() or f"@@ -{old_start},{old_count} +{new_start},{new_count} @@",
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    lines_added=lines_added,
                    lines_removed=lines_removed,
                    patch=patch,
                )
            )
            i = j
            continue

        i += 1

    return hunks


def render_ablation_markdown(report: AblationReport) -> str:
    """Render an :class:`AblationReport` as a Markdown table for the PR body.

    Returns the empty string when the report was skipped or has no rows
    — callers can concatenate the result unconditionally.
    """
    if report.skipped:
        return f"_Ablation report skipped: {report.skip_reason or 'no reason given'}._\n"
    if not report.rows:
        return "_Ablation report: no hunks were ablatable._\n"

    metric_names = sorted({k for row in report.rows for k in row.measured_metrics.keys()})
    if not metric_names:
        metric_names = sorted(report.baseline_metrics.keys())

    header = ["hunk", "file", "lines (+/-)", "status", *metric_names]
    rows = [header, ["---"] * len(header)]

    for row in report.rows:
        cells = [
            f"`{row.hunk_id}`",
            row.file,
            f"+{row.lines_added}/-{row.lines_removed}",
            row.status,
        ]
        for name in metric_names:
            if row.status != "measured":
                cells.append("—")
                continue
            measured = row.measured_metrics.get(name)
            baseline = report.baseline_metrics.get(name)
            if measured is None:
                cells.append("—")
                continue
            if baseline is None or baseline == 0:
                cells.append(f"{measured:g}")
                continue
            delta = (measured - baseline) / abs(baseline)
            cells.append(f"{measured:g} ({delta:+.1%})")
        rows.append(cells)

    table = "\n".join("| " + " | ".join(r) + " |" for r in rows)
    legend = (
        "\n_Each row shows the eval result with that hunk removed. "
        "Percentages are change vs. the winner's metric — a metric that "
        "improves when the hunk is removed means the hunk hurts that "
        "metric._\n"
    )
    return f"### Ablation report\n\n{table}\n{legend}"
