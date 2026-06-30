## MODIFIED Requirements

### Requirement: AgentNode wraps AgentPool agents as BaseNode
AgentPool SHALL provide `AgentNode` — a `pydantic_graph.BaseNode` implementation that wraps an AgentPool agent for graph execution without modifying the agent's lifecycle or `MessageNode`.

#### Scenario: AgentNode execution creates child session
- **WHEN** `AgentNode.run()` is invoked during graph execution
- **THEN** it generates a session ID, creates a child session via `SessionPool.create_session(session_id, agent_name, parent_session_id)`, and runs the wrapped agent within that session

#### Scenario: AgentNode preserves agent lifecycle
- **WHEN** an agent is wrapped in `AgentNode`
- **THEN** the agent's signals, connections, MCP servers, and event handlers remain functional and independent of graph execution

#### Scenario: AgentNode handles streaming events using PydanticAI native event types
- **WHEN** an agent wrapped in `AgentNode` emits streaming events during `_run_stream_once()`
- **THEN** events are iterated and collected using PydanticAI's native `AgentStreamEvent` types (not AgentPool subclasses)
- **AND** the final `StreamCompleteEvent` provides the result message
- **AND** if no `StreamCompleteEvent` is emitted, a `RuntimeError` is raised
- **AND** `session_id` is accessed via `AgentContext`, not from event payload fields

#### Scenario: AgentNode passes session state via context (agent is stateless)
- **WHEN** `AgentNode.run()` begins execution
- **THEN** it passes `session_id` via `AgentRunContext` and `_run_stream_once(session_id=...)` parameters; the agent instance itself is NOT mutated (no `agent.session_id` assignment)

#### Scenario: AgentNode returns End[ChatMessage]
- **WHEN** `AgentNode.run()` completes successfully
- **THEN** it returns `End[ChatMessage]` (as required by pydantic_graph `BaseNode.run()`), wrapping the agent's output message

#### Scenario: AgentNode avoids method name collision
- **WHEN** `AgentNode` executes the wrapped agent
- **THEN** it calls the agent's internal execution method (`_run_stream_once()`), NOT the public `agent.run()` which delegates to SessionPool and would create double session creation

#### Scenario: AgentNode accesses graph deps via ctx.deps
- **WHEN** `AgentNode.run()` needs graph-level state (session_id, event_bus, prompt)
- **THEN** it accesses them via `ctx.deps` (type `GraphDeps`), NOT via `ctx.state` (type `ChatMessage`)

#### Scenario: AgentNode uses ctx.state for sequential chains
- **WHEN** `AgentNode` is part of a sequential chain and `ctx.state` is available
- **THEN** it passes `ctx.state` (the previous node's output) as the agent input, NOT `ctx.deps.prompt`

#### Scenario: AgentNode uses ctx.deps.prompt for initial input
- **WHEN** `AgentNode` is the first node in a graph and `ctx.state` is None
- **THEN** it falls back to `ctx.deps.prompt` as the agent input
