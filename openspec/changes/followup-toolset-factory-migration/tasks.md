## 1. Create ToolsetFactory implementations

- [ ] 1.1 Create `MCPToolsetFactory` — wraps MCP server, produces pdai `Toolset` with MCP tools (reconcile with `migrate-to-mcptoolset` openspec change)
- [ ] 1.2 Create `LocalSkillToolsetFactory` — discovers filesystem skills, produces `Toolset` (reconcile with `refactor-skills-as-capabilities` openspec change)
- [ ] 1.3 Create `PoolToolsetFactory` — exposes agent/team delegation as subagent tools

## 2. Migrate callers

- [ ] 2.1 Migrate `MCPResourceProvider` callers (25) to `MCPToolsetFactory`
- [ ] 2.2 Migrate `LocalResourceProvider` callers (44) to `LocalSkillToolsetFactory`
- [ ] 2.3 Migrate `PoolResourceProvider` callers (1) to `PoolToolsetFactory`
- [ ] 2.4 Migrate `PlanProvider` to pdai `Toolset` subclass (stateful, needs `RunContext.deps`)
- [ ] 2.5 Add `DeprecationWarning` to `CodeModeResourceProvider.__init__` and `RemoteCodeModeResourceProvider.__init__`

## 3. Remove old hierarchy

- [ ] 3.1 Remove `ResourceProvider` abstract base class (after all callers migrated)
- [ ] 3.2 Remove `AggregatingResourceProvider`, `FilteringResourceProvider`, `StaticResourceProvider`
- [ ] 3.3 Remove `SkillsInstructionProvider` (replaced by `SkillActivationCapability` from Phase 6)
- [ ] 3.4 Drop task 5.11 (`SkillBridgeCapability`) — superseded by `SkillActivationCapability`

## 4. Verify

- [ ] 4.1 Run `uv run pytest tests/resource_providers/` — tests updated and passing
- [ ] 4.2 Run `uv run pytest tests/tools/` — tool tests passing
- [ ] 4.3 Run `uv run pytest tests/toolsets/` — toolset tests passing
