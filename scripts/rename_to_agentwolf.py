#!/usr/bin/env python3
"""Rename agentpool → agentwolf across the entire codebase.

Phase 8 of the thin-wrapper refactor. This script performs a one-shot
mechanical rename of all agentpool references to agentwolf.

Usage:
    python scripts/rename_to_agentwolf.py [--dry-run]

Pre-requisites:
    - All Phase 1-7 PRs merged into refactor/thin-wrapper
    - Clean working tree (no uncommitted changes)
    - Run from repository root

What this script does:
    1. Rename src/ directories (agentpool → agentwolf, agentpool_* → agentwolf_*)
    2. Replace all references in .py/.toml/.yml/.md/.json/etc files
    3. Verify entry point files exist post-rename
    4. Print verification checklist

What this script does NOT do:
    - Rename the GitHub repository (do this separately after merge)
    - Update PyPI package name (publishing step)
    - Rename git remotes

After running:
    1. Run `uv sync` to verify dependencies resolve
    2. Run `uv run pytest` to verify all tests pass
    3. Run `uv run ruff check src/` to verify linting
    4. Run `uv run mypy src/` to verify type checking
    5. Verify `agentwolf --version` works
    6. Commit as a single atomic commit
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent

SRC_DIRS_TO_RENAME = [
    ("src/agentpool", "src/agentwolf"),
    ("src/agentpool_bot", "src/agentwolf_bot"),
    ("src/agentpool_cli", "src/agentwolf_cli"),
    ("src/agentpool_commands", "src/agentwolf_commands"),
    ("src/agentpool_config", "src/agentwolf_config"),
    ("src/agentpool_prompts", "src/agentwolf_prompts"),
    ("src/agentpool_server", "src/agentwolf_server"),
    ("src/agentpool_storage", "src/agentwolf_storage"),
    ("src/agentpool_sync", "src/agentwolf_sync"),
    ("src/agentpool_toolsets", "src/agentwolf_toolsets"),
]

REPLACEMENTS = [
    ("agentpool_config", "agentwolf_config"),
    ("agentpool_server", "agentwolf_server"),
    ("agentpool_toolsets", "agentwolf_toolsets"),
    ("agentpool_storage", "agentwolf_storage"),
    ("agentpool_cli", "agentwolf_cli"),
    ("agentpool_commands", "agentwolf_commands"),
    ("agentpool_prompts", "agentwolf_prompts"),
    ("agentpool_sync", "agentwolf_sync"),
    ("agentpool_bot", "agentwolf_bot"),
    ("agentpool", "agentwolf"),
]

FILE_PATTERNS = [
    "*.py",
    "*.toml",
    "*.yml",
    "*.yaml",
    "*.md",
    "*.cfg",
    "*.txt",
    "*.rst",
    "*.json",
]

# Pre-compiled as Path objects for robust is_relative_to() checks.
# Using string "in filepath.parts" would fail for multi-segment paths
# like "openspec/changes" because parts splits them into ("openspec", "changes").
EXCLUDE_DIRS: list[Path] = [
    Path(d)
    for d in [
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "node_modules",
        ".codegraph",
        "openspec/changes",  # Don't rename historical spec docs
        ".omo",  # Don't rename evidence files
    ]
]


def rename_directories(dry_run: bool) -> None:
    """Rename src/ directories from agentpool* to agentwolf*."""
    print("Step 1: Renaming source directories")
    for old, new in SRC_DIRS_TO_RENAME:
        old_path = REPO_ROOT / old
        new_path = REPO_ROOT / new
        if old_path.exists():
            if new_path.exists():
                print(f"  ERROR: {new} already exists — refusing to move {old} inside it")
                sys.exit(1)
            print(f"  {old} → {new}")
            if not dry_run:
                shutil.move(str(old_path), str(new_path))
        else:
            print(f"  SKIP (not found): {old}")


def replace_references(dry_run: bool) -> None:
    """Replace all agentpool references in source files."""
    print("\nStep 2: Replacing references in files")
    files_changed = 0
    for pattern in FILE_PATTERNS:
        for filepath in REPO_ROOT.rglob(pattern):
            if any(filepath.is_relative_to(REPO_ROOT / d) for d in EXCLUDE_DIRS):
                continue
            try:
                content = filepath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue
            new_content = content
            for old, new in REPLACEMENTS:
                new_content = new_content.replace(old, new)
            if new_content != content:
                files_changed += 1
                if dry_run:
                    print(f"  Would update: {filepath.relative_to(REPO_ROOT)}")
                else:
                    filepath.write_text(new_content, encoding="utf-8")
    print(f"  Total files {'would be ' if dry_run else ''}changed: {files_changed}")


def check_entry_points() -> None:
    """Check that renamed entry point files exist."""
    print("\nStep 3: Checking entry points")
    entry_files = [
        REPO_ROOT / "src" / "agentwolf" / "__init__.py",
        REPO_ROOT / "src" / "agentwolf_cli" / "__init__.py",
        REPO_ROOT / "src" / "agentwolf_cli" / "cli.py",
    ]
    for ef in entry_files:
        if ef.exists():
            print(f"  OK: {ef.relative_to(REPO_ROOT)}")
        else:
            print(f"  WARNING: {ef.relative_to(REPO_ROOT)} not found after rename!")


def print_verification_checklist() -> None:
    """Print post-rename verification steps."""
    print("\nStep 4: Verification checklist")
    print("  [ ] Run: uv sync")
    print("  [ ] Run: uv run pytest")
    print("  [ ] Run: uv run ruff check src/")
    print("  [ ] Run: uv run mypy src/")
    print("  [ ] Run: agentwolf --version")
    print("  [ ] Run: agentwolf serve-acp config.yml")
    print("  [ ] Commit: git add -A && git commit -m 'refactor: rename agentpool to agentwolf'")
    print("  [ ] Push: git push million refactor/thin-wrapper --force")


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("=== DRY RUN ===\n")

    rename_directories(dry_run)
    replace_references(dry_run)
    check_entry_points()
    print_verification_checklist()

    if dry_run:
        print("\n=== DRY RUN COMPLETE — no changes made ===")
    else:
        print("\n=== RENAME COMPLETE — run verification checklist ===")


if __name__ == "__main__":
    main()
