"""Scope enforcement — validates candidate diffs stay inside the manifest's scope.

The manifest declares two lists:

* ``target_files`` — patterns the candidate is allowed to modify
* ``do_not_touch`` — patterns that are *always* off-limits, even if they happen
  to match ``target_files`` (``do_not_touch`` wins on conflict)

Plus an optional ``max_diff_files`` cap.

Patterns use ``fnmatch`` / glob semantics. A directory pattern like
``src/auth/`` (trailing slash) or ``src/auth/*`` means "anything under auth".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import PurePosixPath

from agent_evolve.models import ScopeSpec


@dataclass
class ScopeReport:
    in_scope: bool
    violations: list[str] = field(default_factory=list)
    out_of_scope_files: list[str] = field(default_factory=list)
    protected_files: list[str] = field(default_factory=list)
    too_many_files: bool = False


def enforce_scope(changed_files: list[str], scope: ScopeSpec) -> ScopeReport:
    """Validate that *changed_files* fits within *scope*.

    *changed_files* is the list of paths a candidate's diff touches (as returned by
    ``git diff --name-only`` or the equivalent). Paths are normalized to POSIX-style
    forward slashes before matching so Windows checkouts behave identically to Linux.
    """
    report = ScopeReport(in_scope=True)
    targets = [_normalize_pattern(p) for p in scope.target_files]
    forbidden = [_normalize_pattern(p) for p in scope.do_not_touch]

    for raw in changed_files:
        path = _normalize_path(raw)
        if _matches_any(path, forbidden):
            report.protected_files.append(raw)
            report.violations.append(f"'{raw}' is in do_not_touch")
            continue
        if not _matches_any(path, targets):
            report.out_of_scope_files.append(raw)
            report.violations.append(f"'{raw}' is outside target_files")

    if scope.max_diff_files is not None and len(changed_files) > scope.max_diff_files:
        report.too_many_files = True
        report.violations.append(
            f"diff touches {len(changed_files)} files, limit is {scope.max_diff_files}"
        )

    report.in_scope = not report.violations
    return report


def _normalize_path(path: str) -> str:
    p = str(PurePosixPath(path.replace("\\", "/")))
    return p.lstrip("./")


def _normalize_pattern(pattern: str) -> str:
    p = pattern.replace("\\", "/")
    if p.endswith("/"):
        return p + "**"
    return p


def _matches_any(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if _glob_match(path, pattern):
            return True
    return False


def _glob_match(path: str, pattern: str) -> bool:
    """Glob match with ``**`` support for recursive directories.

    ``fnmatch`` alone does not handle ``**``. We expand ``**`` manually by
    matching the pattern against every prefix/subpath, which is sufficient for
    scope enforcement where patterns are simple.
    """
    if "**" not in pattern:
        return fnmatch(path, pattern) or fnmatch(path, pattern + "/**")

    prefix, _, suffix = pattern.partition("**")
    prefix = prefix.rstrip("/")
    suffix = suffix.lstrip("/")

    if prefix and not (path == prefix or path.startswith(prefix + "/")):
        return False

    remainder = path[len(prefix) :].lstrip("/") if prefix else path
    if not suffix:
        return True
    return fnmatch(remainder, suffix) or any(
        fnmatch(remainder[i:], suffix) for i in range(len(remainder))
    )
