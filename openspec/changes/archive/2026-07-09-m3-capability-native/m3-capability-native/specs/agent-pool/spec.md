## MODIFIED Requirements

### Requirement: AgentFactory.compile() produces agents with pydantic-ai Capabilities

`AgentFactory.compile()` SHALL produce agents composed with pydantic-ai native `AbstractCapability` instances instead of `ResourceProvider` instances. The Factory SHALL wire Capabilities, collect `ResourceSource` implementations, and construct `AggregatedResourceSource` at compile time.

#### Scenario: Factory compiles agent with native Capabilities

- **WHEN** `AgentFactory.compile(manifest, host_context)` processes an agent config with MCP servers and skills
- **THEN** the Factory SHALL create `MCPCapability` and `SkillCapability` instances
- **AND** the Factory SHALL collect ResourceSource implementations from capabilities that implement it
- **AND** the Factory SHALL construct an `AggregatedResourceSource` from collected sources
- **AND** the compiled agent SHALL receive the Capabilities list and AggregatedResourceSource

#### Scenario: Factory compiles agent with subagent tool

- **WHEN** `AgentFactory.compile(manifest, host_context)` processes an agent config with a `subagent` tool type
- **THEN** the Factory SHALL create a `SubagentCapability` with `SubagentToolset`
- **AND** the SubagentToolset SHALL NOT receive a direct AgentPool reference
- **AND** the SubagentToolset SHALL delegate to `DelegationService` at runtime (injected via AgentContext)

#### Scenario: Factory compiles agent with custom Capability from entry point

- **WHEN** `AgentFactory.compile(manifest, host_context)` processes an agent config referencing a custom capability type registered via entry point
- **THEN** the Factory SHALL load the entry point and instantiate the custom Capability
- **AND** the custom Capability SHALL be wired into the agent's capability list alongside built-in capabilities

### Requirement: AgentPool no longer manages ResourceProvider lifecycle

`AgentPool` SHALL NOT manage `ResourceProvider` lifecycle (creation, initialization, cleanup). ResourceProvider infrastructure SHALL be deleted. Capability lifecycle SHALL be managed by `AgentFactory` at compile time and by individual Capabilities themselves (via `on_change()`).

#### Scenario: AgentPool startup without ResourceProvider

- **WHEN** AgentPool starts and initializes its infrastructure
- **THEN** no `ResourceProvider` instances SHALL be created or managed
- **AND** MCP server connections SHALL be owned by `HostContext.mcp` and wrapped by `MCPCapability` at compile time
- **AND** skill discovery SHALL be owned by `HostContext.skills_registry` and wrapped by `SkillCapability` at compile time

#### Scenario: AgentPool cleanup without ResourceProvider teardown

- **WHEN** AgentPool shuts down and cleans up resources
- **THEN** no `ResourceProvider.cleanup()` calls SHALL be made
- **AND** Capability cleanup SHALL be handled by individual Capability `__aexit__` methods
- **AND** infrastructure cleanup (MCP servers, storage) SHALL be handled by HostContext lifecycle

### Requirement: AgentFactory subscribes to Capability on_change() for hot-swap

`AgentFactory` SHALL subscribe to `on_change()` streams from compiled Capabilities. When a change event arrives, the Factory SHALL perform a local hot-swap — replacing only the affected agent's capability, not the entire Host or registry.

#### Scenario: MCP tool list change triggers hot-swap

- **WHEN** an MCP server's tool list changes and `MCPCapability.on_change()` yields a `ChangeEvent`
- **THEN** `AgentFactory` SHALL receive the event
- **AND** the Factory SHALL replace only the affected agent's `MCPCapability` instance
- **AND** other agents in the registry SHALL NOT be affected
- **AND** the agent's new capability SHALL be active for subsequent Turns

#### Scenario: Capability without on_change does not trigger hot-swap

- **WHEN** a Capability's `on_change()` returns `None`
- **THEN** `AgentFactory` SHALL not subscribe to change events for that Capability
- **AND** no hot-swap SHALL be triggered for that Capability
