## 1. MCPManager Session Tracking (P1a — foundation)

- [ ] 1.1 Add `_SessionContext` dataclass to `manager.py` with fields: `connection_pool: SessionConnectionPool`, `toolset_cache: dict[str, Any]`, `snapshot: McpConfigSnapshot | None`, `acp_connection_ids: list[tuple[str, int]]` (connection_id, session_key pairs), `_cleanup_lock: asyncio.Lock`
- [ ] 1.2 Add `_session_contexts: dict[str, _SessionContext]` to `MCPManager.__init__`
- [ ] 1.3 Implement `MCPManager.get_or_create_session(session_id: str) -> _SessionContext` — creates if missing, returns existing if present
- [ ] 1.4 Implement `MCPManager.update_session_snapshot(session_id: str, snapshot: McpConfigSnapshot)` — stores snapshot in session context
- [ ] 1.5 Implement `MCPManager.add_acp_transport(session_id: str, client_id: str, transport: ClientTransport, connection_id: str, session_key: int)` — adds transport to session context's connection pool, stores `(connection_id, session_key)` in `acp_connection_ids`
- [ ] 1.6 Implement `MCPManager.cleanup_session(session_id: str)` — acquires `_cleanup_lock`, then in try/except/finally: clears toolset cache, calls `connection_pool.cleanup()`, delegates to `AcpMcpConnectionManager.cleanup_session(session_id)`, clears snapshot; catches and logs exceptions from intermediate steps (does not re-raise); always pops from `_session_contexts` in finally block
- [ ] 1.7 Add unit tests for session context creation, retrieval, cleanup, and concurrent cleanup (double-call is no-op) in `tests/mcp_server/test_session_lifecycle.py`

## 2. as_capability Session-Aware API (P1b — depends on P1a)

- [ ] 2.1 Change `as_capability()` signature to `as_capability(session_id: str | None = None)` — replaces `snapshot` and `session_pool` params
- [ ] 2.2 Modify `_make_capability()` to accept `cache: bool` parameter — when `False`, uses per-session toolset cache instead of shared `_toolset_cache`
- [ ] 2.3 Modify `_process_snapshot()` to pass `cache=False` for session-scoped configs and `cache=True` for global configs
- [ ] 2.4 When `session_id` is provided, look up `_SessionContext` and use its snapshot + connection pool + toolset cache
- [ ] 2.5 When `session_id` is `None`, process only global configs from `self.servers` (backward compat)
- [ ] 2.6 Update `get_agentlet()` in `agent.py` to call `as_capability(session_id=self._session_id)` instead of `as_capability(snapshot=..., session_pool=...)`
- [ ] 2.7 Update existing tests in `test_mcpmanager_caching.py` to use new API
- [ ] 2.8 Update `test_stale_mcp_connection.py` tests — session 2 should now get fresh toolset (test flips from documenting bug to verifying fix)

## 3. AcpMcpConnectionManager Session Tracking (P1c — parallel with P1a)

- [ ] 3.1 Add `_session_connections: dict[str, set[str]]` to `AcpMcpConnectionManager.__init__`
- [ ] 3.2 Add `register_session_connection(session_id: str, connection_id: str)` method — adds connection_id to the session's set
- [ ] 3.3 Implement `cleanup_session(session_id: str)` on `AcpMcpConnectionManager` — pops session's connection IDs, for each: look up `AcpMcpConnection`, call `unregister_session(pair)` for the session's stream pair (using stored `session_key`), remove connections with no active sessions
- [ ] 3.4 Add `has_active_sessions()` method to `AcpMcpConnection` — returns `True` if `_session_streams` is non-empty
- [ ] 3.5 Wire `register_session_connection()` into `connect_acp_mcp_server()` in `acp_agent.py` — pass `session_id` from the calling `ACPSession`; also store the `session_key` (int returned by `register_session()`) in `_SessionContext.acp_connection_ids` via `MCPManager.add_acp_transport()`
- [ ] 3.6 Add unit tests for session connection tracking, cleanup, and shared connection preservation in `tests/agentpool_server/acp_server/test_acp_mcp_session_cleanup.py`

