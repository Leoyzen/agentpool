## 1. MCPManager Session Tracking (P1a — foundation)

- [x] 1.1 Add `_SessionContext` dataclass to `manager.py` with fields: `connection_pool: SessionConnectionPool`, `toolset_cache: dict[str, Any]`, `snapshot: McpConfigSnapshot | None`, `acp_connection_ids: list[tuple[str, int]]` (connection_id, session_key pairs), `_cleanup_lock: asyncio.Lock`
- [x] 1.2 Add `_session_contexts: dict[str, _SessionContext]` to `MCPManager.__init__`
- [x] 1.3 Implement `MCPManager.get_or_create_session(session_id: str) -> _SessionContext` — creates if missing, returns existing if present
- [x] 1.4 Implement `MCPManager.update_session_snapshot(session_id: str, snapshot: McpConfigSnapshot)` — stores snapshot in session context
- [x] 1.5 Implement `MCPManager.add_acp_transport(session_id: str, client_id: str, transport: ClientTransport, connection_id: str, session_key: int)` — adds transport to session context's connection pool, stores `(connection_id, session_key)` in `acp_connection_ids`
- [x] 1.6 Implement `MCPManager.cleanup_session(session_id: str)` — acquires `_cleanup_lock`, then in try/except/finally: clears toolset cache, calls `connection_pool.cleanup()`, delegates to `AcpMcpConnectionManager.cleanup_session(session_id)`, clears snapshot; catches and logs exceptions from intermediate steps (does not re-raise); always pops from `_session_contexts` in finally block
- [x] 1.7 Add unit tests for session context creation, retrieval, cleanup, and concurrent cleanup (double-call is no-op) in `tests/mcp_server/test_session_lifecycle.py`

## 2. as_capability Session-Aware API (P1b — depends on P1a)

- [x] 2.1 Change `as_capability()` signature to `as_capability(session_id: str | None = None)` — replaces `snapshot` and `session_pool` params
- [x] 2.2 Modify `_make_capability()` to accept `cache: bool` parameter — when `False`, uses per-session toolset cache instead of shared `_toolset_cache`
- [x] 2.3 Modify `_process_snapshot()` to pass `cache=False` for session-scoped configs and `cache=True` for global configs
- [x] 2.4 When `session_id` is provided, look up `_SessionContext` and use its snapshot + connection pool + toolset cache
- [x] 2.5 When `session_id` is `None`, process only global configs from `self.servers` (backward compat)
- [x] 2.6 Update `get_agentlet()` in `agent.py` to call `as_capability(session_id=self._session_id)` instead of `as_capability(snapshot=..., session_pool=...)`
- [x] 2.7 Update existing tests in `test_mcpmanager_caching.py` to use new API
- [x] 2.8 Update `test_stale_mcp_connection.py` tests — session 2 should now get fresh toolset (test flips from documenting bug to verifying fix). Use `try/finally` or `@pytest.fixture` teardown for resource cleanup; current tests skip cleanup on assertion failure

## 3. AcpMcpConnectionManager Session Tracking (P1c — parallel with P1a)

- [x] 3.1 Add `_session_connections: dict[str, set[tuple[str, int]]]` to `AcpMcpConnectionManager.__init__` — maps `session_id` to `(connection_id, session_key)` pairs
- [x] 3.2 Add `register_session_connection(session_id: str, connection_id: str, session_key: int)` method — adds `(connection_id, session_key)` tuple to the session's set
- [x] 3.3 Implement `cleanup_session(session_id: str)` on `AcpMcpConnectionManager` — pops session's `(connection_id, session_key)` pairs, for each: look up `AcpMcpConnection` via `connection_id`, look up `SessionStreamPair` via `session_key` in `_session_streams`, call `unregister_session(pair)`, remove connections with no active sessions
- [x] 3.4 Add `has_active_sessions()` method to `AcpMcpConnection` — returns `True` if `_session_streams` is non-empty
- [x] 3.5 Wire `register_session_connection()` into `connect_acp_mcp_server()` in `acp_agent.py` — pass `session_id` from the calling `ACPSession`; also store the `session_key` (int returned by `register_session()`) in `_SessionContext.acp_connection_ids` via `MCPManager.add_acp_transport()`
- [x] 3.6 Add unit tests for session connection tracking, cleanup, and shared connection preservation in `tests/agentpool_server/acp_server/test_acp_mcp_session_cleanup.py`

## 4. Wire cleanup_session into Close Paths (P1d — depends on P1a + P1c)

