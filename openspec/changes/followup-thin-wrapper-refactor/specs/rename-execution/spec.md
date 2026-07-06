## ADDED Requirements

### Requirement: Rename executed as single atomic commit
The rename script `scripts/rename_to_agentwolf.py` SHALL be executed (not dry-run) after all Phase 4-7 follow-up changes are merged. The result SHALL be committed as a single atomic commit with message `refactor: rename agentpool to agentwolf`.

#### Scenario: rename script executed
- **WHEN** `python scripts/rename_to_agentwolf.py` is run (without `--dry-run`)
- **THEN** all 10 `src/` directories SHALL be renamed from `agentpool*` to `agentwolf*` and all file contents SHALL be updated

#### Scenario: single atomic commit
- **WHEN** `git log --oneline` is inspected after rename
- **THEN** exactly one commit with message `refactor: rename agentpool to agentwolf` SHALL contain all rename changes

### Requirement: Full verification after rename
After rename, the following SHALL pass: `uv sync` (dependencies resolve), `uv run pytest` (all tests pass), `uv run ruff check src/` (lint clean), `uv run mypy src/` (type check clean), `agentwolf --version` (CLI works), `agentwolf serve-acp config.yml` (ACP server starts).

#### Scenario: dependencies resolve
- **WHEN** `uv sync` is run after rename
- **THEN** it SHALL complete successfully with all dependencies resolving under the new package names

#### Scenario: tests pass
- **WHEN** `uv run pytest` is run after rename
- **THEN** all tests SHALL pass with zero failures

#### Scenario: CLI works
- **WHEN** `agentwolf --version` is run
- **THEN** it SHALL print the version string without error

### Requirement: No agentpool references remain
After rename, `grep -r "agentpool" src/ tests/ site/ *.toml *.yml *.md` SHALL return only references in `openspec/changes/` (historical artifacts preserved by the script's `EXCLUDE_DIRS`).

#### Scenario: no agentpool references in source
- **WHEN** `grep -r "agentpool" src/ tests/` is run
- **THEN** it SHALL return zero matches

#### Scenario: openspec historical artifacts preserved
- **WHEN** `grep -r "agentpool" openspec/changes/` is run
- **THEN** references SHALL still exist because the rename script excludes `openspec/changes/` from modification
