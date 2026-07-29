"""Sibling-mode artifact seeding.

Implements the Python half of ``artifact_mode: sibling`` (see Phase 0c
of ``.claude/skills/evolve/SKILL.md``). Given a ``ProblemSpec`` whose
``artifact_mode`` is ``"sibling"``, :func:`seed_sibling` materialises a
renamed sibling file from the canonical ``scope.target_files[0]`` and
returns a :class:`SeedResult` the supervisor uses to retarget the
search.

Why sibling mode exists
-----------------------
``replace`` mode mutates the canonical file in place. That is the right
shape when the artifact *is* production code — a hot loop being
optimised, a function whose only callers are tests. It breaks down for
**library catalogue** artifacts (strategy classes, optimizer variants,
ML model registry entries, parser dialects) where downstream callers
import the canonical name and should keep their existing behaviour.

Sibling mode addresses that case by:

1. Sealing the canonical file under ``do_not_touch`` (so candidates
   that try to mutate it are auto-rejected by the scope enforcer).
2. Seeding a renamed copy alongside it before round 1.
3. Pointing the search at the seeded copy.
4. Validating the rename was behaviour-preserving via an extra eval
   pass against the original baseline.

The winner PR's diff is "added 1 new file" rather than "modified 1
existing file" — exactly the shape a library catalogue wants.

Implementation notes
--------------------
The rename uses a token-aware Python-source rewrite (see
:func:`_rename_identifier_in_source`) that touches *NAME tokens only*.
String literals, docstrings, and comments containing the original name
are preserved verbatim — important for files where the symbol name
appears in error messages or documentation.

Sibling mode is Python-only in this version. Non-``.py`` target files
raise :class:`SiblingSeedError` rather than risk a regex-based rewrite
that would corrupt JS/TS/etc.
"""

from __future__ import annotations

import ast
import io
import re
import token
import tokenize
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

from agent_evolve.models import ProblemSpec, SiblingSpec


class SiblingSeedError(RuntimeError):
    """Raised when the sibling seed step cannot proceed.

    Failure modes that produce this error:

    * ``ProblemSpec.artifact_mode`` is not ``"sibling"`` (caller misuse).
    * The target file is not a Python source file.
    * The target file has zero top-level classes/functions or has
      multiple and ``sibling.symbol_name`` was not supplied to
      disambiguate.
    * The seeded file's destination already exists (we refuse to
      overwrite — this would silently lose the previous run's diff).
    """


@dataclass
class SeedResult:
    """Outcome of :func:`seed_sibling`.

    The supervisor uses these fields to (a) re-derive ``scope.target_files``
    to point at the seeded file, (b) re-derive ``scope.do_not_touch`` to
    seal the canonical file, and (c) build the seed-validation eval
    command via ``spec.eval_command_for(new_symbol)``.

    ``original_path`` and ``new_path`` are **repo-root-relative POSIX**
    strings — the dialect scope patterns and ``git diff --name-only``
    speak. Absolute paths here would never fnmatch a diff path, so the
    scope enforcer would prune every sibling-mode candidate as
    out-of-scope (and the canonical file's seal would silently not
    seal). Resolve against the working tree in use when a real
    filesystem path is needed: ``worktree_root / result.new_path``.
    """

    original_path: str
    new_path: str
    original_symbol: str
    new_symbol: str


# Tokens recognised by :func:`expand_template`. Kept in lockstep with the
# allow-list in ``config.py`` (which validates that custom rename
# patterns include at least one of these). Adding a new token requires
# updating both places and the SKILL doc.
_TEMPLATE_TOKENS = frozenset(
    ("{original}", "{ProblemId}", "{Date}", "{original_stem}",
     "{problem_id}", "{date}")
)


