# fix-mcp-session-lifecycle - Work Plan

## TL;DR (For humans)

**What you'll get:** MCP resources (toolsets, transports, ACP connections) are properly cleaned up when sessions close or WebSocket connections drop, eliminating stale toolset references on session resume. This is Phase 1 of a 2-phase MCP lifecycle redesign.

**Why this approach:** The root cause is that session-scoped MCP state is scattered across 4 objects (`_toolset_cache`, `_session_connection_pool`, `_mcp_snapshot`, `AcpMcpConnectionManager._connections`) with no coordinated cleanup. The fix centralizes session-scoped state into `_SessionContext` on `MCPManager`, adds a per-session `asyncio.Lock` for concurrency-safe idempotent `cleanup_session()`, and wires cleanup into all 3 close paths (ACPSession.close, SessionController.close_session, WebSocket disconnect hook). All 8 design decisions were made through 3 rounds of Oracle + Gemini review.

**What it will NOT do:** No changes to MessageNode base class, AgentPool registry, MCPResourceProvider model, or config API. No Phase 2 features (per-agent MCPManager removal, allow/block lists, pool-level MCP consolidation). No ACP v2 migration.

**Effort:** Large
**Risk:** Medium — touches 7 source files across 3 subsystems (MCP manager, ACP server, session orchestrator), but all design decisions are made and codebase state is verified
**Decisions to sanity-check:** D4 (as_capability(session_id) API change — only caller is get_agentlet), D5 (reverse index at manager level instead of changing _session_streams key type), D6 (two-layer close-then-recreate in resume_session)

Your next move: approve to start work, or run a high-accuracy review first. Full execution detail follows below.

---

> TL;DR (machine): Large effort, Medium risk, 33 todos across 7 waves — fix MCP session lifecycle by centralizing session-scoped state in _SessionContext, wiring cleanup into all close paths, and fixing resume_session.

## Scope
### Must have
- `_SessionContext` dataclass on `MCPManager` with per-session `toolset_cache`, `connection_pool`, `snapshot`, `acp_connection_ids`, and `_cleanup_lock`
- `MCPManager.get_or_create_session()`, `update_session_snapshot()`, `add_acp_transport()`, `cleanup_session()` methods
- `as_capability(session_id: str | None = None)` simplified API replacing `as_capability(snapshot=..., session_pool=...)`
- `_session_connections: dict[str, set[tuple[str, int]]]` reverse index on `AcpMcpConnectionManager` with `register_session_connection()` and `cleanup_session()` methods
- `has_active_sessions()` on `AcpMcpConnection`
- `cleanup_session()` wired into `ACPSession.close()` and `SessionController._close_session_run_turn()`
- `_session_id` stored on `Agent` and propagated to `as_capability()` call in `get_agentlet()`
- `resume_session()` close-then-recreate via two-layer cleanup (SessionController.close_session + ACPSession.close)
- `on_disconnect` callback in `_handle_websocket_client()` + `close_all_sessions_for_connection()` on `ACPSessionManager`
- All existing bug-documenting tests flipped to fix-verifying tests
- Full unit + integration test coverage for all new code paths

### Must NOT have (guardrails, anti-slop, scope boundaries)
- NO changes to `MessageNode` base class (`messagenode.py`)
- NO changes to `AgentPool` registry (`pool.py`)
- NO removal of per-agent MCPManager (Phase 2)
- NO config API changes — no new YAML fields, no new public config models
- NO changes to `MCPResourceProvider` model or `ResourceProvider` base class
- NO Phase 2 features (allow/block lists, pool-level MCP consolidation, skill MCP dual path consolidation)
- NO ACP v2 protocol migration
- NO `getattr`/`hasattr` — full type safety with match/case or isinstance
- NO TODOs left in code

### Metis gap resolutions (folded into todos below)
1. **GAP-1 (Critical)**: `AcpMcpConnection.register_session()` currently returns `SessionStreamPair`, NOT an int key. **Resolution**: Modify `register_session()` to return `tuple[SessionStreamPair, int]` — the pair AND the internal `_next_session_key`. Callers store the int key in `acp_connection_ids`. Affected: T1, T3, T6, T7, T8.
2. **GAP-3 (Critical)**: `AgentSideConnection` has no `connection_id` for `_connection_sessions` lookup. **Resolution**: Generate a UUID4 string for each WebSocket connection at accept time, store it on the `AgentSideConnection` instance (add a `connection_id: str` attribute set in `_handle_websocket_client()`), and use that as the key in `_connection_sessions`. The `on_disconnect` callback receives the `AgentSideConnection` and reads `.connection_id`. Affected: T24, T25, T26.
3. **GAP-4 (High)**: `_session_id` storage location undefined. **Resolution**: Use `run_ctx.session_id` in `get_agentlet()` (already available via `AgentRunContext`) instead of storing `self._session_id` on Agent. This avoids duplicating state. If `run_ctx` is None or `run_ctx.session_id` is None, fall back to `session_id=None` (global-only capabilities). Affected: T12, T16.
4. **GAP-5 (High)**: `connect_acp_mcp_server()` signature must gain `session_id: str` parameter. **Resolution**: Change signature to `connect_acp_mcp_server(self, server: AcpMcpServer, session_id: str) -> str`. Update call site at `session.py:478`. Affected: T8.
5. **GAP-7 (Medium)**: `_make_capability()` is a closure inside `as_capability()` and can't access per-session cache. **Resolution**: Pass `toolset_cache: dict[str, Any]` as a parameter to `_make_capability()` instead of accessing `self._toolset_cache` directly. When `cache=True`, pass `self._toolset_cache`; when `cache=False`, pass `ctx.toolset_cache`. Affected: T10.
6. **GAP-11 (High)**: Race condition — `cleanup_session()` can pop context while `as_capability()` reads it. **Resolution**: `as_capability()` acquires `ctx._cleanup_lock` before reading the session context (shared lock via `asyncio.Lock` — but `asyncio.Lock` is exclusive, not shared). Alternative: `as_capability()` catches `KeyError` on `_session_contexts` lookup and falls back to global-only. **Chosen**: Catch `KeyError` fallback approach — simpler, no lock contention. Affected: T10.
7. **GAP-12 (High)**: `AcpMcpConnectionManager.cleanup_session()` has no lock. **Resolution**: Add `_cleanup_lock: asyncio.Lock` to `AcpMcpConnectionManager.__init__`. `cleanup_session()` acquires it. Double-cleanup from `MCPManager.cleanup_session()` + direct call is idempotent (pop from `_session_connections` returns None on second call). Affected: T7.
8. **GAP-15 (High)**: Task 7.5 manual test violates zero-user-intervention. **Resolution**: Replace with automated integration test using mock ACP client. Test creates a mock WebSocket connection, sends ACP messages, simulates disconnect, reconnects, and verifies MCP tools work. Affected: T33.
9. **GAP-14 (Medium)**: Resume after close — closed session re-opening. **Resolution**: `SessionController.close_session()` marks session as closed in store (line 830-832) but `_get_or_create_session_locked()` creates fresh `SessionState` if not in `_sessions` dict (which was popped at line 933). The store's "closed" flag is informational — a new `SessionState` is created. This is validated by the existing resume flow. No change needed, but T20 acceptance criteria must verify this explicitly.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after (implementation first, tests in same todo) + pytest
- Evidence: .omo/evidence/task-<N>-fix-mcp-session-lifecycle.<ext>
- Lint: `uv run ruff check src/` — zero errors
- Types: `uv run --no-group docs mypy src/` — zero errors on changed files
- Tests: `uv run pytest tests/mcp_server/ tests/agentpool_server/acp_server/ -v`
- Unit marker: `uv run pytest -m unit`

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means under-splitting.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 (_SessionContext + _session_contexts) | — | T2, T3, T4, T5, T10, T15 | T6, T7, T8, T9 |
| T2 (get_or_create_session + update_session_snapshot) | T1 | T4, T10, T16 | T3, T6, T7, T8, T9 |
| T3 (add_acp_transport) | T1 | T5, T8 | T2, T6, T7, T8, T9 |
| T4 (cleanup_session with lock) | T1, T2 | T15, T17, T19, T22 | T5, T6, T7, T8, T9 |
| T5 (session context unit tests) | T1-T4 | — | T6, T7, T8, T9 |
| T6 (_session_connections + register_session_connection) | — | T7, T8, T15 | T1-T5, T9 |
| T7 (AcpMcpConnectionManager.cleanup_session + has_active_sessions) | T6 | T15, T17, T19, T22 | T1-T5, T8, T9 |
| T8 (Wire register_session_connection into connect_acp_mcp_server) | T3, T6 | T15 | T1-T5, T7, T9 |
| T9 (ACP session connection unit tests) | T6, T7, T8 | — | T1-T5 |
| T10 (as_capability new signature + _make_capability + _process_snapshot) | T1, T2 | T11, T12, T13, T14 | — |
| T11 (session-scoped vs global routing) | T10 | T12 | T13, T14 |
| T12 (Update get_agentlet call site) | T10, T11 | T15, T16 | T13, T14 |
| T13 (Update test_mcpmanager_caching.py) | T10 | — | T14 |
| T14 (Flip test_stale_mcp_connection.py to fix-verifying) | T10 | — | T13 |
| T15 (Wire cleanup into ACPSession.close + SessionController) | T4, T7, T8, T12 | T17, T18, T19, T20, T22 | T16 |
| T16 (get_or_create_session_agent + _session_id on Agent) | T2, T12 | T17, T18 | T15 |
| T17 (Integration: create→run→close→verify empty) | T15, T16 | — | T18, T19 |
| T18 (Integration: close→recreate same ID→fresh resources) | T15, T16 | — | T17, T19 |
| T19 (Test: concurrent cleanup_session calls) | T4, T15 | T22 | T17, T18 |
| T20 (Fix resume_session close-then-recreate) | T15, T16 | T21, T22, T23 | — |
| T21 (Test: resume→old closed→fresh MCP) | T20 | — | T22, T23 |
| T22 (Test: resume after WebSocket reconnect) | T15, T19, T20 | — | T21, T23 |
| T23 (Test: resume with active run→RunHandle cancelled) | T20 | — | T21, T22 |
| T24 (on_disconnect param + ConnectionClosed hook) | T15 | T25, T26, T27 | — |
| T25 (_connection_sessions + close_all_sessions_for_connection) | T15, T24 | T26, T28 | — |
| T26 (Wire on_disconnect in server setup) | T24, T25 | T27, T28 | — |
| T27 (Tests: disconnect closes + other connections unaffected) | T25, T26 | — | T28 |
| T28 (Test: disconnect during active run→RunHandle cancelled) | T25, T26 | — | T27 |
| T29-T33 (End-to-end verification) | ALL | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

