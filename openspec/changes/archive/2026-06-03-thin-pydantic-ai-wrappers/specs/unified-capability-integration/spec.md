## ADDED Requirements

### Requirement: AgentPool providers expose capabilities via AbstractCapability
All AgentPool internal providers (tool providers, hook managers, MCP managers, history processors) SHALL expose their functionality as pydantic-ai `AbstractCapability` instances through a unified `as_capability()` interface.

#### Scenario: ResourceProvider exposes capability
- **WHEN** `ResourceProvider.as_capability()` is called
- **THEN** it returns an `AbstractCapability` (via `AbstractToolset` or custom capability) that contributes tools via `get_toolset()` and instructions via `get_instructions()`

#### Scenario: AgentHooks exposes capability
- **WHEN** `AgentHooks.as_capability()` is called
- **THEN** it returns a pydantic-ai `Hooks` capability with decorators registered for all configured interception points

#### Scenario: MCPManager exposes capability
- **WHEN** `MCPManager.as_capability()` is called
- **THEN** it returns a pydantic-ai `MCP` capability configured with the server's transport and lifecycle

#### Scenario: get_agentlet collects unified capabilities
- **WHEN** `NativeAgent.get_agentlet()` constructs the pydantic-ai agent
- **THEN** it collects capabilities from all providers via `as_capability()` and passes them as `capabilities=[...]` to `PydanticAgent`

### Requirement: AgentPool supports direct pydantic-ai capability passthrough
AgentPool SHALL expose a `capabilities` configuration field that allows users to directly pass pydantic-ai `AbstractCapability` instances, which are merged with internally-generated capabilities and passed to `PydanticAgent`.

#### Scenario: Direct capability from Python API
- **WHEN** a user instantiates `NativeAgent` with `capabilities=[MyCustomCapability()]`
- **THEN** the custom capability is included in the `capabilities=[...]` list passed to `PydanticAgent`

#### Scenario: Direct capability from YAML config
- **WHEN** an agent YAML config includes `capabilities:` with capability type and arguments
- **THEN** AgentPool resolves the capability class, instantiates it, and includes it in the agent construction

#### Scenario: Mixed internal and external capabilities
- **WHEN** an agent has both internal providers (tools, hooks) and user-provided capabilities
- **THEN** `get_agentlet()` collects all capabilities and passes them as a unified list

### Requirement: EventBus adapter publishes capability lifecycle events
AgentPool SHALL implement an `EventBusHooksAdapter` that wraps pydantic-ai `Hooks` capabilities and publishes lifecycle events to AgentPool's `EventBus` so protocol servers and cross-session consumers continue to receive events.

#### Scenario: Tool execution events via adapter
- **WHEN** pydantic-ai `Hooks.before_tool_execute()` is invoked
- **THEN** `EventBusHooksAdapter` publishes `ToolCallStartEvent` to `EventBus` before delegating to the wrapped hooks

#### Scenario: Protocol servers receive capability events
- **WHEN** an ACP/AG-UI/OpenCode protocol server subscribes to a session's EventBus
- **THEN** it receives all lifecycle events originating from pydantic-ai capabilities via the adapter

### Requirement: Tool confirmation bridged to pydantic-ai ApprovalRequiredToolset
AgentPool SHALL bridge its `InputProvider` confirmation flow with pydantic-ai's `ApprovalRequiredToolset`, preserving per-tool/per-run/never confirmation modes.

#### Scenario: Tool with requires_confirmation mapped to ApprovalRequiredToolset
- **WHEN** a tool has `requires_confirmation=True`
- **THEN** its toolset is wrapped with `ApprovalRequiredToolset` (a toolset wrapper, not a capability), which delegates confirmation requests to AgentPool's `InputProvider`

#### Scenario: Confirmation denial handled gracefully
- **WHEN** a user denies tool execution via `InputProvider`
- **THEN** the denial is translated to pydantic-ai's expected response format and the tool is not executed

### Requirement: pydantic-ai version pinned with upper bound
AgentPool SHALL pin pydantic-ai to a version range with upper bound and test against main branch in CI.

#### Scenario: Version constraint in pyproject.toml
- **WHEN** `pyproject.toml` is read
- **THEN** pydantic-ai dependency is constrained to `>=1.102.0,<2.0.0`

## MODIFIED Requirements

### Requirement: Native agent supports tool management
**Existing spec**: `native-agent` capability requires `get_agentlet()` to construct a pydantic-ai agent with tools.

#### Scenario: ToolManager deprecation
- **WHEN** `ToolManager` is used directly
- **THEN** it delegates to `ResourceProvider.as_capability()` and emits `DeprecationWarning`

### Requirement: Native agent supports lifecycle hooks
**Existing spec**: `native-agent` capability requires pre/post tool and run hooks.

#### Scenario: NativeAgentHookManager deprecation
- **WHEN** `NativeAgentHookManager` is instantiated
- **THEN** it delegates hook execution to pydantic-ai `Hooks` capability and emits `DeprecationWarning`

### Requirement: Native agent supports MCP server integration
**Existing spec**: `native-agent` capability requires MCP server tool discovery and lifecycle management.

#### Scenario: MCPManager deprecation
- **WHEN** `MCPManager` is instantiated
- **THEN** it delegates to pydantic-ai `MCP` capability and emits `DeprecationWarning`

### Requirement: Native agent supports conversation history management
**Existing spec**: `native-agent` capability requires message history with compaction and processor support.

#### Scenario: Manual history processor resolution deprecation
- **WHEN** `Agent._resolve_history_processors()` is called
- **THEN** it delegates to `ProcessHistory` capability construction and emits `DeprecationWarning`

### Requirement: Native agent supports system prompt configuration
**Existing spec**: `native-agent` capability requires configurable system prompts with template resolution.

#### Scenario: SystemPrompts deprecation
- **WHEN** `SystemPrompts` is instantiated
- **THEN** it delegates to pydantic-ai `instructions` parameter and emits `DeprecationWarning`