- [x] 4.1 Add `self.agent.mcp.cleanup_session(session_id)` call to `ACPSession.close()` in `session.py` — before existing env/signal/prompt cleanup
- [x] 4.2 Add `agent.mcp.cleanup_session(session_id)` call to `SessionController._close_session_run_turn()` in `session_controller.py` — before `agent.__aexit__()`
- [x] 4.3 Ensure `get_or_create_session_agent()` in `session_controller.py` calls `agent.mcp.get_or_create_session(session_id)` when creating a new agent
- [x] 4.4 Store `session_id` on `Agent` (e.g., `self._session_id`) so `get_agentlet()` can pass it to `as_capability()`
- [x] 4.5 Add integration test: create session → run turn → close session → verify `_session_contexts` is empty and `_toolset_cache` has no session-scoped entries
- [x] 4.6 Add integration test: create session → close → create new session with same ID → verify fresh MCP resources
- [x] 4.7 Add test: concurrent `cleanup_session()` calls (simulating WebSocket disconnect + SessionController.close_session) → verify no double-cleanup, no errors

## 5. Fix resume_session Early-Return (P1e — depends on P1d)

- [x] 5.1 Remove the early-return at `session_manager.py:244-249` that returns stale session when `session_id in self._acp_sessions`
- [x] 5.2 Replace with: close existing session via `SessionController.close_session(session_id)` first (handles RunHandle lifecycle with timeout + cancel, calls `agent.mcp.cleanup_session()` via task 4.2, calls `agent.__aexit__()`). Then call `ACPSession.close()` for ACP-specific cleanup (acp_env, signals, prompts — which also calls `self.agent.mcp.cleanup_session()` via task 4.1, but cleanup_session is idempotent via D8 lock). Remove from `_acp_sessions`. Then proceed to create fresh session. Fallback to `ACPSession.close()` only when `SessionController` is unavailable (tests)
- [x] 5.3 Add test: resume existing session → verify old session is closed via `SessionController.close_session()` (RunHandle lifecycle handled) → verify new session has fresh MCP resources
- [x] 5.4 Add test: resume after WebSocket reconnect → verify fresh ACP connections are created → verify no stale connection references
- [x] 5.5 Add test: resume session with active run → verify RunHandle is cancelled with timeout before cleanup

## 6. WebSocket Disconnect Hook (P1f — depends on P1d + P1c)

- [x] 6.1 Add `on_disconnect: Callable[[AgentSideConnection], Awaitable[None]] | None` parameter to `_handle_websocket_client()` in `transports.py`
- [x] 6.2 In the `ConnectionClosed` exception handler (line 412), call `on_disconnect(conn)` before `conn.close()` in the finally block
- [x] 6.3 Add `_connection_sessions: dict[str, set[str]]` (connection_id → session_ids) to `ACPSessionManager` — populated when sessions are created/resumed
- [x] 6.4 Implement `ACPSessionManager.close_all_sessions_for_connection(connection_id: str)` — iterates sessions for the connection. For each session: call `SessionController.close_session(session_id)` first (RunHandle lifecycle with timeout + cancel), then call `ACPSession.close()` for ACP-specific cleanup (acp_env, signals, prompts). Both must be called — `SessionController` handles RunHandle + agent lifecycle, `ACPSession.close()` handles ACP-specific state
- [x] 6.5 Wire the `on_disconnect` callback in the server setup that creates `_handle_websocket_client()` — pass a callback that calls `ACPSessionManager.close_all_sessions_for_connection()`
- [x] 6.6 Add test: WebSocket disconnect → verify all sessions for that connection are closed via `SessionController.close_session()` → verify `cleanup_session()` called for each
- [x] 6.7 Add test: WebSocket disconnect → verify sessions on other connections are NOT affected
- [x] 6.8 Add test: WebSocket disconnect during active run → verify RunHandle is cancelled with timeout before cleanup

## 7. End-to-End Verification

- [x] 7.1 Run full test suite: `uv run pytest tests/mcp_server/ tests/agentpool_server/acp_server/ -v`
- [x] 7.2 Run `uv run pytest -m unit` — all unit tests pass
- [x] 7.3 Run `uv run ruff check src/` — no lint errors
- [x] 7.4 Run `uv run --no-group docs mypy src/` — no type errors on changed files
- [x] 7.5 Manual ACP test: connect → create session → use MCP tool → disconnect WebSocket → reconnect → resume session → verify MCP tools work with fresh connections
