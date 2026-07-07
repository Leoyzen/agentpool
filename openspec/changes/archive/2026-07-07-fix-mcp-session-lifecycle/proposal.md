## Why

MCP-over-ACP resources leak across session boundaries, causing silent session failures on resume. When a session is resumed after WebSocket reconnection, stale MCP toolsets, transports, and ACP connections from previous sessions are never cleaned up — the agentlet tries to initialize MCP via dead transports, causing a 300-second timeout. The root cause is that session-scoped MCP state is scattered across 4 different objects (`MCPManager._toolset_cache`, `Agent._session_connection_pool`, `Agent._mcp_snapshot`, `AcpMcpConnectionManager._connections`) with no coordinated cleanup on session close. This is a production bug tracked in #121.

## What Changes

- Add session-level tracking to `MCPManager` via `_session_contexts: dict[str, _SessionContext]`, holding per-session snapshot, connection pool, toolset cache, and ACP connection IDs
- Remove session-scoped toolset caching from the shared `_toolset_cache` — only global (pool + agent) configs are cached; session-scoped configs use per-session toolset cache
- Add `MCPManager.cleanup_session(session_id)` that cleans all session-scoped MCP resources in one call
- Add `AcpMcpConnectionManager.cleanup_session(session_id)` with `_session_connections: dict[str, set[str]]` tracking for per-session connection cleanup
- Fix `AcpMcpConnection._session_streams` to track `session_id` alongside stream pairs (currently uses opaque int keys)
- Wire `cleanup_session()` into `ACPSession.close()` and `SessionController.close_session()`
- Fix `ACPSessionManager.resume_session()` early-return — close stale session before recreating with fresh MCP resources
- Add WebSocket disconnect hook — when ACP WebSocket drops, close all sessions for that connection
- Simplify `as_capability()` API to `as_capability(session_id)` — MCPManager looks up session context internally

## Capabilities

### New Capabilities

- `mcp-session-lifecycle`: Per-session MCP resource tracking and deterministic cleanup. Covers session context creation, toolset caching scope, ACP connection tracking, and cleanup on session close / WebSocket disconnect.

### Modified Capabilities

- `session-orchestration`: Session close path must call `MCPManager.cleanup_session()` before agent cleanup. Session resume must close stale session before recreating.
- `unified-session-lifecycle`: WebSocket disconnect triggers session cleanup including MCP resources.

## Impact

- **`src/agentpool/mcp_server/manager.py`** — Add `_session_contexts`, `_SessionContext` dataclass, `get_or_create_session()`, `cleanup_session()`, modify `as_capability()` signature
- **`src/agentpool_server/acp_server/acp_mcp_manager.py`** — Add `_session_connections` tracking, `cleanup_session()`, fix `AcpMcpConnection` session tracking
- **`src/agentpool_server/acp_server/session.py`** — `ACPSession.close()` calls `cleanup_session()`
- **`src/agentpool_server/acp_server/session_manager.py`** — Fix `resume_session()` early-return
- **`src/agentpool/orchestrator/session_controller.py`** — `close_session()` calls `cleanup_session()`
- **`src/agentpool/agents/native_agent/agent.py`** — `get_agentlet()` uses `as_capability(session_id)` instead of `as_capability(snapshot=..., session_pool=...)`
- **`src/agentpool/messaging/messagenode.py`** — No change (shared MCPManager retained for Phase 1)
- **`src/acp/transports.py`** — WebSocket disconnect hook
- **Tests**: `tests/mcp_server/test_stale_mcp_connection.py` (existing, 5 tests) + new tests for session cleanup
- **No config changes** — existing YAML configs work unchanged
- **No breaking API changes** — `as_capability()` signature change is internal (callers updated in same PR)
