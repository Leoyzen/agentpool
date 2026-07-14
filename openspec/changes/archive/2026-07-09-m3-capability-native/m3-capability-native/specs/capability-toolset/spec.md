## ADDED Requirements

### Requirement: All agents use pydantic-ai native AbstractCapability and AbstractToolset

All agents SHALL be composed using pydantic-ai's native `AbstractCapability` and `AbstractToolset` for tools, hooks, instructions, and lifecycle management. No custom `ResourceProvider` abstraction SHALL remain after migration.

#### Scenario: Agent compiled with native capabilities

- **WHEN** `AgentFactory.compile()` produces a NativeAgent from a manifest
- **THEN** the agent SHALL receive a list of `AbstractCapability` instances
- **AND** each capability SHALL provide its toolset via `get_toolset() -> AbstractToolset`
- **AND** no `ResourceProvider` instances SHALL be attached to the agent

#### Scenario: ACP agent compiled with native capabilities

- **WHEN** `AgentFactory.compile()` produces an ACPAgent from a manifest
- **THEN** the agent SHALL receive capabilities appropriate for ACP (e.g., `MCPToolset` for MCP server access)
- **AND** no `ResourceProvider` instances SHALL be attached to the agent

### Requirement: Seven ToolsetFactory equivalents replace ResourceProvider implementations

Seven pydantic-ai native equivalents SHALL replace the existing ResourceProvider implementations, mapping one-to-one:

| ResourceProvider (deleted) | Replacement |
|---|---|
| `MCPResourceProvider` | `MCPToolset` + `MCPCapability` |
| `StaticResourceProvider` | `FunctionToolset` |
| `FilteringResourceProvider` | `FilteredToolset` |
| `AggregatingResourceProvider` | `CombinedToolset` |
| `PoolResourceProvider` | `SubagentCapability` + `SubagentToolset` |
| `CodeModeResourceProvider` | `CodeModeCapability` |
| `LocalResourceProvider` | `SkillCapability` (already exists) |

#### Scenario: MCPToolset replaces MCPResourceProvider

- **WHEN** an agent config references MCP servers
- **THEN** `AgentFactory` SHALL create an `MCPCapability` wrapping the MCP server connection
- **AND** `MCPCapability.get_toolset()` SHALL return an `MCPToolset` that auto-discovers MCP tools
- **AND** the MCP tools SHALL be available to the agent without any `as_capability()` adapter

#### Scenario: SubagentCapability replaces PoolResourceProvider

- **WHEN** an agent config includes a `subagent` tool type
- **THEN** `AgentFactory` SHALL create a `SubagentCapability` with a `SubagentToolset`
- **AND** `SubagentToolset` SHALL expose a `spawn_subagent` tool
- **AND** the tool SHALL accept agent name and prompt as parameters

#### Scenario: CodeModeCapability replaces CodeModeResourceProvider

- **WHEN** an agent config enables code mode
- **THEN** `AgentFactory` SHALL create a `CodeModeCapability`
- **AND** all agent tools SHALL be wrapped into a single Python execution meta-tool
- **AND** the meta-tool SHALL accept Python code as input

### Requirement: AdapterToolsetFactory bridges ResourceProvider during migration

An `AdapterToolsetFactory` SHALL wrap any existing `ResourceProvider` as a pydantic-ai `AbstractCapability`, allowing incremental migration. Migrated agents use native Capabilities directly; unmigrated agents transparently use the adapter.

#### Scenario: Unmigrated ResourceProvider works through adapter

- **WHEN** an agent still uses a `ResourceProvider` that has not yet been migrated to a native Capability
- **THEN** `AgentFactory` SHALL wrap the ResourceProvider in an `AdapterToolsetFactory`
- **AND** the adapter SHALL implement `AbstractCapability`
- **AND** the adapter's `get_toolset()` SHALL return a toolset containing the ResourceProvider's tools
- **AND** the agent SHALL function identically to pre-migration behavior

#### Scenario: Adapter is removed after full migration

- **WHEN** all 7 ResourceProvider implementations have been migrated to native Capabilities
- **THEN** `AdapterToolsetFactory` SHALL be deleted from the codebase
- **AND** no code SHALL reference `AdapterToolsetFactory`

### Requirement: AbstractCapability.on_change() replaces ResourceProvider change signals

`AbstractCapability` SHALL include an optional `on_change()` method returning `AsyncIterator[ChangeEvent] | None`. This replaces the `ResourceProvider` change signal system. Capabilities that do not need change notification SHALL return `None`.

#### Scenario: Capability notifies on tool list change

- **WHEN** an MCP server's tool list changes (tools added or removed)
- **THEN** `MCPCapability.on_change()` SHALL yield a `ChangeEvent` with `kind="tools_changed"`
- **AND** `AgentFactory` SHALL receive the event and perform a local hot-swap of the affected agent's capability

#### Scenario: Static capability returns None for on_change

- **WHEN** a `FunctionToolset` (static tools) is queried for change notifications
- **THEN** its `on_change()` SHALL return `None`
- **AND** `AgentFactory` SHALL not subscribe to change events for this capability

### Requirement: Entry-point registration for custom capabilities

Custom Capabilities SHALL be registered via the `agentpool.capabilities` entry-point group. `AgentFactory` SHALL discover entry-point capabilities at compile time and make them available for YAML `type:` references.

#### Scenario: Third-party package registers custom capability

- **WHEN** a third-party Python package declares an entry point in the `agentpool.capabilities` group
- **AND** an agent YAML config references the capability by `type:` name
- **THEN** `AgentFactory` SHALL load the entry point and instantiate the Capability
- **AND** the Capability SHALL be attached to the agent alongside built-in capabilities

#### Scenario: Unknown capability type raises error

- **WHEN** an agent YAML config references a `type:` that is neither built-in nor registered via entry point
- **THEN** `AgentFactory` SHALL raise a `CapabilityNotFoundError`
- **AND** the error message SHALL list all available capability types

### Requirement: ResourceProvider is physically deleted

After all 7 implementations are migrated and all consumers updated, the `ResourceProvider` abstract base class, all 7 implementations, the `as_capability()` bridge method, and all related infrastructure SHALL be physically removed from the codebase.

#### Scenario: No ResourceProvider imports remain

- **WHEN** the codebase is searched for `ResourceProvider` references after migration is complete
- **THEN** zero matches SHALL be found in `src/`
- **AND** zero matches SHALL be found in `tests/`
- **AND** the `resource_providers/` module directory SHALL not exist
