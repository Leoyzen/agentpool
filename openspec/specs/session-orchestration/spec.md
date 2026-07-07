## ADDED Requirements

### Requirement: SessionController.close_session() SHALL clean up MCP resources

The `SessionController.close_session()` method SHALL call `agent.mcp.cleanup_session(session_id)` to clean up all session-scoped MCP resources before calling `agent.__aexit__()`. The RunHandle lifecycle (timeout + cancel) SHALL complete before MCP cleanup begins.

#### Scenario: Close session cleans MCP
- **WHEN** `SessionController.close_session(session_id)` is called
- **THEN** the RunHandle is awaited with timeout (10s) or cancelled
- **AND** `agent.mcp.cleanup_session(session_id)` is called before `agent.__aexit__()`
- **AND** the session's `SessionConnectionPool` is cleaned up
- **AND** the session's ACP MCP connections are unregistered

#### Scenario: Close session with active run
- **WHEN** `SessionController.close_session(session_id)` is called while a run is active
- **THEN** the RunHandle is cancelled with timeout
- **AND** MCP cleanup only begins after RunHandle completion/cancellation
- **AND** no MCP toolsets are closed mid-tool-call

### Requirement: get_or_create_session_agent SHALL register session context with MCPManager

The `SessionController.get_or_create_session_agent()` method SHALL call `agent.mcp.get_or_create_session(session_id)` to register the session context before setting `_mcp_snapshot` and `_session_connection_pool` on the agent.

#### Scenario: Agent creation registers session context
- **WHEN** `get_or_create_session_agent(session_id)` creates a new agent
- **THEN** `pool.mcp.get_or_create_session(session_id)` is called
- **AND** the session's `McpConfigSnapshot` is stored in the session context
- **AND** the session's `SessionConnectionPool` is stored in the session context