def expand_template(
    pattern: str,
    *,
    original_symbol: str,
    original_stem: str,
    problem_id: str,
    today: _date,
) -> str:
    """Substitute the canonical sibling-mode template tokens into *pattern*.

    Tokens recognised:

    +----------------------+----------------------------+-------------------+
    | Token                | Meaning                    | Example           |
    +======================+============================+===================+
    | ``{original}``       | Original symbol name       | ``MLRegimeStrat`` |
    +----------------------+----------------------------+-------------------+
    | ``{ProblemId}``      | Problem ID, PascalCase     | ``Multiasset``    |
    +----------------------+----------------------------+-------------------+
    | ``{Date}``           | ``YYYYMMDD``               | ``20260430``      |
    +----------------------+----------------------------+-------------------+
    | ``{original_stem}``  | Original file's stem       | ``ml_regime``     |
    +----------------------+----------------------------+-------------------+
    | ``{problem_id}``     | Problem ID, as-is          | ``multiasset``    |
    +----------------------+----------------------------+-------------------+
    | ``{date}``           | ``YYYY_MM_DD``             | ``2026_04_30``    |
    +----------------------+----------------------------+-------------------+

    Unknown tokens (``{foo}``) pass through unchanged — callers can layer
    their own template steps on top without this function eating their
    placeholders.
    """
    pascal_id = _to_pascal_case(problem_id)
    return (
        pattern
        .replace("{original}", original_symbol)
        .replace("{ProblemId}", pascal_id)
        .replace("{Date}", today.strftime("%Y%m%d"))
        .replace("{original_stem}", original_stem)
        .replace("{problem_id}", problem_id)
        .replace("{date}", today.strftime("%Y_%m_%d"))
    )