### Wave 1: P1a — MCPManager Session Tracking (foundation)

- [x] 1. Add `_SessionContext` dataclass and `_session_contexts` dict to MCPManager
  What to do / Must NOT do: Create a `@dataclass` named `_SessionContext` with fields: `connection_pool: SessionConnectionPool`, `toolset_cache: dict[str, Any]`, `snapshot: McpConfigSnapshot | None`, `acp_connection_ids: list[tuple[str, int]]`, `_cleanup_lock: asyncio.Lock`. Add `_session_contexts: dict[str, _SessionContext]` to `MCPManager.__init__` (after `_toolset_cache` at line 147). Import `SessionConnectionPool` from `agentpool.mcp_server.session_pool`, `McpConfigSnapshot` from `agentpool.mcp_server.config_snapshot`. **Metis GAP-1 resolution**: `AcpMcpConnection.register_session()` currently returns `SessionStreamPair` only — it must be modified (in T7) to return `tuple[SessionStreamPair, int]` so the int key can be stored in `acp_connection_ids`. Must NOT remove or rename existing `_toolset_cache` (D3: retained for global configs).
  Parallelization: Wave 1 | Blocked by: — | Blocks: T2, T3, T4, T5
  References: `src/agentpool/mcp_server/manager.py:115` (MCPManager class), `manager.py:123-147` (__init__ fields), `manager.py:147` (_toolset_cache line), `src/agentpool/mcp_server/session_pool.py` (SessionConnectionPool class with `cleanup(timeout=5.0)` method and `copy_pre_created_transports()`), `src/agentpool/mcp_server/config_snapshot.py` (McpConfigSnapshot frozen dataclass with `pool_configs`, `agent_configs`, `session_configs`, `skill_configs` fields and `global_configs`/`session_scoped_configs` properties)
  Acceptance criteria: `uv run python -c "from agentpool.mcp_server.manager import MCPManager, _SessionContext; print(_SessionContext.__dataclass_fields__.keys())"` prints fields including `connection_pool`, `toolset_cache`, `snapshot`, `acp_connection_ids`, `_cleanup_lock`. `uv run ruff check src/agentpool/mcp_server/manager.py` passes.
  QA scenarios: (happy) `uv run python -c "import asyncio; from agentpool.mcp_server.manager import _SessionContext; ctx = _SessionContext(connection_pool=None, toolset_cache={}, snapshot=None, acp_connection_ids=[], _cleanup_lock=asyncio.Lock()); print(ctx)"` runs without error. (failure) Verify `MCPManager()` has `_session_contexts` attribute initialized as empty dict. Evidence: `.omo/evidence/task-1-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(mcp): add _SessionContext dataclass and _session_contexts to MCPManager

- [x] 2. Implement `get_or_create_session()` and `update_session_snapshot()` on MCPManager
  What to do / Must NOT do: Add `get_or_create_session(self, session_id: str) -> _SessionContext` — if `session_id` not in `_session_contexts`, create new `_SessionContext` with fresh `SessionConnectionPool()`, empty `toolset_cache`, `snapshot=None`, empty `acp_connection_ids`, new `asyncio.Lock()`. Return existing if present. Add `update_session_snapshot(self, session_id: str, snapshot: McpConfigSnapshot) -> None` — calls `get_or_create_session(session_id)` then sets `.snapshot = snapshot`. Must NOT raise if session already exists (idempotent).
  Parallelization: Wave 1 | Blocked by: T1 | Blocks: T4, T10, T16
  References: `src/agentpool/mcp_server/manager.py:115` (class), `session_pool.py` (SessionConnectionPool constructor — check if it takes args), `config_snapshot.py` (McpConfigSnapshot type)
  Acceptance criteria: `uv run pytest tests/mcp_server/test_session_lifecycle.py -k "test_get_or_create_session" -v` passes (test added in T5). `uv run ruff check src/agentpool/mcp_server/manager.py` passes.
  QA scenarios: (happy) Create manager, call `get_or_create_session("s1")` twice, verify same object returned. (failure) Call `update_session_snapshot` on non-existent session, verify it creates the context. Evidence: `.omo/evidence/task-2-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(mcp): implement get_or_create_session and update_session_snapshot

