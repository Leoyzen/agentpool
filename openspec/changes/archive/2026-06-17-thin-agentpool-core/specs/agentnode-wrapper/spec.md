## MODIFIED Requirements

### Requirement: AgentNode wraps AgentPool agents as BaseNode
AgentPool SHALL provide `AgentNode` — a `pydantic_graph.BaseNode` implementation that wraps an AgentPool agent for graph execution without modifying the agent's lifecycle or `MessageNode`.

#### Scenario: AgentNode execution with native agent
- **WHEN** `AgentNode.run()` is invoked wrapping a `native` agent
- **THEN** it creates a child session and runs the native agent within that session via pydantic-graph execution

#### Scenario: AgentNode execution with acp agent
- **WHEN** `AgentNode.run()` is invoked wrapping an `acp` agent
- **THEN** it creates a child session and runs the ACP agent within that session via the ACP protocol

#### Scenario: AgentNode rejects unsupported agent types at construction
- **WHEN** code attempts to create an `AgentNode` wrapping a `claude`, `agui`, or `codex` agent
- **THEN** a `ValueError` or `TypeError` is raised at construction time with a clear message

## REMOVED Requirements

### Requirement: AgentNode supports all AgentPool agent types
**Reason**: With the framework limited to native and acp agents, AgentNode no longer needs to handle claude, agui, or codex agent-specific behaviors.
**Migration**: Ensure all agents used in graph execution are `native` or `acp` type. No code migration needed if already using these types.
