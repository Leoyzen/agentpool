## ADDED Requirements

### Requirement: All standalone agent.run_stream() callers use SessionPool
The system SHALL route all `agent.run_stream()` calls through `SessionPool.run_stream()` when the agent is part of an `AgentPool`. Direct standalone execution without SessionPool SHALL be eliminated for the following callers: Vercel serve, OpenAI API server, AG-UI server, streaming tools, FSSpec toolset, and ACP server session.

#### Scenario: Vercel serve uses SessionPool
- **WHEN** the Vercel serve CLI receives a user prompt
- **THEN** it calls `session_pool.run_stream(session_id, prompt)` instead of `agent.run_stream(prompt)`

#### Scenario: OpenAI API server uses SessionPool
- **WHEN** the OpenAI-compatible API server receives a chat completion request
- **THEN** it calls `session_pool.run_stream(session_id, content)` instead of `agent.run_stream(content)`

#### Scenario: AG-UI server uses SessionPool
- **WHEN** the AG-UI server receives a prompt for an agent
- **THEN** it calls `session_pool.run_stream(session_id, prompt)` instead of `agent.run_stream(prompt)`

#### Scenario: Streaming tools use SessionPool
- **WHEN** a streaming tool forks agent execution
- **THEN** it calls `session_pool.run_stream(session_id, prompt)` instead of `agent.run_stream(prompt)`

#### Scenario: FSSpec toolset uses SessionPool
- **WHEN** the FSSpec toolset executes an agent for file operations
- **THEN** it calls `session_pool.run_stream(session_id, prompt)` instead of `agent.run_stream(prompt)`

#### Scenario: ACP server session uses SessionPool
- **WHEN** the ACP server processes a session prompt
- **THEN** it calls `session_pool.run_stream(session_id, prompt)` instead of `agent.run_stream(prompt)`

### Requirement: BaseAgent.run_stream delegates to SessionPool
`BaseAgent.run_stream()` SHALL delegate to `SessionPool.run_stream()` when `self._session_pool` is available. When `SessionPool` is unavailable, it SHALL raise a `RuntimeError` indicating that SessionPool is required.

#### Scenario: Pooled agent delegates to SessionPool
- **WHEN** `agent.run_stream(prompt)` is called on an agent that is part of an `AgentPool`
- **THEN** execution is delegated to `session_pool.run_stream(session_id, prompt)`
- **AND** a `DeprecationWarning` is emitted

#### Scenario: Unpooled agent raises error
- **WHEN** `agent.run_stream(prompt)` is called on an agent without a `SessionPool`
- **THEN** a `RuntimeError` is raised indicating SessionPool is required
