## ADDED Requirements

### Requirement: Zero core-to-app import violations
All 80 pre-existing import-linter violations SHALL be fixed. The `ignore_imports` entries SHALL be removed. The `allow_indirect_imports` setting SHALL be set to `false` (or removed). `lint-imports` SHALL pass with zero violations.

#### Scenario: lint-imports passes with zero violations
- **WHEN** `uv run lint-imports` is executed
- **THEN** it SHALL exit with code 0 and report zero violations

#### Scenario: no ignore_imports entries remain
- **WHEN** the `[tool.importlinter]` section in `pyproject.toml` is inspected
- **THEN** no `ignore_imports` entries SHALL be present in any contract

#### Scenario: allow_indirect_imports removed
- **WHEN** the `[tool.importlinter]` section in `pyproject.toml` is inspected
- **THEN** `allow_indirect_imports` SHALL NOT be set to `true` in any contract

### Requirement: lint-imports in CI pipeline
`lint-imports` SHALL be added to `.github/workflows/` CI pipeline as a required check. It SHALL run on every PR and block merge on violations.

#### Scenario: lint-imports runs in CI
- **WHEN** a PR is opened against `develop/agentic` or `refactor/thin-wrapper`
- **THEN** the CI pipeline SHALL execute `uv run lint-imports` as a required check

#### Scenario: CI fails on import violation
- **WHEN** a PR introduces a new `agentpool_server` → `agentpool_cli` direct import
- **THEN** the CI `lint-imports` check SHALL fail and block the merge
