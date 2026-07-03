## ADDED Requirements

### Requirement: Rename executed as single atomic commit
The rename script `scripts/rename_to_agentwolf.py` SHALL be executed (not dry-run) after all Phase 4-7 follow-up changes are merged. The result SHALL be committed as a single atomic commit with message `refactor: rename agentpool to agentwolf`.

### Requirement: Full verification after rename
After rename, the following SHALL pass: `uv sync` (dependencies resolve), `uv run pytest` (all tests pass), `uv run ruff check src/` (lint clean), `uv run mypy src/` (type check clean), `agentwolf --version` (CLI works), `agentwolf serve-acp config.yml` (ACP server starts).

### Requirement: No agentpool references remain
After rename, `grep -r "agentpool" src/ tests/ site/ *.toml *.yml *.md` SHALL return only references in `openspec/changes/` (historical artifacts preserved by the script's `EXCLUDE_DIRS`).
