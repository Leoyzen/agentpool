## ADDED Requirements

### Requirement: MCPManager SHALL track per-session MCP resources

The `MCPManager` SHALL maintain a `_session_contexts: dict[str, _SessionContext]` mapping session IDs to per-session MCP state. Each `_SessionContext` SHALL contain: a `SessionConnectionPool`, a per-session `toolset_cache: dict[str, MCPToolset]`, a `McpConfigSnapshot`, and a list of ACP connection IDs owned by that session.

#### Scenario: Session context creation
- **WHEN** `get_or_create_session(session_id)` is called for a session that does not yet have a context
- **THEN** a new `_SessionContext` is created with an empty `SessionConnectionPool`, empty `toolset_cache`, `None` snapshot, and empty `acp_connection_ids` list
- **AND** it is stored in `_session_contexts[session_id]`

#### Scenario: Session context retrieval
- **WHEN** `get_or_create_session(session_id)` is called for a session that already has a context
- **THEN** the existing `_SessionContext` is returned without creating a new one

#### Scenario: Session context cleanup
- **WHEN** `cleanup_session(session_id)` is called
- **THEN** the `_SessionContext` is removed from `_session_contexts`
- **AND** its `toolset_cache` is cleared
- **AND** its `connection_pool.cleanup()` is called
- **AND** its `snapshot` is set to `None`
- **AND** any ACP connections tracked by the session are cleaned up via `AcpMcpConnectionManager.cleanup_session(session_id)`

#### Scenario: Cleanup of non-existent session
- **WHEN** `cleanup_session(session_id)` is called for a session with no context
- **THEN** the call is a no-op (no error raised)

### Requirement: Session-scoped toolsets SHALL NOT be cached in the shared _toolset_cache

The `MCPManager._toolset_cache` SHALL only cache toolsets for global configs (pool-level and agent-level). Session-scoped configs (session-level and skill-level) SHALL use the per-session `toolset_cache` in `_SessionContext`. This ensures session-scoped toolsets are cleaned up when the session closes and never leak across sessions.

#### Scenario: Global config toolset caching
- **WHEN** `as_capability(session_id)` processes a global config entry
- **THEN** the toolset is looked up from or created in `_toolset_cache` (shared, long-lived)
- **AND** the same toolset instance is returned across multiple calls and sessions

#### Scenario: Session-scoped config toolset caching
- **WHEN** `as_capability(session_id)` processes a session-scoped config entry
- **THEN** the toolset is looked up from or created in `_SessionContext.toolset_cache` (per-session)
- **AND** the toolset is NOT stored in the shared `_toolset_cache`

#### Scenario: Session-scoped toolset does not leak across sessions
- **WHEN** session 1 creates a toolset for an ACP MCP server with `client_id="acp_server1"`
- **AND** session 1 is closed via `cleanup_session("ses_1")`
- **AND** session 2 creates a toolset for the same `client_id="acp_server1"`
- **THEN** session 2 gets a fresh toolset with the session 2 transport
- **AND** the fresh toolset does NOT reference the session 1 transport

### Requirement: as_capability SHALL accept session_id parameter

The `MCPManager.as_capability()` method SHALL accept an optional `session_id: str | None` parameter. When `session_id` is provided, the method SHALL look up the session context and process both global and session-scoped configs. When `session_id` is `None`, only global configs are processed.

#### Scenario: as_capability with session_id
- **WHEN** `as_capability(session_id="ses_1")` is called
- **THEN** capabilities are built from both global configs (via `_toolset_cache`) and session-scoped configs (via `_SessionContext.toolset_cache`)

#### Scenario: as_capability without session_id
- **WHEN** `as_capability(session_id=None)` is called
- **THEN** only global configs are processed (via `_toolset_cache`)

### Requirement: AcpMcpConnectionManager SHALL track session-owned connections

The `AcpMcpConnectionManager` SHALL maintain a `_session_connections: dict[str, set[str]]` mapping session IDs to sets of connection IDs. When a session creates an ACP MCP connection via `connect_acp_mcp_server()`, the connection ID SHALL be registered under the session's entry.

#### Scenario: Connection registration
- **WHEN** a session creates an ACP MCP connection
- **THEN** the connection ID is added to `_session_connections[session_id]`

