## MODIFIED Requirements

### Requirement: AgentFactory compiles agents using self._pool, not host_context.pool

AgentFactory SHALL use its own `self._pool` field for agent creation, not `host_context.pool`. AgentFactory SHALL NOT read `host_context.pool` for any purpose. The `pool` parameter in `cfg.get_agent(pool=...)` remains until M4 config split.

#### Scenario: AgentFactory uses self._pool for agent creation

- **WHEN** `AgentFactory.create_session_agent()` is called
- **THEN** it SHALL use `self._pool` for pool-dependent operations
- **AND** it SHALL NOT access `host_context.pool`

#### Scenario: AgentFactory does not depend on host_context.pool

- **WHEN** `host_context.pool` is removed from HostContext
- **THEN** AgentFactory SHALL continue to function correctly
- **AND** no `AttributeError` SHALL be raised

### Requirement: Talk wiring uses _bind_pool instead of ctx.pool

Talk connections SHALL use `MessageNode._bind_pool(pool)` for internal wiring instead of setting `agent_pool` property via `ctx.pool`. This avoids DeprecationWarning and removes the dependency on `HostContext.pool`.

#### Scenario: Talk wires connected nodes via _bind_pool

- **WHEN** Talk processes a connection between source and destination nodes
- **THEN** it SHALL call `destination._bind_pool(source._agent_pool)` 
- **AND** it SHALL NOT access `ctx.pool` or set `node.agent_pool` property
