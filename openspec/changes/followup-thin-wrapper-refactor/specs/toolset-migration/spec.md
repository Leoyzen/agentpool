## ADDED Requirements

### Requirement: ToolsetFactory implementations created
Three `ToolsetFactory` implementations SHALL be created: `MCPToolsetFactory` (wraps MCP server, produces pdai `Toolset`), `LocalSkillToolsetFactory` (discovers filesystem skills, produces `Toolset`), and `PoolToolsetFactory` (exposes agent/team delegation as subagent tools). Each SHALL implement the `ToolsetFactory` protocol defined in Phase 5.

#### Scenario: MCPToolsetFactory produces Toolset from MCP server config
- **WHEN** an `MCPToolsetFactory` is constructed with a valid `MCPServerConfig`
- **THEN** `create_toolset()` SHALL return a pdai `Toolset` containing the MCP server's tools

#### Scenario: LocalSkillToolsetFactory discovers filesystem skills
- **WHEN** a `LocalSkillToolsetFactory` is constructed with a skill directory path
- **THEN** `create_toolset()` SHALL return a pdai `Toolset` containing tools from discovered SKILL.md files

#### Scenario: PoolToolsetFactory exposes delegation tools
- **WHEN** a `PoolToolsetFactory` is constructed with an `AgentPool` instance
- **THEN** `create_toolset()` SHALL return a pdai `Toolset` containing a `subagent` tool for each registered agent

### Requirement: ResourceProvider hierarchy deprecated and removed
All callers of `ResourceProvider` (70+ across `MCPResourceProvider`, `LocalResourceProvider`, `PoolResourceProvider`, `StaticResourceProvider`, `AggregatingResourceProvider`, `FilteringResourceProvider`) SHALL be migrated to `ToolsetFactory` implementations. `DeprecationWarning` SHALL be added to `CodeModeResourceProvider` and `RemoteCodeModeResourceProvider` `__init__` methods. After all callers are migrated, the `ResourceProvider` abstract base class and all subclasses SHALL be removed.

#### Scenario: DeprecationWarning emitted by CodeModeResourceProvider
- **WHEN** `CodeModeResourceProvider()` is instantiated
- **THEN** a `DeprecationWarning` SHALL be emitted with a message directing users to `ToolsetFactory`

#### Scenario: ResourceProvider class removed after migration
- **WHEN** all 70+ callers are migrated to `ToolsetFactory` implementations
- **THEN** `from agentpool.resource_providers.base import ResourceProvider` SHALL raise `ImportError`

### Requirement: PlanProvider migrated to Toolset subclass
`PlanProvider` SHALL be migrated to a pdai `Toolset` subclass. As it is stateful and requires `RunContext.deps`, the migration SHALL preserve state semantics.

#### Scenario: PlanProvider is a Toolset subclass
- **WHEN** `PlanProvider` class definition is inspected
- **THEN** it SHALL inherit from `pydantic_ai.Toolset` (or equivalent pdai base)

#### Scenario: PlanProvider preserves state via RunContext.deps
- **WHEN** a `PlanProvider` instance is used in a run
- **THEN** plan entries SHALL be accessible via `RunContext.deps` without behavioral change from the `ResourceProvider` version

### Requirement: SkillsInstructionProvider removed
`SkillsInstructionProvider` SHALL be removed. Its role (injecting skill XML into agent prompts) is now handled by `SkillActivationCapability` (implemented in Phase 6, PR #100).

#### Scenario: SkillsInstructionProvider not importable
- **WHEN** code attempts `from agentpool.resource_providers.skills_instruction import SkillsInstructionProvider`
- **THEN** the import SHALL raise `ImportError`

#### Scenario: skill injection via SkillActivationCapability
- **WHEN** an agent has `SkillActivationCapability` attached
- **THEN** skill instructions SHALL be injected into `SystemPromptPart.content` via the `before_model_request` hook

### Requirement: SkillBridgeCapability task dropped
Task 5.11 (`SkillBridgeCapability`) SHALL be dropped from the thin-wrapper refactor plan. It is superseded by `SkillActivationCapability` from Phase 6 (PR #100), which provides the same skill injection functionality via pdai Capability hooks.

#### Scenario: SkillBridgeCapability not implemented
- **WHEN** `src/agentpool/capabilities/` is inspected
- **THEN** no `skill_bridge.py` file SHALL exist
