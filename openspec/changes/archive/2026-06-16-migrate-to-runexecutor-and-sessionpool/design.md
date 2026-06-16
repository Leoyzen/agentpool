## Context

AgentPool currently has three parallel implementations of the pydantic-ai iteration loop for native agents:

1. **`_run_agentlet_core()`** (`native_agent/agent.py:859`) — the actual execution path. Called by `_stream_events()` (standalone/TurnRunner) and `_execute_node()` (graph). Drives `agentlet.iter()` with `next(node)`. **Bug**: never sets `RunHandle.active_agent_run`, so `TurnRunner.steer()`/`followup()` always falls through to idle path.

2. **`RunExecutor.execute()`** (`orchestrator/run_executor.py:67`) — dead code in production (only instantiated in tests). Correctly manages `active_agent_run`, has `UndrainedPendingMessagesError` handling, and a self-contained consumer loop. Missing `emitted_tool_starts` dedup (both `FunctionToolCallEvent` and `PartStartEvent(BaseToolCallPart)` paths), cancelled-response fallback, and `session_id`/`parent_session_id` on `RunStartedEvent`.

3. **`merge_queue_into_iterator` branch** in `_run_agentlet_core()` (lines 983-998) — merges `run_ctx.event_queue` into the pydantic-ai stream for standalone mode when `event_bus` is None. Known bug (BUG-001) with task switching.

`BaseAgent.run_stream()` (lines 901-939) already delegates to `SessionPool.run_stream()` when `self.agent_pool.session_pool` is available. The `_execute_direct()` fallback (lines 939+) is the remaining standalone path that this change removes.

## Goals / Non-Goals

**Goals:**
- `RunExecutor.execute()` is the single, unified implementation of the pydantic-ai iteration loop for native agents.
- `_run_agentlet_core()` is removed entirely.
- `_stream_events()` and `_execute_node()` both use `RunExecutor.execute()`.
- `merge_queue_into_iterator` is removed from the native agent path.
- Migratable standalone `agent.run_stream()` callers (Vercel serve, OpenAI API, ACP session) use `SessionPool.run_stream()`.
- `BaseAgent.run_stream()` removes `_execute_direct()` fallback; raises `RuntimeError` when unpooled.
- `RunExecutor` gains missing features: dedup, fallback, `session_id`/`parent_session_id` on `RunStartedEvent`.
- `_consume_event_queue` in TurnRunner is gated behind `agent_type != "native"`.

**Non-Goals:**
- Migrating AG-UI adapter (`agui_server/base_agent_adapter.py:126`) — permanent protocol bypass pattern.
- Migrating streaming tools (`streaming_tools.py:119,164`) — uses `message_history=fork_history` incompatible with `SessionPool.run_stream()`.
- Migrating FSSpec toolset (`fsspec_toolset/toolset.py`) — same fork-history + turn-context deadlock pattern.
- Removing `merge_queue_into_iterator` from ACP agent or Claude Code agent (separate concern).
- Changing graph execution path's event semantics beyond using `RunExecutor`.

## Decisions

### Decision 1: `_stream_events()` wraps `RunExecutor.execute()` instead of `_run_agentlet_core()`

`RunExecutor.execute()` is an async generator that yields `RunExecutorEvent` tokens with built-in background-task + consumer-loop isolation. `_stream_events()` directly iterates over it:

```python
# After (_stream_events):
executor = RunExecutor(self, run_handle)
async for event in executor.execute(
    prompts=..., run_ctx=..., session_id=..., parent_id=...,
    user_msg=..., message_history=..., message_id=...,
    input_provider=..., deps=...,
):
    yield event
```

Since `execute()` already yields `RunStartedEvent` and `StreamCompleteEvent`, `_stream_events()` no longer emits these itself. `_run_stream_once()` (line 1205) still captures `StreamCompleteEvent` for `final_message`.

**Cancellation**: `_interrupt()` at agent.py:1263 reads `self._iteration_task` to cancel the LLM API call. After migration, `_stream_events()` won't manage `self._iteration_task` directly. Cancellation works through `run_ctx.current_task` cancel → `execute()` consumer loop TimeoutError check (lines 272-274) → finally block cancels `RunExecutor._iteration_task`. This adds up to 100ms extra latency (the 0.1s polling interval). Acceptable trade-off; a follow-up could wire `self._iteration_task = executor._iteration_task` for direct cancellation.

**Rationale**: `execute()` already encapsulates the full lifecycle. Nesting in another background task would be redundant.

### Decision 2: `_execute_node()` also uses `RunExecutor.execute()`

