## ADDED Requirements

### Requirement: ToolsetFactory implementations created
Three `ToolsetFactory` implementations SHALL be created: `MCPToolsetFactory` (wraps MCP server, produces pdai `Toolset`), `LocalSkillToolsetFactory` (discovers filesystem skills, produces `Toolset`), and `PoolToolsetFactory` (exposes agent/team delegation as subagent tools). Each SHALL implement the `ToolsetFactory` protocol defined in Phase 5.

### Requirement: ResourceProvider hierarchy deprecated and removed
All callers of `ResourceProvider` (70+ across `MCPResourceProvider`, `LocalResourceProvider`, `PoolResourceProvider`, `StaticResourceProvider`, `AggregatingResourceProvider`, `FilteringResourceProvider`) SHALL be migrated to `ToolsetFactory` implementations. `DeprecationWarning` SHALL be added to `CodeModeResourceProvider` and `RemoteCodeModeResourceProvider` `__init__` methods. After all callers are migrated, the `ResourceProvider` abstract base class and all subclasses SHALL be removed.

### Requirement: PlanProvider migrated to Toolset subclass
`PlanProvider` SHALL be migrated to a pdai `Toolset` subclass. As it is stateful and requires `RunContext.deps`, the migration SHALL preserve state semantics.

### Requirement: SkillsInstructionProvider removed
`SkillsInstructionProvider` SHALL be removed. Its role (injecting skill XML into agent prompts) is now handled by `SkillActivationCapability` (implemented in Phase 6, PR #100).

### Requirement: SkillBridgeCapability task dropped
Task 5.11 (`SkillBridgeCapability`) is dropped — superseded by `SkillActivationCapability` from Phase 6.