#### Scenario: Session cleanup removes connections
- **WHEN** `cleanup_session(session_id)` is called on `AcpMcpConnectionManager`
- **THEN** all connection IDs for that session are retrieved from `_session_connections`
- **AND** for each connection, the session's stream pair is unregistered
- **AND** connections with no remaining active sessions are removed and closed
- **AND** connections still used by other sessions are preserved

#### Scenario: Shared connection not removed when child session closes
- **WHEN** parent session creates an ACP MCP connection
- **AND** child session inherits the same connection via `copy_pre_created_transports()`
- **AND** child session is closed via `cleanup_session(child_session_id)`
- **THEN** the connection's child session stream pair is unregistered
- **AND** the connection itself is NOT removed (parent session still active)

### Requirement: ACPSession.close() SHALL clean up MCP resources

The `ACPSession.close()` method SHALL call `self.agent.mcp.cleanup_session(session_id)` to clean up all session-scoped MCP resources before cleaning environment, signals, and prompts.

#### Scenario: Session close cleans MCP resources
- **WHEN** `ACPSession.close()` is called
- **THEN** `self.agent.mcp.cleanup_session(session_id)` is called first
- **AND** then existing cleanup (env, signals, prompts) proceeds as before

### Requirement: cleanup_session SHALL be concurrency-safe and idempotent

The `MCPManager.cleanup_session(session_id)` method SHALL use a per-session lock to prevent concurrent execution. If called concurrently for the same session (e.g., from WebSocket disconnect + SessionController.close_session), the second call SHALL be a no-op. The method SHALL always pop the session from `_session_contexts` in a finally block, even if intermediate cleanup steps raise exceptions.

#### Scenario: Concurrent cleanup calls
- **WHEN** `cleanup_session(session_id)` is called twice concurrently for the same session
- **THEN** the first call acquires the lock and performs cleanup
- **AND** the second call waits for the lock, then finds the session already removed and is a no-op
- **AND** no double-cleanup of connection pools or ACP connections occurs

#### Scenario: Cleanup error recovery
- **WHEN** `cleanup_session(session_id)` is called and `connection_pool.cleanup()` raises an exception
- **THEN** the exception is logged
- **AND** the session is still popped from `_session_contexts` (finally block)
- **AND** subsequent `cleanup_session(session_id)` calls are no-ops

### Requirement: WebSocket disconnect SHALL trigger session cleanup via SessionController

When an ACP WebSocket connection drops, the system SHALL close all sessions associated with that connection through `SessionController.close_session()` (which handles RunHandle lifecycle with timeout + cancel), not raw `ACPSession.close()`. This ensures active runs are safely cancelled before MCP resource cleanup.

#### Scenario: WebSocket disconnect closes sessions
- **WHEN** `_handle_websocket_client()` catches `ConnectionClosed`
- **THEN** the `on_disconnect` callback is called with the `AgentSideConnection`
- **AND** `ACPSessionManager.close_all_sessions_for_connection()` is invoked
- **AND** each session is closed via `SessionController.close_session()` (which waits for RunHandle with timeout)
- **AND** each session close calls `cleanup_session(session_id)` which cleans MCP resources

#### Scenario: WebSocket disconnect during active run
- **WHEN** WebSocket disconnects while a session has an active run
- **THEN** `SessionController.close_session()` cancels the RunHandle with timeout
- **AND** after RunHandle completion/cancellation, `cleanup_session()` cleans MCP resources
- **AND** no MCP toolsets are closed mid-tool-call

### Requirement: resume_session SHALL NOT return stale sessions

The `ACPSessionManager.resume_session()` method SHALL NOT return a session with stale MCP resources. If a session already exists in `_acp_sessions`, it SHALL be closed first via `SessionController.close_session()` (which handles RunHandle lifecycle with timeout + cancel), then a fresh session SHALL be created with new MCP resources.

#### Scenario: Resume after WebSocket reconnect
- **WHEN** `resume_session(session_id)` is called for a session that already exists in `_acp_sessions`
- **THEN** the existing session is closed via `SessionController.close_session()` (which waits for active runs)
- **AND** the session is removed from `_acp_sessions`
- **AND** a new session is created with fresh MCP resources via `initialize_mcp_servers()`

#### Scenario: Resume session with active run
- **WHEN** `resume_session(session_id)` is called for a session with an active run
- **THEN** `SessionController.close_session()` cancels the RunHandle with timeout
- **AND** after RunHandle completion/cancellation, MCP cleanup runs
- **AND** the new session is created with fresh MCP resources