```python
# After (_execute_node):
executor = RunExecutor(self, run_handle)
response_msg = None
async for event in executor.execute(
    prompts=list(prompts), run_ctx=run_ctx, ...
):
    state.event_queue.put_nowait(event)
    if isinstance(event, StreamCompleteEvent):
        response_msg = event.message
return response_msg
```

All events including `RunStartedEvent` are forwarded to `state.event_queue`. The graph framework already handles `StreamCompleteEvent` in its queue (current code puts it there at agent.py:1017). `RunStartedEvent` is new to the graph queue — verify the graph consumer handles it gracefully (task 7.3).

### Decision 3: `RunExecutor` gains features from `_run_agentlet_core`

1. **`emitted_tool_starts` dedup**: Port both `FunctionToolCallEvent` and `PartStartEvent(BaseToolCallPart)` dedup from agent.py:919-972.
2. **Cancelled-response fallback**: Create empty `ChatMessage` with `finish_reason="stop"` when cancelled before response (agent.py:1210-1217 pattern).
3. **Concurrent-run warning**: Log warning when `self._iteration_task` is still active (agent.py:1168-1173 pattern).
4. **`session_id` and `parent_session_id` on `RunStartedEvent`**: Port from agent.py:1131-1136.

### Decision 4: Only migratable standalone callers are migrated

| Caller | Action | Rationale |
|---|---|---|
| `serve_vercel.py:148` | Migrate | Has session_pool access |
| `openai_api_server/completions/helpers.py:43` | Migrate | Has session_pool access |
| `acp_server/session.py:650` | Formalize | Already routes through SessionPool via `BaseAgent.run_stream()` delegation |
| `agui_server/base_agent_adapter.py:126` | **SKIP** | Permanent protocol bypass (documented in `docs/audit/agui-bypass-audit.md`) |
| `streaming_tools.py:119,164` | **SKIP** | Uses `message_history=fork_history` — `SessionPool.run_stream()` doesn't support this |
| `fsspec_toolset/toolset.py:1310,1438,1524` | **SKIP** | Same fork-history + turn-context deadlock pattern |

### Decision 5: `BaseAgent.run_stream()` removes `_execute_direct()` fallback

The existing delegation (lines 901-939) stays. The `_execute_direct()` fallback (lines 939+) is removed. When `SessionPool` is unavailable, `run_stream()` raises `RuntimeError` indicating SessionPool is required.

**Rationale**: The delegation already exists. The change is making it the ONLY path.

### Decision 6: Gate `_consume_event_queue` for native agents

After migration, tool events go to EventBus directly via `StreamEventEmitter._emit()`, and pydantic-ai events go through `RunExecutor.execute()` → `_stream_events()` → TurnRunner → EventBus. `_consume_event_queue` (core.py:1456) becomes a no-op for native agents. Gate it behind `agent_type != "native"`.

**Rationale**: Non-native agents (ACP, Claude Code) still need `_consume_event_queue` for their `merge_queue_into_iterator` pattern.

## Risks / Trade-offs

- **[Risk] `_interrupt()` cancellation latency**: Up to 100ms extra due to 0.1s polling interval in `execute()` consumer loop. → **Mitigation**: Acceptable trade-off. Follow-up can wire `self._iteration_task = executor._iteration_task` for direct cancellation. Task 7.6 verifies cancellation still works.
- **[Risk] `RunStartedEvent` in graph state queue**: New event type for graph consumers (previously only emitted by `_stream_events()`, not `_run_agentlet_core()`). → **Mitigation**: Task 7.3 verifies graph consumers handle it.
- **[Risk] `RunExecutor` missing `PartStartEvent` dedup**: May cause duplicate `ToolCallStartEvent` if only `FunctionToolCallEvent` is dedup'd. → **Mitigation**: Port both paths (task 1.1-1.2).
- **[Risk] Test files calling `_run_agentlet_core()` directly**: 7 test files will break. → **Mitigation**: Task 4.3 migrates all test callers.
- **[Risk] `merge_queue_into_iterator` removal from native agent**: ACP agent still uses it. → **Mitigation**: Keep utility function in `streams.py`, only remove native agent import.

## Migration Plan

1. Add missing features to `RunExecutor` (dedup, fallback, warning, RunStartedEvent fields).
2. Refactor `_stream_events()` to use `RunExecutor.execute()`.
3. Refactor `_execute_node()` to use `RunExecutor.execute()`.
4. Remove `_run_agentlet_core()` and migrate test callers.
5. Remove `merge_queue_into_iterator` import from native agent.
6. Gate `_consume_event_queue` for native agents in TurnRunner.
7. Migrate migratable standalone callers (Vercel, OpenAI API, ACP session).
8. Remove `_execute_direct()` fallback from `BaseAgent.run_stream()`.
9. Run full test suite; fix regressions.
