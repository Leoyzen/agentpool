## Why

Phase 8 of the thin-wrapper refactor created the automated rename script `scripts/rename_to_agentwolf.py` (PR #101, merged). The script is ready but execution is deferred until all other phases (4-7) merge. This change tracks the actual execution of the rename.

The rename touches 1215+ files, 1599+ imports, 10 `src/` directories, `pyproject.toml`, YAML configs, Markdown docs, and CI workflows. It must be a single atomic commit.

## What Changes

- Execute `python scripts/rename_to_agentwolf.py` (no `--dry-run`)
- Run full verification: `uv sync`, `uv run pytest`, `uv run ruff check src/`, `uv run mypy src/`
- Verify `agentwolf --version` and `agentwolf serve-acp config.yml` work
- Commit as single atomic commit: `refactor: rename agentpool to agentwolf`

## Prerequisites

- All Phase 4-7 follow-up changes merged
- Clean working tree
- All tests passing before rename

## Impact

Massive mechanical rename. No behavioral changes. Pre-1.0, no external consumers expected. No alias period.

Part of #74. Final step — closes #74 when merged.
