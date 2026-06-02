## ADDED Requirements

### Requirement: AgentNode wraps AgentPool agents as BaseNode
AgentPool SHALL provide `AgentNode` — a `pydantic_graph.BaseNode` implementation that wraps an AgentPool agent for graph execution without modifying the agent's lifecycle or `MessageNode`.

#### Scenario: AgentNode execution creates child session
- **WHEN** `AgentNode.run()` is invoked during graph execution
- **THEN** it generates a session ID, creates a child session via `SessionPool.create_session(session_id, agent_name, parent_session_id)`, and runs the wrapped agent within that session

#### Scenario: AgentNode preserves agent lifecycle
- **WHEN** an agent is wrapped in `AgentNode`
- **THEN** the agent's signals, connections, MCP servers, and event handlers remain functional and independent of graph execution

#### Scenario: AgentNode handles streaming events
- **WHEN** an agent wrapped in `AgentNode` emits streaming events
- **THEN** events are collected and forwarded through the graph execution context

#### Scenario: AgentNode returns End[ChatMessage]
- **WHEN** `AgentNode.run()` completes
- **THEN** it returns `End[ChatMessage]` (as required by pydantic_graph `BaseNode.run()`), wrapping the agent's output message

#### Scenario: AgentNode avoids method name collision
- **WHEN** `AgentNode` executes the wrapped agent
- **THEN** it calls the agent's internal execution method (`_run_stream_once()` or equivalent), NOT the public `agent.run()` which delegates to SessionPool and would create double session creation

### Requirement: MessageNode does NOT extend BaseNode
`MessageNode` SHALL remain an independent abstraction and SHALL NOT extend `pydantic_graph.BaseNode`.

#### Scenario: MessageNode independent of graph execution
- **WHEN** `MessageNode` is used outside of graph execution
- **THEN** it functions normally without any graph-related dependencies

#### Scenario: AgentNode wraps MessageNode
- **WHEN** `AgentNode` is created wrapping a `MessageNode`
- **THEN** the `MessageNode` remains independent; only the `AgentNode` has graph semantics
