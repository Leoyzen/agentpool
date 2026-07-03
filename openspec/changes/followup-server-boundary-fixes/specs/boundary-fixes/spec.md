## ADDED Requirements

### Requirement: Zero core-to-app import violations
All 80 pre-existing import-linter violations SHALL be fixed. The `ignore_imports` entries SHALL be removed. The `allow_indirect_imports` setting SHALL be set to `false` (or removed). `lint-imports` SHALL pass with zero violations.

### Requirement: lint-imports in CI pipeline
`lint-imports` SHALL be added to `.github/workflows/` CI pipeline as a required check. It SHALL run on every PR and block merge on violations.
