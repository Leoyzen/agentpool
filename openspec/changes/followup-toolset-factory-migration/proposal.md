## Why

Phase 5 of the thin-wrapper refactor delivered the `ToolsetFactory` protocol and `AdapterToolsetFactory` (PR #98), but the actual factory implementations (`MCPToolsetFactory`, `LocalSkillToolsetFactory`, `PoolToolsetFactory`) and migration of 70+ callers from `ResourceProvider` to `ToolsetFactory` were not done. Additionally, the `SkillBridgeCapability` (task 5.11) is now superseded by the `SkillActivationCapability` implemented in Phase 6 (PR #100), so that task can be dropped.

This phase overlaps with two existing openspec changes:
- `migrate-to-mcptoolset` — covers MCPToolsetFactory equivalent (MCPServer → MCPToolset migration)
- `refactor-skills-as-capabilities` — covers LocalSkillToolsetFactory equivalent (Skill → SkillCapability)

Those changes should be reconciled with this phase to avoid duplicate work.

## What Changes

- Create `MCPToolsetFactory` — wraps MCP server, produces pdai `Toolset` with MCP tools (may be subsumed by `migrate-to-mcptoolset`)
- Create `LocalSkillToolsetFactory` — discovers filesystem skills, produces `Toolset` (may be subsumed by `refactor-skills-as-capabilities`)
- Create `PoolToolsetFactory` — exposes agent/team delegation as subagent tools
- Migrate 70 callers from `ResourceProvider` to `ToolsetFactory` implementations
- Migrate `PlanProvider` to pdai `Toolset` subclass
- Add `DeprecationWarning` to `CodeModeResourceProvider` and `RemoteCodeModeResourceProvider`
- Remove `ResourceProvider` abstract base class and hierarchy (after all callers migrated)
- Remove `SkillsInstructionProvider` (replaced by `SkillActivationCapability` from Phase 6)
- Drop task 5.11 (`SkillBridgeCapability`) — superseded by `SkillActivationCapability`

## Impact

Large migration touching 70+ files. Should be done incrementally: create factories, migrate callers in batches, then remove old hierarchy.

Part of #74. Depends on PR #93 merge.
