#!/usr/bin/env python3
"""One-shot installer for agent-evolve.

Run from the repo root:

    uv run python install.py

What it does:

1. Installs the ``agent-evolve`` Python package globally as a uv tool, so
   the ``agent-evolve`` CLI (manifest validator, HTML report renderer) is
   available from any shell.
2. Symlinks every skill under ``.claude/skills/`` into
   ``~/.claude/skills/`` so ``/evolve``, ``/explorer``, and ``/reviewer``
   are available in every Claude Code session, not just this repo.

Re-run with ``--force`` to overwrite existing skill links. Skip individual
steps with ``--skip-python`` or ``--skip-skills``.

Windows: symlink creation requires Developer Mode or an elevated prompt.
If symlinks fail the script falls back to a full copy.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
SKILLS_SRC = REPO_ROOT / ".claude" / "skills"
USER_SKILLS = Path.home() / ".claude" / "skills"


def install_python_package() -> int:
    """Run ``uv tool install --from . agent-evolve``. Returns the exit code."""
    print("[install] installing agent-evolve Python package (uv tool install)")
    if shutil.which("uv") is None:
        print(
            "[install]   uv is not on PATH — install it from "
            "https://docs.astral.sh/uv/ and re-run.",
            file=sys.stderr,
        )
        return 127
    result = subprocess.run(
        ["uv", "tool", "install", "--force", "--from", str(REPO_ROOT), "agent-evolve"],
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        print(
            f"[install]   uv tool install exited {result.returncode}. "
            "You may be able to continue; the skills do not require the CLI.",
            file=sys.stderr,
        )
    return result.returncode


def install_skills(*, force: bool) -> int:
    """Symlink every skill directory into the user-scope skills folder.

    Re-installation contract: an existing destination is auto-refreshed
    when its ``SKILL.md`` frontmatter ``name:`` matches the source's —
    i.e. a prior copy of the *same* skill from a previous install.
    A foreign skill occupying the same slot (different ``name:``) still
    requires ``--force`` to overwrite, preserving the original collision
    guard for users with unrelated skills.
    """
    USER_SKILLS.mkdir(parents=True, exist_ok=True)

    skills = sorted(p for p in SKILLS_SRC.iterdir() if p.is_dir())
    if not skills:
        print(f"[install] no skills found under {SKILLS_SRC}", file=sys.stderr)
        return 1

    print(f"[install] installing skills into {USER_SKILLS}")
    errors = 0
    for src in skills:
        dst = USER_SKILLS / src.name
        if _already_points_here(dst, src):
            print(f"[install]   {src.name}: already linked — skipping")
            continue
        if dst.exists() or dst.is_symlink():
            same_skill = _is_same_skill(src, dst)
            if not force and not same_skill:
                print(
                    f"[install]   {src.name}: {dst} exists and is a different skill "
                    f"({_skill_name_from(dst) or '?'}); re-run with --force to overwrite"
                )
                errors += 1
                continue
            if same_skill and not force:
                print(f"[install]   {src.name}: refreshing existing copy in place")
            _remove(dst)

        if _make_symlink(src, dst):
            print(f"[install]   {src.name}: linked -> {dst}")
        else:
            try:
                shutil.copytree(src, dst)
                print(
                    f"[install]   {src.name}: symlink failed; copied -> {dst} "
                    "(re-run install.py to refresh the copy after editing SKILL.md)"
                )
            except OSError as e:
                print(f"[install]   {src.name}: copy failed — {e}", file=sys.stderr)
                errors += 1

    return 0 if errors == 0 else 2


def _skill_name_from(skill_dir: Path) -> str | None:
    """Read the YAML frontmatter ``name:`` from ``<skill_dir>/SKILL.md``.

    Returns ``None`` if the file is missing, lacks frontmatter, or does
    not declare a ``name`` field. The frontmatter is delimited by lines
    of three dashes; we scan until the closing fence rather than
    full-parsing YAML to avoid pulling in a dependency for one field.
    """
    md = skill_dir / "SKILL.md"
    if not md.exists():
        return None
    try:
        text = md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        if ":" in line:
            key, _, value = line.partition(":")
            if key.strip().lower() == "name":
                return value.strip()
    return None


def _is_same_skill(src: Path, dst: Path) -> bool:
    """True if ``src`` and ``dst`` declare the same SKILL ``name:``.

    A symlinked destination is treated as not-same (the symlink case is
    handled separately by ``_already_points_here``); we are only
    interested in the case where the destination is a stale *copy* from
    a previous install.
    """
    if dst.is_symlink():
        return False
    src_name = _skill_name_from(src)
    dst_name = _skill_name_from(dst)
    return src_name is not None and src_name == dst_name


def _already_points_here(dst: Path, src: Path) -> bool:
    if not dst.is_symlink():
        return False
    try:
        return dst.resolve() == src.resolve()
    except OSError:
        return False


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _make_symlink(src: Path, dst: Path) -> bool:
    """Try to create a directory symlink. Returns False on permission error."""
    try:
        os.symlink(src, dst, target_is_directory=True)
        return True
    except OSError as e:
        if os.name == "nt":
            return False
        # non-Windows: re-raise unexpected errors
        raise e


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite existing entries under ~/.claude/skills/",
    )
    parser.add_argument(
        "--skip-python", action="store_true",
        help="do not run `uv tool install`",
    )
    parser.add_argument(
        "--skip-skills", action="store_true",
        help="do not symlink the skills",
    )
    args = parser.parse_args()

    exit_code = 0
    if not args.skip_python:
        rc = install_python_package()
        if rc != 0:
            exit_code = rc

    if not args.skip_skills:
        rc = install_skills(force=args.force)
        if rc != 0 and exit_code == 0:
            exit_code = rc

    print()
    if exit_code == 0:
        print("[install] done.")
    else:
        print(f"[install] finished with warnings (exit {exit_code}).")
    print()
    print("Verify:")
    print("  agent-evolve --help")
    print("  # in Claude Code, open any repo and type: /evolve <Tab>")
    print("  # You should see /evolve, /explorer, /reviewer in the completion list.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
