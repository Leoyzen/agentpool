## Context

AgentPool's MCP-over-ACP integration has a critical lifecycle bug (#121): session-scoped MCP resources (toolsets, transports, ACP connections) are never cleaned up when sessions close or WebSocket connections drop. The root cause is that session-scoped MCP state is scattered across 4 different objects with no coordinated cleanup:

- `MCPManager._toolset_cache` (shared, caches session-scoped toolsets by deterministic `client_id`)
- `Agent._session_connection_pool` (per-session, `cleanup()` exists but is never called)
- `Agent._mcp_snapshot` (per-session, never reverted on close)
- `AcpMcpConnectionManager._connections` (global flat dict, no per-session tracking)

The `_toolset_cache` was deliberately removed in commit `88bdc1758` to fix cross-task CancelScope errors, then re-added in `d3b4966c8` as a regression. The original #88 design intended "fresh MCPToolset per agentlet from snapshot + connection pools" with no caching.

This change is Phase 1 of a 2-phase MCP lifecycle redesign. Phase 1 fixes the lifecycle bugs without changing the config API. Phase 2 (future) will remove the per-agent MCP tier entirely, moving all MCP server declarations to pool level with agent-level allow/block filtering.

**MCPManager scope clarification**: `_session_contexts` goes on the **agent's** MCPManager — the one `get_agentlet()` calls `as_capability()` on (`self.mcp` in `agent.py:901`). When `_mcp_shared=True` (agent has no agent-level servers), this is the pool's MCPManager. When `_mcp_shared=False`, it's the agent's dedicated MCPManager. `ACPSession.close()` calls `self.agent.mcp.cleanup_session(session_id)`. In Phase 2, all agents will share the pool's MCPManager, so `_session_contexts` moves there permanently.

## Goals / Non-Goals

**Goals:**
- Eliminate stale MCP toolset references on session resume
- Ensure all session-scoped MCP resources are cleaned up on session close
- Ensure ACP MCP connections are tracked per-session and cleaned up on close
- Handle WebSocket disconnect by closing all associated sessions (with active run safety)
- Fix `resume_session()` to not return stale sessions with zombie connections
- Maintain backward compatibility — no config API changes

