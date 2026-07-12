## ADDED Requirements

### Requirement: MCPManager SHALL provide public get_session_context accessor

The `MCPManager` SHALL provide a `get_session_context(session_id: str) -> _SessionContext | None` method that returns the session context for the given session ID without creating one. This method SHALL return `None` if no context exists, avoiding phantom context creation. External callers SHALL use this method instead of directly accessing `_session_contexts`.

#### Scenario: Existing session context retrieved
- **WHEN** `get_session_context(session_id)` is called for a session that has a context
- **THEN** the existing `_SessionContext` is returned
- **AND** no new context is created

#### Scenario: Non-existent session context returns None
- **WHEN** `get_session_context(session_id)` is called for a session that has no context
- **THEN** `None` is returned
- **AND** no new context is created in `_session_contexts`

### Requirement: Agent SHALL NOT hold MCP snapshot state

The `Agent` class SHALL NOT maintain `_mcp_snapshot` or `_session_connection_pool` fields. All MCP session state SHALL reside exclusively on `MCPManager._session_contexts` as the single source of truth. Snapshot reads and writes SHALL go through `MCPManager.get_session_context()`, `get_or_create_session()`, and `update_session_snapshot()`.

#### Scenario: Agent has no MCP snapshot fields
- **WHEN** an `Agent` instance is created
- **THEN** the instance does not have `_mcp_snapshot` or `_session_connection_pool` attributes
- **AND** accessing either attribute raises `AttributeError`

#### Scenario: Skill configs written to session context
- **WHEN** `get_agentlet()` collects skill config entries from visible `SkillCapability` instances
- **THEN** skill configs are written to `MCPManager._session_contexts[session_id].snapshot` via `update_session_snapshot()`
- **AND** the skill configs are NOT written to any Agent-local field

#### Scenario: Skill config registration ordering preserved
- **WHEN** `get_agentlet()` executes
- **THEN** `as_capability(session_id)` is called BEFORE skill configs are written to the session context snapshot
- **AND** this ordering prevents `as_capability()` from processing skill configs (which would duplicate tools handled by `SkillCapability`)

## MODIFIED Requirements

### Requirement: MCPManager SHALL track per-session MCP resources

The `MCPManager` SHALL maintain a `_session_contexts: dict[str, _SessionContext]` mapping session IDs to per-session MCP state. Each `_SessionContext` SHALL contain: a `SessionConnectionPool`, a per-session `toolset_cache: dict[str, MCPToolset]`, a `McpConfigSnapshot`, and a list of ACP connection IDs owned by that session. The `MCPManager` SHALL also provide a `get_session_context(session_id)` method that returns the context without creating one.

#### Scenario: Session context creation
- **WHEN** `get_or_create_session(session_id)` is called for a session that does not yet have a context
- **THEN** a new `_SessionContext` is created with an empty `SessionConnectionPool`, empty `toolset_cache`, `None` snapshot, and empty `acp_connection_ids` list
- **AND** it is stored in `_session_contexts[session_id]`

#### Scenario: Session context retrieval
- **WHEN** `get_or_create_session(session_id)` is called for a session that already has a context
- **THEN** the existing `_SessionContext` is returned without creating a new one

#### Scenario: Session context lookup without creation
- **WHEN** `get_session_context(session_id)` is called for a session that already has a context
- **THEN** the existing `_SessionContext` is returned without creating a new one

#### Scenario: Session context lookup returns None for unknown session
- **WHEN** `get_session_context(session_id)` is called for a session that does not have a context
- **THEN** `None` is returned without creating a new context

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

#### Scenario: Session context is None after cleanup
- **WHEN** `get_session_context(session_id)` is called after `cleanup_session(session_id)` has been called
- **THEN** `None` is returned (no stale context remains)

### Requirement: Tests SHALL use public get_session_context accessor

Tests SHALL NOT directly access `MCPManager._session_contexts` private dict for assertions. Tests SHALL use `get_session_context()` to verify session context state. This ensures tests validate behavior through the public interface and remain stable when internal implementation changes.

#### Scenario: Pool-level MCP tool inherited by subagent
- **WHEN** a pool-level MCP server with a mock tool (e.g., `search_kb`) is configured
- **AND** a child session is spawned (e.g., librarian subagent pattern)
- **THEN** `get_session_context(child_session_id)` returns a context whose snapshot contains the pool-level MCP config
- **AND** `as_capability(child_session_id)` produces a toolset that includes the mock tool
