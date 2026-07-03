## Why

Phase 7 of the thin-wrapper refactor added `import-linter` with 3 forbidden contracts (PR #99), but documented 80 pre-existing violations as `ignore_imports` entries with `allow_indirect_imports = true` to keep CI green. The violations need to be fixed incrementally:

- 8 violations: `agentpool_server` → `agentpool_cli`/`agentpool_commands`
- 72 violations: `agentpool_config` → `agentpool` (core)
- 0 violations: `acp` → `agentpool_server` (clean)

Additionally, `lint-imports` is not yet in the CI pipeline — it only runs locally.

## What Changes

- Fix 8 `agentpool_server` → `agentpool_cli`/`agentpool_commands` violations (move code or invert dependency)
- Fix 72 `agentpool_config` → `agentpool` violations (move types to config or invert dependency)
- Add `lint-imports` to `.github/workflows/` CI pipeline
- Remove `ignore_imports` entries as violations are fixed
- Remove `allow_indirect_imports = true` when all violations are resolved

## Impact

Gradual migration touching 80+ import statements. Each fix batch removes entries from `ignore_imports`. Final state: zero violations, `allow_indirect_imports` removed.

Part of #74. Depends on PR #93 merge.