def seed_sibling(
    spec: ProblemSpec,
    *,
    problem_id: str,
    today: _date | None = None,
    repo_root: Path | str | None = None,
) -> SeedResult:
    """Materialise the sibling artifact described by *spec*.

    The supervisor calls this between Phase 0b (baseline measurement)
    and round 1 of Phase A, with *repo_root* pointing at a dedicated
    seed worktree. After it returns, the supervisor must:

    1. Commit the seeded file in that tree and re-anchor the run to the
       seed commit — candidate worktrees are materialised from the run
       anchor, so an uncommitted seed is invisible to every candidate.
    2. Re-derive the spec with :func:`dataclasses.replace` — both
       ``ProblemSpec`` and ``ScopeSpec`` are frozen, so attribute
       assignment raises ``FrozenInstanceError``::

           spec = replace(spec, scope=replace(
               spec.scope,
               target_files=[result.new_path],
               do_not_touch=[*spec.scope.do_not_touch, result.original_path],
           ))

    3. Run the eval against ``spec.eval_command_for(result.new_symbol)``
       and validate the metrics match the original baseline within
       ``spec.expected_baseline_tolerance``. A behaviour-preserving
       rename produces a matching baseline; a broken rename (e.g. a
       self-reference to the class name that wasn't updated, or an eval
       command that doesn't honour the ``{symbol}`` token) is caught
       here, before the search burns its budget.

    *today* defaults to today's date; it is exposed for deterministic
    tests. *repo_root* defaults to the current working directory; the
    target file path is interpreted relative to it.
    """
    if spec.artifact_mode != "sibling":
        raise SiblingSeedError(
            f"seed_sibling called on a spec with artifact_mode="
            f"{spec.artifact_mode!r} — expected 'sibling'"
        )

    sibling_spec = spec.sibling or SiblingSpec()
    target = spec.scope.target_files[0]
    today = today or _date.today()
    repo_root_path = Path(repo_root) if repo_root is not None else Path.cwd()
    original_path = (repo_root_path / target).resolve()

    if not original_path.exists():
        raise SiblingSeedError(
            f"canonical target file does not exist: {original_path}"
        )
    if original_path.suffix != ".py":
        raise SiblingSeedError(
            f"sibling mode is Python-only; got {original_path.suffix!r} file "
            f"({original_path}). Implement support for other languages by "
            f"extending agent_evolve.artifact._rename_identifier_in_source."
        )

    source = original_path.read_text(encoding="utf-8")

    if sibling_spec.symbol_name:
        original_symbol = sibling_spec.symbol_name
    else:
        original_symbol = _find_unique_top_level_symbol(source, original_path)

    new_symbol = expand_template(
        sibling_spec.symbol_rename_pattern,
        original_symbol=original_symbol,
        original_stem=original_path.stem,
        problem_id=problem_id,
        today=today,
    )
    new_stem = expand_template(
        sibling_spec.file_rename_pattern,
        original_symbol=original_symbol,
        original_stem=original_path.stem,
        problem_id=problem_id,
        today=today,
    )

    if not new_symbol.isidentifier():
        raise SiblingSeedError(
            f"expanded symbol_rename_pattern produced an invalid Python "
            f"identifier: {new_symbol!r}. Check that the pattern's "
            f"non-token characters are identifier-safe."
        )

    if sibling_spec.output_dir:
        output_dir = (repo_root_path / sibling_spec.output_dir).resolve()
    else:
        output_dir = original_path.parent

    new_path = output_dir / f"{new_stem}{original_path.suffix}"

    if new_path.exists():
        raise SiblingSeedError(
            f"sibling target already exists: {new_path} — refusing to "
            f"overwrite. Either delete the existing file, change "
            f"sibling.file_rename_pattern, or change the run's problem_id "
            f"or date so the expanded path is unique."
        )

    new_source = _rename_identifier_in_source(
        source, original=original_symbol, new=new_symbol,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    new_path.write_text(new_source, encoding="utf-8")

    return SeedResult(
        original_path=_repo_rel(original_path, repo_root_path),
        new_path=_repo_rel(new_path, repo_root_path),
        original_symbol=original_symbol,
        new_symbol=new_symbol,
    )


def _repo_rel(path: Path, root: Path) -> str:
    """*path* as a repo-root-relative POSIX string.

    Scope patterns and ``git diff --name-only`` both speak this dialect;
    see the :class:`SeedResult` docstring for why absolute paths are
    unusable there. Raises ``ValueError`` if *path* escapes *root* —
    loud is correct, since a sibling seeded outside the repo can never
    be committed or scoped.
    """
    return path.resolve().relative_to(root.resolve()).as_posix()


def _find_unique_top_level_symbol(source: str, file_path: Path) -> str:
    """Return the single top-level class/def name in *source*.

    Used when ``sibling.symbol_name`` is unset. Raises
    :class:`SiblingSeedError` when the file has zero or multiple
    candidates — the user must disambiguate via ``symbol_name``.
    """
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        raise SiblingSeedError(
            f"{file_path} did not parse as Python: {e}"
        ) from e

    candidates = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SiblingSeedError(
            f"{file_path}: no top-level class or function found — set "
            f"sibling.symbol_name explicitly to name the symbol you want "
            f"renamed."
        )
    raise SiblingSeedError(
        f"{file_path}: multiple top-level symbols ({candidates}) — set "
        f"sibling.symbol_name to one of them so the seed step knows "
        f"which one to rename."
    )


def _rename_identifier_in_source(
    source: str,
    *,
    original: str,
    new: str,
) -> str:
    """Rename every NAME-token occurrence of *original* to *new* in *source*.

    Uses :mod:`tokenize` to find NAME tokens, which means the rewrite
    only touches:

    * Class/function definitions (``class Original`` / ``def original``)
    * References (``Original()``, ``Original.attr``, ``foo(Original)``)
    * Decorators (``@original``)
    * Type-annotation references (``x: Original``)

    NOT renamed:

    * The string ``"Original"`` inside a string literal — docstrings,
      error messages, format strings, regex literals, etc.
    * The word ``Original`` inside a comment.
    * ``Original`` as part of a longer identifier (``OriginalThing`` is
      a different NAME token and is not modified).

    The implementation uses byte-level surgery on the original source
    to preserve every comment, blank line, and trailing-whitespace
    nuance that ``tokenize.untokenize`` may otherwise normalise.
    """
    line_starts = _build_line_starts(source)

    replacements: list[tuple[int, int]] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == token.NAME and tok.string == original:
            start = line_starts[tok.start[0] - 1] + tok.start[1]
            end = line_starts[tok.end[0] - 1] + tok.end[1]
            replacements.append((start, end))

    if not replacements:
        return source

    # Apply in reverse so each prior offset stays valid as we splice.
    out = source
    for start, end in reversed(replacements):
        out = out[:start] + new + out[end:]
    return out


def _build_line_starts(source: str) -> list[int]:
    """Map 1-indexed line numbers to absolute character offsets.

    ``tokenize`` reports positions as ``(line, column)`` pairs; this
    helper converts them to absolute offsets so byte-level surgery on
    the source is straightforward. A trailing entry beyond the last
    newline keeps the lookup safe for tokens on the final line.
    """
    starts = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            starts.append(i + 1)
    return starts


_PASCAL_SPLIT = re.compile(r"[-_\s]+")


def _to_pascal_case(s: str) -> str:
    """Convert ``problem_id`` to PascalCase for the ``{ProblemId}`` token.

    ``"multiasset"`` -> ``"Multiasset"``;
    ``"multi-asset"`` -> ``"MultiAsset"``;
    ``"ml_regime_strategy"`` -> ``"MlRegimeStrategy"``;
    ``"MultiAsset"`` -> ``"MultiAsset"`` (already PascalCase, untouched).

    The conversion is intentionally simple — the *first* character of
    each split is uppercased; everything else is left as-is. This
    preserves user intent for already-cased input and handles the
    common snake/kebab/space-separated cases without surprises.
    """
    parts = _PASCAL_SPLIT.split(s)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)