- [x] 3. Implement `add_acp_transport()` on MCPManager
  What to do / Must NOT do: Add `add_acp_transport(self, session_id: str, client_id: str, transport: ClientTransport, connection_id: str, session_key: int) -> None` — gets session context via `get_or_create_session(session_id)`, adds transport to `ctx.connection_pool` (check SessionConnectionPool's API for adding transports — it uses `(client_id, skill_name)` keys), appends `(connection_id, session_key)` to `ctx.acp_connection_ids`. Must NOT create duplicate entries if called twice with same args.
  Parallelization: Wave 1 | Blocked by: T1 | Blocks: T5, T8
  References: `src/agentpool/mcp_server/manager.py:115` (class), `session_pool.py` (SessionConnectionPool — check how transports are stored, keyed by `(client_id, skill_name)`), `src/acp/client/protocol.py` (ClientTransport type)
  Acceptance criteria: `uv run pytest tests/mcp_server/test_session_lifecycle.py -k "test_add_acp_transport" -v` passes (test added in T5). `uv run ruff check src/agentpool/mcp_server/manager.py` passes.
  QA scenarios: (happy) Add transport, verify it appears in `ctx.connection_pool` and `ctx.acp_connection_ids`. (failure) Add transport to non-existent session, verify session context is created. Evidence: `.omo/evidence/task-3-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(mcp): implement add_acp_transport for session-scoped ACP tracking

- [x] 4. Implement `cleanup_session()` on MCPManager with per-session lock
  What to do / Must NOT do: Add `async cleanup_session(self, session_id: str) -> None`. Acquire `ctx._cleanup_lock` (from `get_or_create_session`). In try block: (1) clear `ctx.toolset_cache` dict, (2) call `await ctx.connection_pool.cleanup()` (with try/except to log but not re-raise), (3) delegate to ACP cleanup — if `self._acp_mcp_manager` is not None, call `await self._acp_mcp_manager.cleanup_session(session_id)` (with try/except to log). In finally block: always `self._session_contexts.pop(session_id, None)`. Must NOT re-raise exceptions from intermediate steps. Must NOT skip the pop in finally. D8: the lock makes concurrent calls idempotent — second caller blocks on lock, then finds session already popped.
  Parallelization: Wave 1 | Blocked by: T1, T2 | Blocks: T15, T17, T19, T22
  References: `src/agentpool/mcp_server/manager.py:115` (class), `manager.py:275` (disconnect_all — pattern for clearing toolset cache), `manager.py:438` (cleanup — pattern for exit_stack closing), `session_pool.py` (SessionConnectionPool.cleanup(timeout=5.0) method), `src/agentpool_server/acp_server/acp_mcp_manager.py:253` (AcpMcpConnectionManager — will have cleanup_session() after T7). Note: MCPManager may need an `_acp_mcp_manager: AcpMcpConnectionManager | None = None` field to delegate ACP cleanup — check if it already has a reference, if not add one.
  Acceptance criteria: `uv run pytest tests/mcp_server/test_session_lifecycle.py -k "test_cleanup_session" -v` passes (test added in T5). `uv run pytest tests/mcp_server/test_session_lifecycle.py -k "test_concurrent_cleanup" -v` passes. `uv run ruff check src/agentpool/mcp_server/manager.py` passes.
  QA scenarios: (happy) Create session, add resources, call `cleanup_session()`, verify `_session_contexts` is empty. (failure) Call `cleanup_session()` twice concurrently (asyncio.gather), verify no errors and second call is no-op. Evidence: `.omo/evidence/task-4-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(mcp): implement cleanup_session with per-session asyncio.Lock

- [x] 5. Unit tests for MCPManager session context lifecycle
  What to do / Must NOT do: Create `tests/mcp_server/test_session_lifecycle.py` with tests: (1) `test_get_or_create_session_creates_and_returns_same` — two calls return same object, (2) `test_get_or_create_session_creates_fresh_for_different_ids` — different session_ids get different contexts, (3) `test_update_session_snapshot_stores_snapshot` — snapshot is stored correctly, (4) `test_add_acp_transport_stores_transport_and_ids` — transport and (connection_id, session_key) are stored, (5) `test_cleanup_session_clears_all_resources` — after cleanup, `_session_contexts` is empty, (6) `test_cleanup_session_is_idempotent` — double-call is no-op, (7) `test_concurrent_cleanup_session_no_error` — asyncio.gather of two cleanup calls. Use `@pytest.mark.unit`. Use `pytest.fixture` for MCPManager instance. Must NOT use `getattr`/`hasattr` — use direct attribute access with type annotations.
  Parallelization: Wave 1 | Blocked by: T1-T4 | Blocks: —
  References: `tests/mcp_server/test_mcpmanager_caching.py` (existing test patterns), `tests/mcp_server/test_session_pool.py` (SessionConnectionPool test patterns), `tests/conftest.py` (fixtures, TestModel, observability disabled)
  Acceptance criteria: `uv run pytest tests/mcp_server/test_session_lifecycle.py -v` — all 7 tests pass. `uv run pytest -m unit tests/mcp_server/test_session_lifecycle.py -v` — all pass with unit marker.
  QA scenarios: (happy) All 7 tests pass. (failure) Intentionally break cleanup (remove pop from finally), verify test 6 and 7 fail. Evidence: `.omo/evidence/task-5-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(mcp): add session context lifecycle unit tests

### Wave 2: P1c — AcpMcpConnectionManager Session Tracking (parallel with Wave 1)

- [x] 6. Add `_session_connections` dict and `register_session_connection()` to AcpMcpConnectionManager
  What to do / Must NOT do: Add `_session_connections: dict[str, set[tuple[str, int]]]` to `AcpMcpConnectionManager.__init__` (after `_connections` at line 259). Maps `session_id` → set of `(connection_id, session_key)` tuples. Add `register_session_connection(self, session_id: str, connection_id: str, session_key: int) -> None` — adds `(connection_id, session_key)` to the session's set, creating the set if missing. Must NOT modify `AcpMcpConnection._session_streams` (D5: reverse index at manager level, not changing int keys).
  Parallelization: Wave 2 | Blocked by: — | Blocks: T7, T8, T15
  References: `src/agentpool_server/acp_server/acp_mcp_manager.py:253` (AcpMcpConnectionManager class), `acp_mcp_manager.py:259` (_connections dict), `acp_mcp_manager.py:34` (AcpMcpConnection class), `acp_mcp_manager.py:50` (_session_streams dict with int keys), `acp_mcp_manager.py:52` (_next_session_key int), `acp_mcp_manager.py:78` (register_session returns SessionStreamPair)
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/test_acp_mcp_session_cleanup.py -k "test_register_session_connection" -v` passes (test added in T9). `uv run ruff check src/agentpool_server/acp_server/acp_mcp_manager.py` passes.
  QA scenarios: (happy) Register a connection, verify it appears in `_session_connections`. (failure) Register same tuple twice, verify set deduplicates. Evidence: `.omo/evidence/task-6-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(acp): add session connection tracking to AcpMcpConnectionManager

- [x] 7. Implement `cleanup_session()` and `has_active_sessions()` on AcpMcpConnectionManager/AcpMcpConnection
  What to do / Must NOT do: Add `has_active_sessions(self) -> bool` to `AcpMcpConnection` (line 34) — returns `len(self._session_streams) > 0`. **Metis GAP-1 resolution**: Modify `register_session()` at `acp_mcp_manager.py:78` to return `tuple[SessionStreamPair, int]` instead of just `SessionStreamPair` — return `(pair, key)` where `key` is the internal `_next_session_key`. Add `async cleanup_session(self, session_id: str) -> None` to `AcpMcpConnectionManager` (line 253) — **Metis GAP-12 resolution**: acquire `_cleanup_lock` (new `asyncio.Lock` added to `__init__`) before proceeding. Pop `session_id` from `_session_connections`, for each `(connection_id, session_key)` tuple: look up `AcpMcpConnection` via `self._connections[connection_id]`, look up `SessionStreamPair` via `conn._session_streams[session_key]` (note: _session_streams uses int keys, session_key is int from the modified `register_session()`), call `conn.unregister_session(pair)`, after processing all tuples for a connection check `conn.has_active_sessions()` — if False, optionally remove the connection (check existing `remove_connection()` logic at line ~290 for cleanup pattern). Must NOT change `_session_streams` key type from int to str (D5).
  Parallelization: Wave 2 | Blocked by: T6 | Blocks: T15, T17, T19, T22
  References: `src/agentpool_server/acp_server/acp_mcp_manager.py:253` (class), `acp_mcp_manager.py:67` (close method), `acp_mcp_manager.py:78` (register_session), `acp_mcp_manager.py:98` (unregister_session takes SessionStreamPair), `acp_mcp_manager.py:50` (_session_streams dict), `acp_mcp_manager.py:224` (broadcast_to_sessions — pattern for iterating sessions)
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/test_acp_mcp_session_cleanup.py -k "test_cleanup_session" -v` passes (test added in T9). `uv run ruff check src/agentpool_server/acp_server/acp_mcp_manager.py` passes.
  QA scenarios: (happy) Register 2 sessions on same connection, cleanup one, verify connection still has 1 active session. (failure) Cleanup all sessions, verify connection is removed or has `has_active_sessions() == False`. Evidence: `.omo/evidence/task-7-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(acp): implement cleanup_session and has_active_sessions

- [x] 8. Wire `register_session_connection()` into `connect_acp_mcp_server()`
  What to do / Must NOT do: **Metis GAP-5 resolution**: Change `connect_acp_mcp_server()` signature at `acp_agent.py:823` from `connect_acp_mcp_server(self, server: AcpMcpServer) -> str` to `connect_acp_mcp_server(self, server: AcpMcpServer, session_id: str) -> str`. Update call site at `session.py:478` to pass `self.session_id`. After calling `AcpMcpConnection.register_session()` (which now returns `tuple[SessionStreamPair, int]` per T7), extract the `session_key` (int) from the return. Call `self._mcp_manager.register_session_connection(session_id, connection_id, session_key)` and `agent.mcp.add_acp_transport(session_id, client_id, transport, connection_id, session_key)` (or equivalent MCPManager method from T3). Must NOT change the `SessionStreamPair` return type.
  Parallelization: Wave 2 | Blocked by: T3, T6 | Blocks: T15
  References: `src/agentpool_server/acp_server/acp_agent.py:823` (connect_acp_mcp_server), `acp_agent.py:846` (disconnect_acp_mcp_server), `acp_agent.py:238` (_mcp_manager field), `acp_agent.py:263` (_mcp_manager init), `acp_mcp_manager.py:78` (register_session returns SessionStreamPair — check if key is stored on the pair or accessible), `src/agentpool_server/acp_server/session.py:165` (self.agent is BaseAgent)
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/test_acp_mcp_session_cleanup.py -k "test_connect_registers_session" -v` passes (test added in T9). `uv run ruff check src/agentpool_server/acp_server/acp_agent.py` passes.
  QA scenarios: (happy) Connect ACP MCP server, verify `register_session_connection()` was called with correct session_id and connection_id. (failure) Connect without session_id, verify graceful handling (no crash). Evidence: `.omo/evidence/task-8-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(acp): wire register_session_connection into connect_acp_mcp_server

- [x] 9. Unit tests for AcpMcpConnectionManager session connection tracking
  What to do / Must NOT do: Create `tests/agentpool_server/acp_server/test_acp_mcp_session_cleanup.py` with tests: (1) `test_register_session_connection_adds_to_set`, (2) `test_register_deduplicates_same_tuple`, (3) `test_cleanup_session_unregisters_streams`, (4) `test_cleanup_preserves_shared_connection`, (5) `test_cleanup_removes_connection_with_no_sessions`, (6) `test_has_active_sessions_true_when_streams_exist`, (7) `test_has_active_sessions_false_when_empty`, (8) `test_connect_acp_mcp_server_registers_session` (integration with T8). Use `@pytest.mark.unit` for 1-7, `@pytest.mark.integration` for 8. Must NOT use `getattr`/`hasattr`.
  Parallelization: Wave 2 | Blocked by: T6, T7, T8 | Blocks: —
  References: `tests/agentpool_server/acp_server/test_acp_mcp_manager.py` (existing test patterns), `tests/agentpool_server/acp_server/test_acp_mcp_agent_integration.py` (integration test patterns)
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/test_acp_mcp_session_cleanup.py -v` — all 8 tests pass. `uv run pytest -m unit tests/agentpool_server/acp_server/test_acp_mcp_session_cleanup.py -v` — 7 unit tests pass.
  QA scenarios: (happy) All 8 tests pass. (failure) Remove `has_active_sessions()` check from cleanup, verify test 4 (shared connection preservation) fails. Evidence: `.omo/evidence/task-9-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(acp): add session connection cleanup unit tests

### Wave 3: P1b — as_capability Session-Aware API (depends on Wave 1)

- [x] 10. Change `as_capability()` signature and modify `_make_capability()` and `_process_snapshot()`
  What to do / Must NOT do: Change `as_capability(self, snapshot: McpConfigSnapshot | None = None, session_pool: SessionConnectionPool | None = None)` at `manager.py:301` to `as_capability(self, session_id: str | None = None) -> AggregatingCapability` (or whatever the return type is — check current signature). When `session_id` is provided: look up `_SessionContext` via `get_or_create_session(session_id)`, use its `snapshot`, `connection_pool`, and `toolset_cache`. **Metis GAP-11 resolution**: Wrap the `_session_contexts` lookup in try/except `KeyError` — if the session context was popped by concurrent `cleanup_session()`, fall back to global-only capabilities (log a warning). This avoids the race condition without lock contention. When `session_id` is None: process only global configs from `self.servers` (backward compat, use `_toolset_cache`). **Metis GAP-7 resolution**: Modify `_make_capability(self, server, transport)` at line 374 to accept `toolset_cache: dict[str, Any]` parameter (instead of accessing `self._toolset_cache` directly) — when processing global configs, pass `self._toolset_cache`; when processing session-scoped configs, pass `ctx.toolset_cache`. Modify `_process_snapshot(self, snap)` at line 396 to pass the correct `toolset_cache` for session-scoped vs global configs. Must NOT remove `_toolset_cache` (D3: retained for global configs). Must NOT break `session_id=None` backward compat path.
  Parallelization: Wave 3 | Blocked by: T1, T2 | Blocks: T11, T12, T13, T14
  References: `src/agentpool/mcp_server/manager.py:301` (as_capability current signature), `manager.py:374` (_make_capability), `manager.py:396` (_process_snapshot), `manager.py:147` (_toolset_cache), `config_snapshot.py` (McpConfigSnapshot.global_configs and .session_scoped_configs properties), `session_pool.py` (SessionConnectionPool)
  Acceptance criteria: `uv run pytest tests/mcp_server/test_manager_capability.py -v` — all existing 19 tests pass (may need updates in T13). `uv run ruff check src/agentpool/mcp_server/manager.py` passes. `uv run --no-group docs mypy src/agentpool/mcp_server/manager.py` passes.
  QA scenarios: (happy) Call `as_capability(session_id="s1")` with a session context that has a snapshot, verify session-scoped configs use per-session cache. (failure) Call `as_capability(session_id=None)`, verify only global configs are processed. Evidence: `.omo/evidence/task-10-fix-mcp-session-lifecycle.txt`
  Commit: Y | refactor(mcp): change as_capability to session_id-based API

- [x] 11. Implement session-scoped vs global config routing in `as_capability()`
  What to do / Must NOT do: Inside `as_capability(session_id)`: if `session_id` is not None and `ctx.snapshot` is not None, call `_process_snapshot(ctx.snapshot, cache=False, toolset_cache=ctx.toolset_cache, connection_pool=ctx.connection_pool)` for session-scoped configs and `_process_snapshot(ctx.snapshot, cache=True)` for global configs. If `session_id` is None, process `self.servers` global configs with `_toolset_cache` as before. Must NOT mix session-scoped toolsets into `_toolset_cache`.
  Parallelization: Wave 3 | Blocked by: T10 | Blocks: T12
  References: `src/agentpool/mcp_server/manager.py:301` (as_capability), `manager.py:396` (_process_snapshot), `config_snapshot.py` (global_configs property returns pool+agent configs, session_scoped_configs returns session+skill configs)
  Acceptance criteria: `uv run pytest tests/mcp_server/test_manager_capability.py -k "session" -v` passes (tests updated in T13). `uv run ruff check src/agentpool/mcp_server/manager.py` passes.
  QA scenarios: (happy) Session-scoped config produces toolset in `ctx.toolset_cache`, NOT in `_toolset_cache`. (failure) Global config produces toolset in `_toolset_cache`, NOT in `ctx.toolset_cache`. Evidence: `.omo/evidence/task-11-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(mcp): route session-scoped configs to per-session cache

- [x] 12. Update `get_agentlet()` call site in `agent.py`
  What to do / Must NOT do: At `agent.py:901-903`, change `mcp_capabilities = await self.mcp.as_capability(snapshot=self._mcp_snapshot, session_pool=self._session_connection_pool)` to `mcp_capabilities = await self.mcp.as_capability(session_id=run_ctx.session_id if run_ctx else None)`. **Metis GAP-4 resolution**: Use `run_ctx.session_id` (already available via `AgentRunContext` parameter in `get_agentlet()`) instead of storing `self._session_id` on Agent. This avoids duplicating state. If `run_ctx` is None or `run_ctx.session_id` is None, pass `session_id=None` (global-only capabilities). Remove the direct setting of `self._mcp_snapshot` and `self._session_connection_pool` on the agent if they are now managed through `MCPManager.get_or_create_session()` and `update_session_snapshot()`. However, keep `self._mcp_snapshot` and `self._session_connection_pool` fields for backward compat if other code reads them — check all references. Must NOT remove `_mcp_snapshot` or `_session_connection_pool` field declarations if other code references them.
  Parallelization: Wave 3 | Blocked by: T10, T11 | Blocks: T15, T16
  References: `src/agentpool/agents/native_agent/agent.py:901-903` (as_capability call), `agent.py:333-334` (_mcp_snapshot and _session_connection_pool declarations), `src/agentpool/orchestrator/session_controller.py:504-505` (child agent sets _mcp_snapshot and _session_connection_pool), `session_controller.py:586-587` (main agent sets _mcp_snapshot and _session_connection_pool)
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/ -v` — existing ACP tests pass. `uv run ruff check src/agentpool/agents/native_agent/agent.py` passes. `uv run --no-group docs mypy src/agentpool/agents/native_agent/agent.py` passes.
  QA scenarios: (happy) Agentlet creation calls `as_capability(session_id=...)` with correct session_id. (failure) Call `as_capability(session_id=None)`, verify it returns global-only capabilities. Evidence: `.omo/evidence/task-12-fix-mcp-session-lifecycle.txt`
  Commit: Y | refactor(agent): update get_agentlet to use as_capability(session_id)

- [x] 13. Update existing tests in `test_mcpmanager_caching.py`
  What to do / Must NOT do: Update all 6 tests in `tests/mcp_server/test_mcpmanager_caching.py` to use new `as_capability(session_id=...)` API instead of `as_capability(snapshot=..., session_pool=...)`. Tests: toolset cache sharing, client_id keying, aggregating provider, no dedup hack, engineer/librarian scoping. For tests that verify cache sharing behavior, update to test per-session cache isolation instead. Must NOT delete tests — update them to verify the new behavior.
  Parallelization: Wave 3 | Blocked by: T10 | Blocks: —
  References: `tests/mcp_server/test_mcpmanager_caching.py` (6 existing tests), `tests/mcp_server/test_manager_capability.py` (19 existing tests — may also need updates)
  Acceptance criteria: `uv run pytest tests/mcp_server/test_mcpmanager_caching.py -v` — all 6 updated tests pass. `uv run pytest tests/mcp_server/test_manager_capability.py -v` — all 19 tests pass.
  QA scenarios: (happy) All tests pass with new API. (failure) Revert API change, verify tests fail with old signature. Evidence: `.omo/evidence/task-13-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(mcp): update caching tests for session_id API

- [x] 14. Flip `test_stale_mcp_connection.py` tests from bug-documenting to fix-verifying
  What to do / Must NOT do: Update all 5 tests in `tests/mcp_server/test_stale_mcp_connection.py`: (1) `test_session_resume_returns_stale_toolset_from_cache` → rename to `test_session_resume_returns_fresh_toolset` and assert session 2 gets a DIFFERENT toolset object, (2) `test_acp_client_id_is_deterministic` → keep as-is (still valid), (3) `test_session_pool_provides_fresh_transport` → keep as-is (still valid), (4) `test_multiple_acp_servers_all_go_stale` → rename to `test_multiple_acp_servers_get_fresh_toolsets` and assert freshness, (5) `test_disconnect_all_clears_cache_but_not_called_on_resume` → rename to `test_cleanup_session_clears_per_session_cache` and verify cleanup works. Add `try/finally` or `@pytest.fixture` teardown for resource cleanup — current tests skip cleanup on assertion failure. Must NOT keep assertions that verify the bug exists.
  Parallelization: Wave 3 | Blocked by: T10 | Blocks: —
  References: `tests/mcp_server/test_stale_mcp_connection.py` (5 existing tests documenting the bug)
  Acceptance criteria: `uv run pytest tests/mcp_server/test_stale_mcp_connection.py -v` — all 5 updated tests pass. Tests verify the FIX, not the bug.
  QA scenarios: (happy) Session 2 gets fresh toolset after session 1 is cleaned up. (failure) Remove cleanup_session call, verify test 1 and 4 fail (stale toolset returned). Evidence: `.omo/evidence/task-14-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(mcp): flip stale connection tests to verify fix

### Wave 4: P1d — Wire cleanup_session into Close Paths (depends on Waves 1+2)

- [x] 15. Wire `cleanup_session()` into `ACPSession.close()` and `SessionController._close_session_run_turn()`
  What to do / Must NOT do: (1) In `session.py:795-823` (`ACPSession.close()`), add `await self.agent.mcp.cleanup_session(self.session_id)` BEFORE existing env/signal/prompt cleanup (before `acp_env.__aexit__()`). Check that `self.agent` has `.mcp` attribute and `.session_id` is accessible — `self.session_id` should be on ACPSession (check `session.py` for the field name, it may be `self._session_id` or similar). (2) In `session_controller.py:835-949` (`_close_session_run_turn()`), add `await agent.mcp.cleanup_session(session_id)` BEFORE `agent.__aexit__()` call (before line 941). Must NOT call cleanup_session AFTER `agent.__aexit__()` (agent context may be torn down). Must NOT skip cleanup if `is_per_session_agent=False` — the shared MCPManager still has session-scoped contexts that need cleanup.
  Parallelization: Wave 4 | Blocked by: T4, T7, T8, T12 | Blocks: T17, T18, T19, T20, T22
  References: `src/agentpool_server/acp_server/session.py:795-823` (ACPSession.close), `session.py:165` (self.agent is BaseAgent), `src/agentpool/orchestrator/session_controller.py:835-949` (_close_session_run_turn), `session_controller.py:941-947` (agent.__aexit__ call with is_per_session_agent check)
  Acceptance criteria: `uv run pytest tests/mcp_server/test_session_lifecycle.py -v` passes. `uv run pytest tests/agentpool_server/acp_server/ -v` — existing tests pass. `uv run ruff check src/agentpool_server/acp_server/session.py src/agentpool/orchestrator/session_controller.py` passes.
  QA scenarios: (happy) Close session, verify `cleanup_session()` was called and `_session_contexts` is empty. (failure) Close session with active run, verify RunHandle is cancelled with timeout before cleanup. Evidence: `.omo/evidence/task-15-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(session): wire cleanup_session into ACPSession.close and SessionController

- [x] 16. Wire `get_or_create_session()` in `get_or_create_session_agent()` and update MCP snapshot setup
  What to do / Must NOT do: **Metis GAP-4 resolution**: Do NOT add `self._session_id` to Agent — `get_agentlet()` uses `run_ctx.session_id` instead (see T12). (1) In `session_controller.py:397-672` (`get_or_create_session_agent()`), when creating a new agent: call `agent.mcp.get_or_create_session(session_id)` to create the session context, and call `agent.mcp.update_session_snapshot(session_id, snapshot)` if a snapshot is available (replace the direct `agent._mcp_snapshot = ...` and `agent._session_connection_pool = ...` setting at lines 504-505 and 586-587). **Metis GAP-6 resolution**: This must be done on ALL 3 agent creation paths: (a) child session (line 444-546), (b) main native (line 548-601), (c) non-native (line 603-657). For child sessions, the MCPManager is the parent's/pool's shared one — calling `get_or_create_session` on it is correct (session_ids are unique). Must NOT remove the `_mcp_snapshot` and `_session_connection_pool` field declarations if other code reads them — but do redirect the setting through MCPManager.
  Parallelization: Wave 4 | Blocked by: T2, T12 | Blocks: T17, T18
  References: `src/agentpool/agents/native_agent/agent.py:333-334` (field declarations), `src/agentpool/orchestrator/session_controller.py:397-672` (get_or_create_session_agent), `session_controller.py:504-505` (child agent MCP setup), `session_controller.py:586-587` (main agent MCP setup)
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/ -v` — existing tests pass. `uv run ruff check src/agentpool/agents/native_agent/agent.py src/agentpool/orchestrator/session_controller.py` passes. `uv run --no-group docs mypy src/agentpool/agents/native_agent/agent.py` passes.
  QA scenarios: (happy) Create session agent, verify `_session_id` is set and `_session_contexts` has the session. (failure) Create agent without session_id, verify `as_capability(session_id=None)` works. Evidence: `.omo/evidence/task-16-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(agent): add _session_id and wire get_or_create_session in SessionController

- [ ] 17. Integration test: create session → run turn → close → verify empty contexts
  What to do / Must NOT do: Create integration test in `tests/mcp_server/test_session_lifecycle.py` (or a new `tests/integration/test_session_cleanup.py`): create an AgentPool with a native agent that has MCP servers, create a session, run a turn (use TestModel), close the session, verify `agent.mcp._session_contexts` is empty and `agent.mcp._toolset_cache` has no session-scoped entries. Use `@pytest.mark.integration`. Must NOT use real model calls — use TestModel from pydantic-ai.
  Parallelization: Wave 4 | Blocked by: T15, T16 | Blocks: —
  References: `tests/conftest.py` (TestModel setup, observability disabled), `tests/mcp_server/test_mcp_provider_lifecycle.py` (integration test patterns)
  Acceptance criteria: `uv run pytest tests/mcp_server/test_session_lifecycle.py -k "test_integration_create_run_close" -v` passes.
  QA scenarios: (happy) After close, `_session_contexts` is empty. (failure) Remove cleanup call from close path, verify test fails (context still present). Evidence: `.omo/evidence/task-17-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(mcp): integration test for session create→run→close lifecycle

- [ ] 18. Integration test: close → recreate same ID → verify fresh MCP resources
  What to do / Must NOT do: Create integration test: create session "s1", run turn, close session, create new session "s1" (same ID), verify the new session has fresh MCP resources (different toolset objects, fresh connection pool). Use `@pytest.mark.integration`. Must NOT reuse the old session object.
  Parallelization: Wave 4 | Blocked by: T15, T16 | Blocks: —
  References: Same as T17
  Acceptance criteria: `uv run pytest tests/mcp_server/test_session_lifecycle.py -k "test_integration_close_recreate_fresh" -v` passes.
  QA scenarios: (happy) New session "s1" has fresh resources, different from old session "s1". (failure) Remove cleanup, verify old resources leak into new session. Evidence: `.omo/evidence/task-18-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(mcp): integration test for session close→recreate freshness

- [ ] 19. Test: concurrent `cleanup_session()` calls (WebSocket disconnect + SessionController)
  What to do / Must NOT do: Create test that simulates concurrent cleanup: spawn `asyncio.gather(agent.mcp.cleanup_session("s1"), agent.mcp.cleanup_session("s1"))`. Verify no errors, no double-cleanup, `_session_contexts` is empty. Use `@pytest.mark.unit`. Must NOT use real WebSocket connections — mock the disconnect trigger.
  Parallelization: Wave 4 | Blocked by: T4, T15 | Blocks: T22
  References: `tests/mcp_server/test_session_lifecycle.py` (existing test patterns from T5)
  Acceptance criteria: `uv run pytest tests/mcp_server/test_session_lifecycle.py -k "test_concurrent_cleanup_from_two_paths" -v` passes.
  QA scenarios: (happy) Both calls complete without error, only one does actual cleanup. (failure) Remove lock from cleanup_session, verify race condition or double-cleanup error. Evidence: `.omo/evidence/task-19-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(mcp): concurrent cleanup_session from WebSocket and SessionController

### Wave 5: P1e — Fix resume_session Early-Return (depends on Wave 4)

- [ ] 20. Remove early-return and implement close-then-recreate in `resume_session()`
  What to do / Must NOT do: In `session_manager.py:243-249`, remove the early-return that returns stale session when `session_id in self._acp_sessions`. Replace with: (1) if session exists, call `SessionController.close_session(session_id)` first (handles RunHandle lifecycle with 10s timeout + cancel, calls `agent.mcp.cleanup_session()` via T15, calls `agent.__aexit__()`), (2) then call `ACPSession.close()` for ACP-specific cleanup (acp_env, signals, prompts — also calls `cleanup_session()` via T15, but idempotent via D8 lock), (3) remove from `_acp_sessions`, (4) proceed to create fresh session. Fallback: if `SessionController` is unavailable (tests), call `ACPSession.close()` only. Must NOT skip the `SessionController.close_session()` call when it's available — it handles active runs. Must NOT skip `ACPSession.close()` — it handles ACP-specific state.
  Parallelization: Wave 5 | Blocked by: T15, T16 | Blocks: T21, T22, T23
  References: `src/agentpool_server/acp_server/session_manager.py:243-249` (early-return to remove), `session_manager.py:45` (_acp_sessions dict), `session_manager.py:371-391` (close_all_sessions pattern), `src/agentpool/orchestrator/session_controller.py:951-966` (close_session one-liner)
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/ -v` — existing tests pass. `uv run ruff check src/agentpool_server/acp_server/session_manager.py` passes. `uv run --no-group docs mypy src/agentpool_server/acp_server/session_manager.py` passes.
  QA scenarios: (happy) Resume existing session, verify old session is closed and new session has fresh resources. (failure) Resume with active run, verify RunHandle is cancelled with timeout. Evidence: `.omo/evidence/task-20-fix-mcp-session-lifecycle.txt`
  Commit: Y | fix(acp): resume_session close-then-recreate instead of early-return

- [ ] 21. Test: resume → verify old session closed → fresh MCP resources
  What to do / Must NOT do: Create test: create session, run turn, resume same session, verify old session was closed (check `_acp_sessions` had old entry removed and re-added), verify new session has fresh MCP resources (different toolset objects). Use `@pytest.mark.integration`.
  Parallelization: Wave 5 | Blocked by: T20 | Blocks: —
  References: `tests/agentpool_server/acp_server/test_acp_mcp_agent_integration.py` (integration test patterns)
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/ -k "test_resume_closes_old_session" -v` passes.
  QA scenarios: (happy) Resumed session has fresh MCP resources. (failure) Revert early-return, verify test fails (stale resources). Evidence: `.omo/evidence/task-21-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(acp): resume_session closes old and creates fresh

- [ ] 22. Test: resume after WebSocket reconnect → fresh ACP connections
  What to do / Must NOT do: Create test: create session with ACP MCP server, simulate WebSocket disconnect, reconnect, resume session, verify fresh ACP connections are created and no stale connection references remain. Use `@pytest.mark.integration`. Must NOT use real WebSocket — mock the connection/disconnect.
  Parallelization: Wave 5 | Blocked by: T15, T19, T20 | Blocks: —
  References: `tests/agentpool_server/acp_server/test_acp_mcp_agent_integration.py`
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/ -k "test_resume_after_reconnect" -v` passes.
  QA scenarios: (happy) After reconnect+resume, ACP connections are fresh. (failure) Don't close old session on resume, verify stale connections persist. Evidence: `.omo/evidence/task-22-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(acp): resume after WebSocket reconnect creates fresh connections

- [ ] 23. Test: resume with active run → RunHandle cancelled with timeout
  What to do / Must NOT do: Create test: create session, start a long-running turn, resume same session while run is active, verify RunHandle is cancelled with timeout before cleanup proceeds. Use `@pytest.mark.integration`. Must NOT block forever — use `asyncio.wait_for` in test with 30s timeout.
  Parallelization: Wave 5 | Blocked by: T20 | Blocks: —
  References: `src/agentpool/orchestrator/session_controller.py:835-949` (_close_session_run_turn with 10s timeout)
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/ -k "test_resume_with_active_run" -v` passes.
  QA scenarios: (happy) RunHandle cancelled, cleanup proceeds, new session created. (failure) Remove timeout from close_session, verify test hangs (would need timeout). Evidence: `.omo/evidence/task-23-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(acp): resume with active run cancels RunHandle

### Wave 6: P1f — WebSocket Disconnect Hook (depends on Waves 4+2)

- [ ] 24. Add `on_disconnect` parameter to `_handle_websocket_client()` and call in `ConnectionClosed` handler
  What to do / Must NOT do: Add `on_disconnect: Callable[[AgentSideConnection], Awaitable[None]] | None = None` parameter to `_handle_websocket_client()` at `transports.py:355`. **Metis GAP-3 resolution**: Generate a UUID4 string for each WebSocket connection at accept time, store it as `conn.connection_id: str` attribute on the `AgentSideConnection` instance (set right after creation at line 376). In the `ConnectionClosed` exception handler (line 412), call `await on_disconnect(conn)` BEFORE `conn.close()` in the finally block (line 414). The callback reads `conn.connection_id` to look up sessions. If `on_disconnect` is None, skip the call (backward compat). Must NOT make `on_disconnect` a required parameter. Must NOT call `on_disconnect` after `conn.close()`.
  Parallelization: Wave 6 | Blocked by: T15 | Blocks: T25, T26, T27
  References: `src/acp/transports.py:355-428` (_handle_websocket_client), `transports.py:412` (ConnectionClosed catch), `transports.py:414-428` (finally block)
  Acceptance criteria: `uv run pytest tests/ -k "websocket" -v` — existing WebSocket tests pass. `uv run ruff check src/acp/transports.py` passes. `uv run --no-group docs mypy src/acp/transports.py` passes.
  QA scenarios: (happy) Disconnect triggers `on_disconnect` callback with connection object. (failure) `on_disconnect=None`, verify no callback called and existing behavior unchanged. Evidence: `.omo/evidence/task-24-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(acp): add on_disconnect callback to websocket handler

- [ ] 25. Add `_connection_sessions` to ACPSessionManager and implement `close_all_sessions_for_connection()`
  What to do / Must NOT do: (1) Add `_connection_sessions: dict[str, set[str]]` (connection_id → session_ids) to `ACPSessionManager.__init__` (after `_acp_sessions` at line 45). **Metis GAP-3 resolution**: The `connection_id` is the UUID4 string generated and stored on `AgentSideConnection.connection_id` (from T24). Populate `_connection_sessions` when sessions are created/resumed — add `session_id` to `_connection_sessions[connection_id]` set. The `connection_id` must be passed from the `Client` object or from the `AgentSideConnection` when creating the session. Check `ACPSessionManager.create_session()` to see how `client: Client` is received and how to access the underlying connection's `connection_id`. (2) Implement `async close_all_sessions_for_connection(self, connection_id: str) -> None` — iterates sessions for the connection. For each session: call `SessionController.close_session(session_id)` first (RunHandle lifecycle with timeout + cancel), then call `ACPSession.close()` for ACP-specific cleanup. Both must be called — SessionController handles RunHandle + agent lifecycle, ACPSession.close() handles ACP-specific state. Remove the connection entry from `_connection_sessions` after all sessions are closed. Must NOT skip SessionController.close_session() when available. Must NOT skip ACPSession.close().
  Parallelization: Wave 6 | Blocked by: T15, T24 | Blocks: T26, T28
  References: `src/agentpool_server/acp_server/session_manager.py:45` (_acp_sessions), `session_manager.py:371-391` (close_all_sessions pattern), `src/agentpool/orchestrator/session_controller.py:951-966` (close_session)
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/ -k "close_all_sessions_for_connection" -v` passes. `uv run ruff check src/agentpool_server/acp_server/session_manager.py` passes.
  QA scenarios: (happy) Disconnect connection, all sessions for that connection are closed. (failure) Disconnect, verify sessions on other connections are NOT affected. Evidence: `.omo/evidence/task-25-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(acp): implement close_all_sessions_for_connection

- [ ] 26. Wire `on_disconnect` callback in server setup
  What to do / Must NOT do: In the server setup that creates `_handle_websocket_client()` call (search for where `_handle_websocket_client` is called — likely in `ACPWebSocketTransport` or a server module), pass a callback that calls `ACPSessionManager.close_all_sessions_for_connection(connection_id)`. The callback needs access to the `ACPSessionManager` instance and the `connection_id` — check how the connection_id is determined at the call site. Must NOT create a circular dependency between transports.py and session_manager.py — use a callback, not a direct import.
  Parallelization: Wave 6 | Blocked by: T24, T25 | Blocks: T27, T28
  References: Search for `_handle_websocket_client` call sites in `src/acp/` and `src/agentpool_server/acp_server/`. Check `src/acp/transports.py` for `ACPWebSocketTransport` class.
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/ -v` — existing tests pass. `uv run ruff check src/` passes on changed files.
  QA scenarios: (happy) WebSocket disconnect triggers `close_all_sessions_for_connection()`. (failure) Callback not wired, verify disconnect doesn't close sessions. Evidence: `.omo/evidence/task-26-fix-mcp-session-lifecycle.txt`
  Commit: Y | feat(acp): wire on_disconnect to close_all_sessions_for_connection

- [ ] 27. Tests: WebSocket disconnect closes sessions + other connections unaffected
  What to do / Must NOT do: Create tests: (1) `test_websocket_disconnect_closes_all_sessions` — create 2 sessions on same connection, disconnect, verify both closed via `cleanup_session()`, (2) `test_websocket_disconnect_preserves_other_connections` — create sessions on 2 connections, disconnect one, verify only that connection's sessions are closed. Use `@pytest.mark.integration`. Must NOT use real WebSocket — mock connection/disconnect.
  Parallelization: Wave 6 | Blocked by: T25, T26 | Blocks: —
  References: `tests/agentpool_server/acp_server/test_acp_mcp_agent_integration.py`
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/ -k "websocket_disconnect" -v` — both tests pass.
  QA scenarios: (happy) Disconnect closes all sessions for that connection. (failure) Don't wire callback, verify sessions remain open. Evidence: `.omo/evidence/task-27-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(acp): websocket disconnect closes sessions and preserves others

- [ ] 28. Test: WebSocket disconnect during active run → RunHandle cancelled with timeout
  What to do / Must NOT do: Create test: create session, start long-running turn, simulate WebSocket disconnect, verify RunHandle is cancelled with timeout before cleanup proceeds. Use `@pytest.mark.integration`. Must NOT block forever — use `asyncio.wait_for` in test with 30s timeout.
  Parallelization: Wave 6 | Blocked by: T25, T26 | Blocks: —
  References: `src/agentpool/orchestrator/session_controller.py:835-949` (_close_session_run_turn with 10s timeout)
  Acceptance criteria: `uv run pytest tests/agentpool_server/acp_server/ -k "websocket_disconnect_during_run" -v` passes.
  QA scenarios: (happy) RunHandle cancelled, cleanup proceeds. (failure) Remove timeout, verify test hangs. Evidence: `.omo/evidence/task-28-fix-mcp-session-lifecycle.txt`
  Commit: Y | test(acp): websocket disconnect during active run cancels RunHandle

### Wave 7: End-to-End Verification

- [ ] 29. Run full test suite for MCP and ACP server
  What to do / Must NOT do: Run `uv run pytest tests/mcp_server/ tests/agentpool_server/acp_server/ -v` and verify all tests pass. Capture full output. Must NOT mark any test as `xfail` or `skip` to make it pass.
  Parallelization: Wave 7 | Blocked by: ALL | Blocks: —
  References: All test files mentioned in previous todos
  Acceptance criteria: `uv run pytest tests/mcp_server/ tests/agentpool_server/acp_server/ -v` — 0 failures, 0 errors.
  QA scenarios: (happy) All tests pass. (failure) Any test fails — fix before proceeding. Evidence: `.omo/evidence/task-29-fix-mcp-session-lifecycle.txt`
  Commit: N

- [ ] 30. Run unit test suite
  What to do / Must NOT do: Run `uv run pytest -m unit` and verify all unit tests pass. Must NOT include slow or integration tests.
  Parallelization: Wave 7 | Blocked by: ALL | Blocks: —
  References: All test files
  Acceptance criteria: `uv run pytest -m unit` — 0 failures, 0 errors.
  QA scenarios: (happy) All unit tests pass. (failure) Any unit test fails — fix before proceeding. Evidence: `.omo/evidence/task-30-fix-mcp-session-lifecycle.txt`
  Commit: N

- [ ] 31. Ruff lint check
  What to do / Must NOT do: Run `uv run ruff check src/` and verify zero errors. Must NOT add `# noqa` comments to suppress errors.
  Parallelization: Wave 7 | Blocked by: ALL | Blocks: —
  References: All changed source files
  Acceptance criteria: `uv run ruff check src/` — 0 errors.
  QA scenarios: (happy) Zero lint errors. (failure) Any lint error — fix before proceeding. Evidence: `.omo/evidence/task-31-fix-mcp-session-lifecycle.txt`
  Commit: N

- [ ] 32. Mypy type check
  What to do / Must NOT do: Run `uv run --no-group docs mypy src/` and verify zero errors on changed files. Must NOT use `# type: ignore` to suppress errors (use proper type annotations).
  Parallelization: Wave 7 | Blocked by: ALL | Blocks: —
  References: All changed source files
  Acceptance criteria: `uv run --no-group docs mypy src/` — 0 errors on changed files.
  QA scenarios: (happy) Zero type errors. (failure) Any type error — fix before proceeding. Evidence: `.omo/evidence/task-32-fix-mcp-session-lifecycle.txt`
  Commit: N

- [ ] 33. Automated end-to-end ACP test (replaces manual test per Metis GAP-15)
  What to do / Must NOT do: Create automated integration test in `tests/agentpool_server/acp_server/test_e2e_session_lifecycle.py`: (1) Start ACP server in-process with a config that has MCP servers (use TestModel), (2) Create a mock WebSocket client that connects, (3) Create session, (4) Use MCP tool (mock), (5) Simulate WebSocket disconnect (close the mock connection), (6) Reconnect with new mock client, (7) Resume session, (8) Verify MCP tools work with fresh connections (assert toolset objects are different from pre-disconnect). Use `@pytest.mark.integration` and `@pytest.mark.slow`. Must NOT require a real ACP client or real model API key.
  Parallelization: Wave 7 | Blocked by: ALL | Blocks: —
  References: `agentpool serve-acp config.yml` command, example configs in `site/examples/*/config.yml`
  Acceptance criteria: All 8 steps complete successfully. MCP tools work after reconnect+resume.
  QA scenarios: (happy) Full flow works, MCP tools functional after resume. (failure) MCP tools fail after resume — indicates stale resources. Evidence: `.omo/evidence/task-33-fix-mcp-session-lifecycle.txt`
  Commit: N

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — verify every task in `openspec/changes/fix-mcp-session-lifecycle/tasks.md` is implemented and checked off. Compare task-by-task.
- [ ] F2. Code quality review — `uv run ruff check src/` and `uv run --no-group docs mypy src/` both pass with zero errors. Review changed code for `getattr`/`hasattr` usage (forbidden), missing type annotations, TODOs left in code.
- [ ] F3. Real manual QA — run the manual ACP test from T33: connect → session → MCP tool → disconnect → reconnect → resume → verify MCP tools work. Capture output as evidence.
- [ ] F4. Scope fidelity — verify NO changes to `MessageNode`, `AgentPool` registry, `MCPResourceProvider` model, or config API. Verify NO Phase 2 features were introduced. Verify all 5 stale-mcp tests are now fix-verifying (not bug-documenting).

## Commit strategy
- One commit per todo that has `Commit: Y` (28 commits)
- Todos with `Commit: N` (T29-T33 verification) are verification-only, no commits
- Commit message format: `<type>(<scope>): <summary>` matching repo style
- Types: `feat`, `fix`, `refactor`, `test`
- Scopes: `mcp`, `acp`, `agent`, `session`
- Branch: `fix-mcp-session-lifecycle` (already created as worktree)

## Success criteria
1. `uv run pytest tests/mcp_server/ tests/agentpool_server/acp_server/ -v` — 0 failures
2. `uv run pytest -m unit` — 0 failures
3. `uv run ruff check src/` — 0 errors
4. `uv run --no-group docs mypy src/` — 0 errors on changed files
5. Manual ACP test (T33) — MCP tools work after WebSocket disconnect + reconnect + resume
6. All 5 tests in `test_stale_mcp_connection.py` verify the fix (not the bug)
7. `_session_contexts` is empty after session close on all close paths (ACPSession.close, SessionController.close_session, WebSocket disconnect)
8. `resume_session()` creates fresh session, not returning stale one
