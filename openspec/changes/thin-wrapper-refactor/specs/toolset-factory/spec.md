## ADDED Requirements

### Requirement: ToolsetFactory protocol replaces ResourceProvider hierarchy
The system SHALL define a `ToolsetFactory` protocol that produces pdai `Toolset` instances. The `ResourceProvider` abstract base class and its hierarchy (`MCPResourceProvider`, `LocalResourceProvider`, `PoolResourceProvider`, `StaticResourceProvider`, `AggregatingResourceProvider`, `FilteringResourceProvider`) SHALL be deprecated and replaced by `ToolsetFactory` implementations.

#### Scenario: ToolsetFactory produces Toolset
- **WHEN** a `ToolsetFactory` instance is called
- **THEN** it SHALL return a pdai `Toolset` (or `Toolset` subclass) instance

#### Scenario: ResourceProvider not used by agents
- **WHEN** agent code acquires tools after migration
- **THEN** it SHALL use `ToolsetFactory` instances, not `ResourceProvider` instances

### Requirement: MCP tools provided via MCPToolsetFactory
An `MCPToolsetFactory` SHALL replace `MCPResourceProvider`. It SHALL produce a pdai `Toolset` that wraps an MCP server connection, exposing MCP tools, prompts, resources, and skills as pdai tools.

#### Scenario: MCPToolsetFactory produces tools from MCP server
- **WHEN** an `MCPToolsetFactory` is created with an MCP server config
- **THEN** the resulting `Toolset` SHALL expose all MCP server tools as callable pdai tools

### Requirement: Local skills provided via LocalSkillToolsetFactory
A `LocalSkillToolsetFactory` SHALL replace `LocalResourceProvider`. It SHALL discover skills from filesystem directories and produce a `Toolset` that exposes skill commands as pdai tools.

#### Scenario: LocalSkillToolsetFactory discovers skills
- **WHEN** a `LocalSkillToolsetFactory` is created with skill paths
- **THEN** the resulting `Toolset` SHALL expose all discovered skills as callable tools

### Requirement: Pool delegation provided via PoolToolsetFactory
A `PoolToolsetFactory` SHALL replace `PoolResourceProvider`. It SHALL produce a `Toolset` that exposes agent and team delegation as subagent tools.

#### Scenario: PoolToolsetFactory exposes delegation tools
- **WHEN** a `PoolToolsetFactory` is created with an `AgentPool` reference
- **THEN** the resulting `Toolset` SHALL expose agent delegation tools for each registered agent

### Requirement: CodeModeResourceProvider deprecated
`CodeModeResourceProvider` and `RemoteCodeModeResourceProvider` SHALL be deprecated without a direct `ToolsetFactory` replacement. The single-meta-tool pattern (wrapping all tools into one Python execution tool) cannot be expressed by `ToolsetFactory`.

#### Scenario: CodeModeResourceProvider emits deprecation warning
- **WHEN** `CodeModeResourceProvider` is instantiated
- **THEN** a `DeprecationWarning` SHALL be emitted with guidance to use pdai Toolset directly

### Requirement: PlanProvider migrated to stateful Toolset
`PlanProvider` SHALL be migrated to a pdai `Toolset` subclass (not `ToolsetFactory`) because it requires `RunContext.deps` for stateful plan management. It SHALL expose `get_plan_entry` and `set_plan_entry` as pdai tools with `RunContext` access.

#### Scenario: PlanProvider is a Toolset subclass
- **WHEN** `PlanProvider` class definition is inspected
- **THEN** it SHALL inherit from `pydantic_ai.Toolset` (or a subclass), not `ResourceProvider`

### Requirement: SkillBridgeCapability as interim skill injection
A `SkillBridgeCapability` SHALL be created as a pdai `Capability` that injects skill instructions into agent prompts. This is an interim solution that preserves the current `SkillsInstructionProvider` behavior using the Capability mechanism. It SHALL be superseded by `SkillActivationCapability` in Phase 6.

#### Scenario: SkillBridgeCapability injects skill metadata
- **WHEN** an agent with `SkillBridgeCapability` runs
- **THEN** skill metadata SHALL be injected into the system prompt as an XML block
