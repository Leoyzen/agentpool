# Spec: ACP Server — Execution Path Unification

## Requirements

### REQ-1: Single Execution Path

ACP agents MUST execute through `ACPTurn.execute()` as the sole execution path. The legacy `_run_stream_once()` hook firing for `AGENT_TYPE != "native"` MUST be removed.

**Rationale**: Dual execution paths require a `hooks_fired` double-fire guard that complicates hook ordering and makes debugging difficult.

### REQ-2: ACPAgentAPI Completeness

`ACPAgentAPI` MUST implement the full `ACPClientProtocol`, including `stream_events()` and `get_messages()` methods. `ACPTurn.execute()` depends on these methods to function.

The `stream_events()` implementation MUST bridge the gap between `ACPTurn.execute()`'s async-iterator-based event delivery and the inline `_stream_events()`'s state-polling mechanism. The inline path polls `pop_update()` on `self._state` (a `SessionState` object), woken by `_update_event` (a `TimeoutableEvent`) via `wait_with_timeout(0.05)`. The adapter MUST wrap this polling loop into an async iterator — it may NOT simply delegate to `ACPClient.stream_events()` if the underlying transport uses polling.

The `cast("ACPClientProtocol", self._api)` at `acp_agent.py:654` MUST be removed. `isinstance(self._api, ACPClientProtocol)` MUST pass without cast.

**Rationale**: Without these methods, `ACPTurn.execute()` is dead code. ACP standalone falls back to an inline 200-LOC `_stream_events()` implementation. The cast hides the incomplete implementation from the type checker.

### REQ-3: hooks_fired Removal

The `hooks_fired: set[str]` field on `AgentRunContext` MUST be removed. All 21 references across `base_agent.py`, `turn.py`, `run.py`, and `context.py` MUST be eliminated.

The `_log_tool_execution` method at `turn.py:213-265` uses `hooks_fired` for tool-log idempotency (`tool_log:{tool_call_id}` key). This MUST be replaced with a `set[str]` local to the Turn instance (`self._logged_tools: set[str]`) to preserve idempotency after `hooks_fired` removal.

**Rationale**: The guard exists only to prevent double-firing when both `_run_stream_once()` and `Turn.execute()` run in the same turn. With a single execution path, the guard is unnecessary. The tool-log idempotency guard is independent of the hook system and must be preserved.

### REQ-4: Deprecated API Removal

The following deprecated APIs MUST be removed:
- `queue_prompt`/`inject_prompt` ACP-specific branching (`base_agent.py:1024-1139`)

**Rationale**: These code paths are superseded by `session_pool.followup()`/`steer()`.

> Note: `ACPEventConverter.subagent_display_mode` removal was moved to Phase 7 (task 7.10, deferred). The field is actively used in 15+ locations for 3 display modes (legacy/zed/qwen) and is NOT merely a deprecated constructor field. Removal requires a design decision on what replaces the display mode logic. See active changes `add-zed-subagent-display-mode` and `subagent-qwen-display-mode`.

### REQ-5: MCP State Encapsulation

`NativeAgent._mcp_snapshot` and `NativeAgent._session_connection_pool` MUST be removed. All MCP session state MUST be consolidated on `MCPManager` with the following API:
- `MCPManager.get_session_context(session_id) -> McpSessionContext`
- `MCPManager.add_transport(session_id, transport) -> None`
- `MCPManager.update_snapshot(session_id, snapshot) -> None`

`NativeAgent.get_agentlet()` MUST query `MCPManager.get_session_context()` instead of reading `self._mcp_snapshot`. `ACPSession.initialize_mcp_servers()` MUST call `MCPManager` methods instead of mutating agent fields.

**Rationale**: External mutation of agent fields is an encapsulation violation that makes agent state unpredictable. `get_agentlet()` reads `_mcp_snapshot` for capability building, so the migration path must account for this dependency.

### REQ-6: Event Ordering and Metadata Equivalence

After unification, `ACPTurn.execute()` MUST produce the same event sequence as the inline `_stream_events()` path, including:
1. Event ordering for near-simultaneous updates (state polling vs async iterator may differ)
2. Tool metadata enrichment: the inline path enriches `ToolCallCompleteEvent` with metadata from `ToolResultMetadataEvent` — `ACPTurn.execute()` MUST do the same

**Rationale**: The inline path and `ACPTurn.execute()` use fundamentally different event delivery mechanisms (state polling vs `stream_events()` async iterator). Event ordering and metadata enrichment must be equivalent to avoid behavioral regression.

## Verification

- `grep -rn 'hooks_fired' src/` returns 0
- `grep -rn '_mcp_snapshot\|_session_connection_pool' src/` returns 0
- `grep -rn 'subagent_display_mode' src/agentpool_server/acp_server/` returns 0
- ACP standalone streaming snapshot test passes (same events as before)
- `isinstance(acp_agent_api, ACPClientProtocol)` passes
