## ADDED Requirements

### Requirement: DelegationService exposes a limited interface for subagent spawning

`DelegationService` SHALL expose only two methods: `spawn_subagent(name, prompt)` and `get_available_agents()`. Tools SHALL know WHAT they can do (spawn a subagent by name), not HOW RunLoop implements the spawning (queue, priority, background task).

#### Scenario: Tool spawns a subagent by name

- **WHEN** an agent tool calls `ctx.deps.delegation.spawn_subagent("coder", "Write a function")`
- **THEN** the DelegationService SHALL initiate spawning of the named subagent with the given prompt
- **AND** the tool SHALL receive a result or stream from the subagent's execution
- **AND** the tool SHALL NOT have access to the spawning mechanism internals (queue, priority, task management)

#### Scenario: Tool lists available agents

- **WHEN** an agent tool calls `ctx.deps.delegation.get_available_agents()`
- **THEN** a list of agent name strings SHALL be returned
- **AND** only agents authorized for the current scope SHALL be included
- **AND** the list SHALL NOT include agents from other tenants or configs

### Requirement: DelegationService is implemented by RunLoop

`DelegationService` SHALL be implemented by RunLoop, not by AgentFactory or AgentPool. RunLoop controls the spawning mechanism, queueing, and lifecycle of subagent runs.

#### Scenario: RunLoop provides DelegationService to AgentContext

- **WHEN** RunLoop constructs an `AgentContext` for a Turn
- **THEN** the `delegation` field SHALL be a DelegationService instance backed by the RunLoop's session controller
- **AND** `spawn_subagent()` SHALL route through the RunLoop's `SessionController.receive_request()` method
- **AND** the RunLoop SHALL manage the spawned subagent's lifecycle (cancellation, timeout, completion)

#### Scenario: DelegationService does not expose RunLoop internals

- **WHEN** an agent tool accesses the DelegationService instance
- **THEN** only `spawn_subagent()` and `get_available_agents()` SHALL be accessible
- **AND** RunLoop internal methods (queue management, event bus, session state) SHALL NOT be accessible through the DelegationService interface

### Requirement: DelegationService enforces scope isolation

`DelegationService` SHALL only allow spawning of agents within the current RunScope. Agents from other tenants or configs SHALL NOT be accessible.

#### Scenario: Agent within scope is spawnable

- **WHEN** `spawn_subagent("coder", prompt)` is called and "coder" is registered in the current config
- **THEN** the subagent SHALL be spawned successfully

#### Scenario: Agent outside scope is rejected

- **WHEN** `spawn_subagent("other_tenant_agent", prompt)` is called and the agent is not in the current scope
- **THEN** an `AgentNotFoundError` SHALL be raised
- **AND** the error SHALL NOT reveal the existence of agents outside the current scope
