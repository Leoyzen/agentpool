## MODIFIED Requirements

### Requirement: AgentPool as Registry and HostContext provider

AgentPool SHALL be a `BaseRegistry[NodeName, MessageNode]` that manages lifecycle of all agents and teams. `AgentPool.get_context()` SHALL pass `main_agent_name=self.main_agent_name` when constructing HostContext. AgentPool SHALL NOT be passed directly to protocol server constructors.

#### Scenario: AgentPool constructs HostContext with main_agent_name

- **WHEN** `pool.get_context()` is called
- **THEN** the returned HostContext SHALL have `main_agent_name` set to `pool.main_agent_name`

#### Scenario: AgentPool no longer passed to protocol server constructors

- **WHEN** `ACPProtocolHandler` is constructed
- **THEN** it SHALL receive a `HostContext` instead of an `AgentPool`
- **AND** `ACPProtocolHandler` SHALL access `session_pool` and `event_bus` via `host_context`