## 4. Wire cleanup_session into Close Paths (P1d — depends on P1a + P1c)

- [ ] 4.1 Add `self.agent.mcp.cleanup_session(session_id)` call to `ACPSession.close()` in `session.py` — before existing env/signal/prompt cleanup
- [ ] 4.2 Add `agent.mcp.cleanup_session(session_id)` call to `SessionController._close_session_run_turn()` in `session_controller.py` — before `agent.__aexit__()`
- [ ] 4.3 Ensure `get_or_create_session_agent()` in `session_controller.py` calls `agent.mcp.get_or_create_session(session_id)` when creating a new agent
- [ ] 4.4 Store `session_id` on `Agent` (e.g., `self._session_id`) so `get_agentlet()` can pass it to `as_capability()`
- [ ] 4.5 Add integration test: create session → run turn → close session → verify `_session_contexts` is empty and `_toolset_cache` has no session-scoped entries
- [ ] 4.6 Add integration test: create session → close → create new session with same ID → verify fresh MCP resources
- [ ] 4.7 Add test: concurrent `cleanup_session()` calls (simulating WebSocket disconnect + SessionController.close_session) → verify no double-cleanup, no errors

## 5. Fix resume_session Early-Return (P1e — depends on P1d)

- [ ] 5.1 Remove the early-return at `session_manager.py:244-249` that returns stale session when `session_id in self._acp_sessions`
- [ ] 5.2 Replace with: close existing session via `SessionController.close_session(session_id)` (handles RunHandle lifecycle with timeout + cancel), remove from `_acp_sessions`, then proceed to create fresh session. Fallback to `ACPSession.close()` when `SessionController` is unavailable (tests)
- [ ] 5.3 Add test: resume existing session → verify old session is closed via `SessionController.close_session()` (RunHandle lifecycle handled) → verify new session has fresh MCP resources
- [ ] 5.4 Add test: resume after WebSocket reconnect → verify fresh ACP connections are created → verify no stale connection references
- [ ] 5.5 Add test: resume session with active run → verify RunHandle is cancelled with timeout before cleanup

## 6. WebSocket Disconnect Hook (P1f — depends on P1d + P1c)

- [ ] 6.1 Add `on_disconnect: Callable[[AgentSideConnection], Awaitable[None]] | None` parameter to `_handle_websocket_client()` in `transports.py`
- [ ] 6.2 In the `ConnectionClosed` exception handler (line 412), call `on_disconnect(conn)` before `conn.close()` in the finally block
- [ ] 6.3 Add `_connection_sessions: dict[str, set[str]]` (connection_id → session_ids) to `ACPSessionManager` — populated when sessions are created/resumed
- [ ] 6.4 Implement `ACPSessionManager.close_all_sessions_for_connection(connection_id: str)` — iterates sessions for the connection and calls `SessionController.close_session()` for each (not raw `ACPSession.close()`)
- [ ] 6.5 Wire the `on_disconnect` callback in the server setup that creates `_handle_websocket_client()` — pass a callback that calls `ACPSessionManager.close_all_sessions_for_connection()`
- [ ] 6.6 Add test: WebSocket disconnect → verify all sessions for that connection are closed via `SessionController.close_session()` → verify `cleanup_session()` called for each
- [ ] 6.7 Add test: WebSocket disconnect → verify sessions on other connections are NOT affected
- [ ] 6.8 Add test: WebSocket disconnect during active run → verify RunHandle is cancelled with timeout before cleanup

## 7. End-to-End Verification

- [ ] 7.1 Run full test suite: `uv run pytest tests/mcp_server/ tests/agentpool_server/acp_server/ -v`
- [ ] 7.2 Run `uv run pytest -m unit` — all unit tests pass
- [ ] 7.3 Run `uv run ruff check src/` — no lint errors
- [ ] 7.4 Run `uv run --no-group docs mypy src/` — no type errors on changed files
- [ ] 7.5 Manual ACP test: connect → create session → use MCP tool → disconnect WebSocket → reconnect → resume session → verify MCP tools work with fresh connections
