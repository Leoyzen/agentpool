## Why

The thin-wrapper refactor (`openspec/changes/thin-wrapper-refactor/`) delivered Phases 1-3 (100% complete) and partial Phases 4-8 via 7 sub-PRs (#95-#101, all merged). This change tracks the **remaining 59 tasks** across 5 incomplete phases:

| Phase | Done | Total | Remaining |
|-------|------|-------|-----------|
| 4. Team Cleanup | 9 | 18 | 9 (Team/TeamRun removal + caller migration) |
| 5. ToolProvider | 2 | 17 | 15 (3 factories + 70-caller migration + ResourceProvider removal) |
| 6. Capabilities | 13 | 17 | 4 (YAML config wiring + hook audit) |
| 7. Server Boundaries | 3 | 11 | 8 (80 import-linter violations + CI integration) |
| 8. Rename | 1 | 24 | 23 (script ready, execution deferred) |

## What Changes

### Phase 4: Team/TeamRun Removal
- Remove `Team` class, `TeamRun` class, `TeamConfig.get_team()` factory
- Migrate 50 callers to `GraphConfig` + `GraphBuilder`
- `teams:` YAML syntax continues via auto-translation (PR #97)

### Phase 5: ToolsetFactory Migration
- Create `MCPToolsetFactory`, `LocalSkillToolsetFactory`, `PoolToolsetFactory`
- Migrate 70 callers from `ResourceProvider` to `ToolsetFactory`
- Remove `ResourceProvider` hierarchy, `SkillsInstructionProvider`
- Drop `SkillBridgeCapability` (superseded by `SkillActivationCapability` from Phase 6)
- Reconcile with `migrate-to-mcptoolset` and `refactor-skills-as-capabilities` openspec changes

### Phase 6: Capability Wiring
- Add `capabilities:` YAML config section
- Wire Capabilities into `Agent` class from config
- Audit existing hooks for Capability overlap
- Reconcile with `unify-tool-interception-to-pydantic-ai-capabilities` openspec change

### Phase 7: Server Boundary Fixes
- Fix 80 import-linter violations (8 server→cli, 72 config→core)
- Add `lint-imports` to CI pipeline
- Remove `ignore_imports` entries and `allow_indirect_imports`

### Phase 8: Rename Execution
- Execute `scripts/rename_to_agentwolf.py` (PR #101, ready)
- Full verification: pytest, mypy, ruff, CLI
- Single atomic commit

## Impact

Large multi-phase migration. Each phase can be a separate PR stacked on `refactor/thin-wrapper` (or `develop/agentic` after PR #93 merges). Phase 8 (rename) must be last.

Part of #74. Closes #74 when Phase 8 executes.