**Non-Goals:**
- Removing per-agent MCPManager (Phase 2)
- Adding allow/block list config for MCP servers (Phase 2)
- Consolidating skill MCP dual paths (Phase 2)
- ACP v2 protocol migration (#109)
- Changing the `MCPResourceProvider` model

## Decisions

### D1: Session tracking on MCPManager (not a separate SessionMcpContext class)

**Decision**: Add `_session_contexts: dict[str, _SessionContext]` directly on `MCPManager` instead of creating a separate `SessionMcpContext` class.

**Rationale**: Fewer classes, simpler wiring. `ACPSession.close()` and `SessionController.close_session()` only need `session_id` — no need to obtain a `SessionMcpContext` reference. The `_SessionContext` is an internal dataclass, not a public API.

**Scope**: `_session_contexts` goes on the **agent's** MCPManager (`self.mcp` in `agent.py:901`). When `_mcp_shared=True`, this is `agent_pool.mcp`. `ACPSession.close()` calls `self.agent.mcp.cleanup_session(session_id)`.

**Alternative considered**: Separate `SessionMcpContext` class owned by the session. Rejected because it requires wiring the context into `ACPSession`, `SessionController`, and `Agent` — more touch points for the same functionality.

### D2: Per-session toolset cache (not "no cache")

**Decision**: Session-scoped configs get a per-session toolset cache in `_SessionContext.toolset_cache`, cleared on `cleanup_session()`. Not a return to "fresh MCPToolset per call" (the #88 design).

**Rationale**: Creating a fresh `MCPToolset` per `as_capability()` call produces redundant objects across agentlet forks and cross-turn calls. `MCPToolset` is a lightweight wrapper around a transport; the expensive part (subprocess/HTTP connection) lives in the transport, which is already pooled in `SessionConnectionPool`. Per-session caching gives stale-free behavior (cleared on close) without per-turn overhead.

**Alternative considered**: No caching for session-scoped configs (pure #88 design). Rejected because per-session caching is standard practice and avoids edge cases with `MCPToolset` internal state across multiple `__aenter__`/`__aexit__` cycles within a session.

### D3: Global `_toolset_cache` retained for pool-level configs only

**Decision**: Keep `MCPManager._toolset_cache` but only for global configs (pool + agent level). Session-scoped configs bypass it entirely.

**Rationale**: Pool-level MCP servers (stdio processes, HTTP clients) are long-lived and shared across all sessions. Caching their toolsets is safe — the transport doesn't change. Session-scoped configs have per-session transports, so caching them in a shared dict is a category error.

### D4: `as_capability(session_id)` simplified API

**Decision**: Change `as_capability(snapshot=..., session_pool=...)` to `as_capability(session_id)`. The MCPManager looks up the session context internally.

**Rationale**: Reduces coupling — callers don't need to manage snapshot and connection pool references. The MCPManager owns all session-scoped state, making cleanup a single `cleanup_session(session_id)` call.

**Migration**: `get_agentlet()` in `agent.py` is the only caller. Updated in the same PR. `session_id=None` returns global-only capabilities for backward compat.

### D5: AcpMcpConnectionManager session tracking via reverse index

**Decision**: Add `_session_connections: dict[str, set[str]]` (session_id → connection_ids) on `AcpMcpConnectionManager`. On `cleanup_session()`, unregister session stream pairs and remove connections with no active sessions.

**Rationale**: `AcpMcpConnection._session_streams` uses opaque int keys (`_next_session_key` auto-increment). Rather than changing the key type (which would require changes throughout the ACP MCP transport layer), add a reverse index at the manager level.

**Implementation detail**: `_SessionContext.acp_connection_ids` stores `(connection_id, session_key: int)` tuples — the `session_key` is the int returned by `AcpMcpConnection.register_session()`. On cleanup, `unregister_session()` is called with the stored `SessionStreamPair` (looked up by `session_key`). This is required because `unregister_session()` takes a `SessionStreamPair` object, not a key.

**Alternative considered**: Change `_session_streams` keys from `int` to `str` (session_id). Rejected because it touches `AcpMcpTransport.connect_session()` and `AcpMcpConnection` internals — more invasive than a manager-level index.

### D6: `resume_session()` close-then-recreate via SessionController + ACPSession

**Decision**: Remove the early-return in `resume_session()`. On resume, if a session exists, close it via a two-layer cleanup: (1) `SessionController.close_session()` (handles RunHandle lifecycle with timeout + cancel, calls `agent.mcp.cleanup_session()` via task 4.2, calls `agent.__aexit__()`), then (2) `ACPSession.close()` (cleans ACP-specific state: `acp_env`, `state_updated` signal, `sys_prompts`, skill command callback). Remove from `_acp_sessions`, then create a fresh session.

**Two-layer cleanup rationale**: `SessionController.close_session()` handles the orchestrator layer (RunHandle, agent context, MCP cleanup) but has no knowledge of `ACPSession` (a server/transport-specific class). `ACPSession.close()` handles the ACP layer (env, signals, prompts) but doesn't handle RunHandle lifecycle. Both must be called for complete cleanup. MCP cleanup is idempotent via D8's per-session lock — calling `cleanup_session()` twice (once from each layer) is safe.

**Fallback**: In contexts where `SessionController` is not available (e.g., tests), fall back to `ACPSession.close()` only.

**Revised from initial design**: Initial design called `ACPSession.close()` directly, which does NOT wait for active runs. Oracle review (round 1) identified this as insufficient. Round 2 revision used `SessionController.close_session()` only, but Gemini Code Assist review identified that this leaks ACP-specific resources. This round 3 revision calls both layers.

### D7: WebSocket disconnect hook via on_disconnect callback

**Decision**: Add an `on_disconnect: Callable[[AgentSideConnection], Awaitable[None]] | None` parameter to `_handle_websocket_client()`. When `ConnectionClosed` is caught, call `on_disconnect(conn)` before `conn.close()`. The callback delegates to `ACPSessionManager.close_all_sessions_for_connection()`, which performs two-layer cleanup for each session: (1) `SessionController.close_session()` (RunHandle lifecycle), then (2) `ACPSession.close()` (ACP-specific cleanup).

**Two-layer cleanup**: Same rationale as D6 — `SessionController` handles the orchestrator layer, `ACPSession.close()` handles the ACP layer. Both must be called for complete cleanup on WebSocket disconnect.

**Wiring**:
1. `_handle_websocket_client()` gains `on_disconnect` callback parameter
2. `ACPWebSocketTransport` (or the server that creates it) passes a callback that calls `ACPSessionManager.close_all_sessions_for_connection()`
3. `ACPSessionManager` gains `_connection_sessions: dict[str, set[str]]` (connection_id → session_ids) reverse mapping to track which sessions belong to which WebSocket connection
4. `close_all_sessions_for_connection()` iterates sessions and calls both `SessionController.close_session()` and `ACPSession.close()` for each

**Revised from initial design**: Initial design called `ACPSessionManager.close_all_sessions_for_connection()` directly in the `ConnectionClosed` handler. Oracle review (round 1) identified that `_handle_websocket_client()` has no reference to `ACPSessionManager` and that sessions must be closed through `SessionController.close_session()` to handle active runs. The callback approach decouples the transport layer from the session manager. Gemini Code Assist review (round 2) identified that `SessionController.close_session()` alone leaks ACP-specific resources — this round 3 revision calls both layers.

### D8: Concurrency protection for cleanup_session()

**Decision**: Add an `asyncio.Lock` per session in `MCPManager._session_contexts` cleanup. The `_SessionContext` dataclass includes a `_cleanup_lock: asyncio.Lock` field. `cleanup_session()` acquires the lock before proceeding; if the lock is already held (concurrent cleanup from WebSocket disconnect + SessionController), the second call is a no-op.

**Rationale**: WebSocket disconnect and `SessionController.close_session()` can fire concurrently for the same session. Without a lock, `connection_pool.cleanup()` and `AcpMcpConnectionManager.cleanup_session()` may be called twice. The lock ensures idempotent cleanup.

**Error recovery**: `cleanup_session()` wraps all steps in try/finally — `_session_contexts.pop(session_id)` always runs last, even if intermediate steps (toolset cache clear, connection pool cleanup, ACP cleanup) raise exceptions.

**Added from Oracle review**: Oracle identified that concurrent `cleanup_session()` calls from WebSocket disconnect + SessionController.close_session() could cause double-cleanup. The lock + try/finally pattern ensures safe concurrent invocation.

## Risks / Trade-offs

- **[Risk: `cleanup_session()` not called on all paths]** → Mitigation: Add cleanup to `ACPSession.close()`, `SessionController.close_session()`, and WebSocket disconnect hook (via `SessionController.close_session()`). Add test verifying no leak after each path.

- **[Risk: Per-session toolset cache leaks if `cleanup_session()` is missed]** → Mitigation: Same as above. The per-session cache is in `_session_contexts[session_id]`, which is popped on cleanup. If missed, the entry remains but doesn't affect other sessions (unlike the current shared cache).

- **[Risk: `as_capability(session_id)` API change breaks external callers]** → Mitigation: `get_agentlet()` is the only caller. Support `session_id=None` for backward compat (returns global-only capabilities).

- **[Risk: Close-then-recreate in `resume_session()` adds latency]** → Mitigation: `SessionController.close_session()` has a 10s timeout for active runs. The recreate path already runs `initialize_mcp_servers()`. Net latency increase is bounded by the RunHandle timeout.

- **[Risk: Concurrent cleanup from WebSocket disconnect + SessionController]** → Mitigation: D8 — per-session `asyncio.Lock` ensures idempotent cleanup. Try/finally ensures `_session_contexts.pop()` always runs.

- **[Risk: Cleanup during active run closes MCP toolsets mid-tool-call]** → Mitigation: D6 + D7 — all close paths go through `SessionController.close_session()`, which waits for RunHandle completion (10s timeout + cancel) before calling `cleanup_session()`.

- **[Risk: Parent session close while child sessions active]** → Mitigation: `SessionController._close_session_run_turn()` already cascades child session close (unless `lifecycle_policy == "independent"`). Independent child sessions must call `register_session_connection()` on their own `session_id`. The `has_active_sessions()` check in `AcpMcpConnectionManager.cleanup_session()` preserves connections still used by other sessions.

- **[Trade-off: `AcpMcpConnectionManager` reverse index adds memory]** → Negligible: one `dict[str, set[str]]` entry per session. Cleared on `cleanup_session()`.

- **[Trade-off: `_handle_websocket_client()` gains `on_disconnect` parameter]** → Minor API change in internal transport function. Only called from `ACPWebSocketTransport` server setup, not public API.
