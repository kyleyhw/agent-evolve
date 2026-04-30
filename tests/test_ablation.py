"""Tests for :mod:`agent_evolve.ablation`."""

from __future__ import annotations

from agent_evolve.ablation import (
    AblationReport,
    HunkAblation,
    HunkSpec,
    parse_hunks,
    render_ablation_markdown,
    run_ablation_report,
)


_SAMPLE_DIFF = """\
diff --git a/src/foo.py b/src/foo.py
index aaaa..bbbb 100644
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,4 @@
 def f(x):
-    return x + 1
+    return x * 2
+    # vectorised
@@ -10,2 +11,2 @@
 def g(x):
-    return [i for i in x]
+    return list(x)
diff --git a/src/bar.py b/src/bar.py
index cccc..dddd 100644
--- a/src/bar.py
+++ b/src/bar.py
@@ -5,1 +5,2 @@
 baseline = compute()
+baseline_cache = baseline
"""


def test_parse_hunks_finds_three_hunks_across_two_files():
    """Two-file diff with two hunks in foo.py and one in bar.py."""
    hunks = parse_hunks(_SAMPLE_DIFF)
    assert len(hunks) == 3
    assert hunks[0].path == "src/foo.py"
    assert hunks[0].old_start == 1
    assert hunks[0].new_start == 1
    assert hunks[1].path == "src/foo.py"
    assert hunks[1].old_start == 10
    assert hunks[2].path == "src/bar.py"
    assert hunks[2].old_start == 5


def test_parse_hunks_counts_added_and_removed_lines():
    """Per-hunk line counts come from the body, not the @@ header."""
    hunks = parse_hunks(_SAMPLE_DIFF)
    # foo.py:1 has -1 / +2 (one removed return, two added lines).
    assert hunks[0].lines_removed == 1
    assert hunks[0].lines_added == 2
    # bar.py:5 has -0 / +1.
    assert hunks[2].lines_removed == 0
    assert hunks[2].lines_added == 1


def test_parse_hunks_handles_empty_diff():
    """An empty input yields an empty list (callers treat this as no-ablation)."""
    assert parse_hunks("") == []


def test_parse_hunks_handles_diff_with_no_hunks():
    """Mode-only / binary diffs (no @@ markers) yield an empty list."""
    binary_only = (
        "diff --git a/img.png b/img.png\n"
        "Binary files a/img.png and b/img.png differ\n"
    )
    assert parse_hunks(binary_only) == []


def test_run_ablation_report_skips_when_no_apply_callback():
    """No callback ⇒ report is marked skipped with a useful reason."""
    report = run_ablation_report(
        winner_id="42",
        winner_metrics={"duration_ms": 50.0},
        diff_text=_SAMPLE_DIFF,
        eval_command="echo",
        eval_cwd=None,
    )
    assert report.skipped is True
    assert "apply_ablation" in (report.skip_reason or "")
    assert report.rows == []


def test_run_ablation_report_skips_when_diff_has_no_hunks():
    report = run_ablation_report(
        winner_id="42",
        winner_metrics={"duration_ms": 50.0},
        diff_text="",
        eval_command="echo",
        eval_cwd=None,
        apply_ablation=lambda hunk: True,
    )
    assert report.skipped is True
    assert "no hunks" in (report.skip_reason or "")


def test_run_ablation_report_records_apply_failure_per_row(monkeypatch):
    """A hunk whose ``apply_ablation`` returns False is recorded, others measured."""
    seen: list[str] = []

    def fake_apply(hunk: HunkSpec) -> bool:
        seen.append(hunk.path)
        # Refuse to ablate the bar.py hunk; allow the foo.py ones.
        return hunk.path == "src/foo.py"

    # Stub run_eval so the test does not actually shell out.
    import agent_evolve.ablation as ablation_module

    class FakeEval:
        passed = True
        returncode = 0
        parse_error = None

        def __init__(self, metrics: dict[str, float]) -> None:
            self.metrics = metrics

    monkeypatch.setattr(
        ablation_module,
        "run_eval",
        lambda *a, **kw: FakeEval({"duration_ms": 40.0}),
    )

    report = run_ablation_report(
        winner_id="42",
        winner_metrics={"duration_ms": 50.0},
        diff_text=_SAMPLE_DIFF,
        eval_command="echo",
        eval_cwd=None,
        apply_ablation=fake_apply,
    )

    assert report.skipped is False
    assert len(report.rows) == 3
    statuses = [r.status for r in report.rows]
    # foo.py hunks succeed; bar.py refused.
    assert statuses[:2] == ["measured", "measured"]
    assert statuses[2] == "could_not_ablate"
    assert seen == ["src/foo.py", "src/foo.py", "src/bar.py"]


def test_render_ablation_markdown_skipped_report_returns_short_note():
    report = AblationReport(
        winner_id="42",
        baseline_metrics={"x": 1.0},
        skipped=True,
        skip_reason="no apply callback",
    )
    md = render_ablation_markdown(report)
    assert "skipped" in md.lower()
    assert "no apply callback" in md


def test_render_ablation_markdown_table_has_one_row_per_hunk():
    report = AblationReport(
        winner_id="42",
        baseline_metrics={"duration_ms": 50.0},
        rows=[
            HunkAblation(
                hunk_id="src/foo.py:1",
                file="src/foo.py",
                header="@@ -1,3 +1,4 @@",
                lines_added=2,
                lines_removed=1,
                status="measured",
                measured_metrics={"duration_ms": 60.0},
            ),
            HunkAblation(
                hunk_id="src/bar.py:5",
                file="src/bar.py",
                header="@@ -5,1 +5,2 @@",
                lines_added=1,
                lines_removed=0,
                status="could_not_ablate",
                error="reverse apply failed",
            ),
        ],
    )
    md = render_ablation_markdown(report)
    # The header row, separator, and two data rows.
    assert md.count("|\n") >= 3
    assert "src/foo.py:1" in md
    assert "src/bar.py:5" in md
    # The could_not_ablate row should fill metric cells with em-dash.
    assert "could_not_ablate" in md
    # The measured row should report a +20% increase in duration_ms.
    assert "+20.0%" in md or "+20%" in md
