## ADDED Requirements

### Requirement: AgentContext is a frozen dataclass carrying per-turn state

`AgentContext` SHALL be a `@dataclass(frozen=True)` that carries typed references to per-turn runtime state. It SHALL be immutable — fields cannot be modified after construction. It SHALL be the sole mechanism for passing turn-scoped context to agent tools and capabilities.

#### Scenario: AgentContext immutability

- **WHEN** code attempts to modify a field on an `AgentContext` instance after construction
- **THEN** a `FrozenInstanceError` SHALL be raised
- **AND** the original `AgentContext` instance remains unchanged

### Requirement: AgentContext is constructed by RunLoop at Turn time

`AgentContext` SHALL be constructed by RunLoop when a Turn begins execution, not by `AgentFactory` at compile time. The RunLoop SHALL assemble the context from current session state, run scope, and available services.

#### Scenario: RunLoop constructs AgentContext per turn

- **WHEN** RunLoop begins executing a Turn for a session
- **THEN** RunLoop SHALL construct an `AgentContext` containing current session state, run scope, and delegation service
- **AND** the `AgentContext` SHALL be injected into the pydantic-ai `RunContext` as `deps`
- **AND** a new `AgentContext` instance SHALL be created for each Turn (no reuse across turns)

### Requirement: AgentContext contains agent registry, delegation, session, scope, resources, and host

`AgentContext` SHALL contain the following fields:
- `agent_registry: AgentRegistry` — read-only access to compiled agents for delegation
- `delegation: DelegationService` — limited interface for spawning subagents
- `session: SessionState` — current session state (message history, metadata)
- `scope: RunScope` — run scope (config_id, tenant_id, user_id, session_id)
- `resources: ResourceSource | None` — aggregated resource access (None if agent has no resource sources)
- `host: HostContext` — infrastructure handles (mcp, storage, skills, etc.)

#### Scenario: AgentContext provides all fields

- **WHEN** an agent tool accesses `ctx.deps` (the AgentContext)
- **THEN** all six fields SHALL be accessible and typed
- **AND** `resources` SHALL be `None` for agents with no ResourceSource capabilities
- **AND** `resources` SHALL be an `AggregatedResourceSource` for agents with ResourceSource capabilities

#### Scenario: AgentContext fields are typed

- **WHEN** mypy --strict is run on the AgentContext definition
- **THEN** no type errors SHALL be reported for AgentContext field declarations
- **AND** each field type SHALL match the actual class (e.g., `delegation: DelegationService`, not `delegation: Any`)

### Requirement: AgentContext agent_registry is read-only

The `agent_registry` field SHALL provide read-only access to compiled agents. Tools SHALL be able to query available agent names and retrieve agent metadata, but SHALL NOT modify the registry or create new agents.

#### Scenario: Tool queries available agents

- **WHEN** an agent tool calls `ctx.deps.agent_registry.list_names()`
- **THEN** a list of agent name strings SHALL be returned
- **AND** the registry SHALL not be modifiable through the AgentContext

#### Scenario: Tool retrieves agent metadata

- **WHEN** an agent tool calls `ctx.deps.agent_registry.get("coder")`
- **THEN** the agent instance SHALL be returned if it exists
- **AND** a `KeyError` SHALL be raised if the agent name is not found
