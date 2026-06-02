## Why

AgentPool currently has two parallel execution paths for agent streaming: `BaseAgent.run_stream()` (the legacy direct-usage path) and `TurnRunner._run_turn_unlocked()` (the SessionPool orchestration path). This bifurcation creates fundamental lifecycle inconsistencies: `run_ctx` ContextVar is only set in the legacy path, `session_id` is managed by both the agent instance and SessionPool simultaneously, events flow through competing consumers (`run_ctx.event_queue` is read by the native agent's `merge_queue_into_iterator`, ClaudeCodeAgent's `merge_queue_into_iterator`, ACPAgent's `merge_queue_into_iterator`, and TurnRunner's `_consume_event_queue`), and the `_stream_event_bus_set` flag causes race conditions. These issues make the codebase fragile, hard to reason about, and impossible to guarantee correct event routing for child sessions. We need a single, unified execution model where SessionPool is the sole authority for all session and run lifecycle management.

## What Changes

- **BREAKING**: `BaseAgent.run_stream()` is deprecated. SessionPool becomes the only supported entry point for streaming execution when `AgentPool` is used.
- **BREAKING**: `BaseAgent.session_id`, `_active_run_ctx`, `_current_stream_task`, and `_event_queue` instance attributes are removed. Agent instances are pure execution engines with no session-scoped mutable state.
- `AgentRunContext` gains `session_id` and `event_bus` fields so that `StreamEventEmitter._emit()` can correctly route events without relying on agent instance state or global class variables.
- `TurnRunner._run_turn_unlocked()` becomes the sole run orchestrator. It is responsible for creating `run_ctx`, setting `_current_run_ctx_var`, managing the prompt injection loop, and forwarding events to `EventBus`.
- Tool events (via `StreamEventEmitter._emit()`) are published directly to `EventBus` AND put into a **TurnRunner-managed per-run queue** that feeds back into the stream. This eliminates the dual-consumer race on `run_ctx.event_queue` while preserving event visibility in the stream.
- Native agent's `merge_queue_into_iterator(stream, run_ctx.event_queue)` is replaced with a TurnRunner-managed queue. ClaudeCodeAgent and ACPAgent receive similar treatment.
- `SessionPool` is always enabled when `AgentPool` is used. The `session_pool.enabled` feature flag is removed.
- Protocol handlers (ACP, OpenCode, AG-UI) subscribe to `EventBus` with `scope="descendants"` so child session events are automatically routed to parent subscribers.
- `BaseAgent.run()` and `run_stream()` remain as convenience wrappers but delegate internally to `SessionPool` when available; standalone usage without `AgentPool` keeps a simplified legacy path.

## Capabilities

### New Capabilities

- `sessionpool-only-execution`: SessionPool is the single, mandatory entry point for all agent streaming. BaseAgent is a pure execution engine with no session-scoped mutable state.
- `unified-event-routing`: All events (stream events and tool events) are published directly to EventBus. No dual-consumer race on `run_ctx.event_queue`.
- `runctx-session-binding`: `AgentRunContext` carries `session_id` and `event_bus`, enabling correct event routing without agent instance state.

### Modified Capabilities

- *(none — this is primarily an internal architecture refactor with no external protocol behavior changes)*

## Impact

- `agentpool/agents/base_agent.py`: Removes session lifecycle management (`session_id`, `_active_run_ctx`, `_current_stream_task`, `_event_queue`). Adds deprecation warnings to `run_stream()`. `run()` delegates to SessionPool.
- `agentpool/agents/context.py`: `AgentRunContext` gains `session_id: str | None` and `event_bus: Any | None`.
- `agentpool/agents/events/event_emitter.py`: `_emit()` publishes directly to EventBus using `run_ctx.session_id` and `run_ctx.event_bus`.
- `agentpool/agents/native_agent/agent.py`: `merge_queue_into_iterator` no longer merges `run_ctx.event_queue`; uses TurnRunner-managed queue instead.
- `agentpool/agents/claude_code_agent/claude_code_agent.py`: Similar refactor for `merge_queue_into_iterator`.
- `agentpool/agents/acp_agent/acp_agent.py`: Similar refactor for `merge_queue_into_iterator`.
- `agentpool/orchestrator/core.py`: `TurnRunner` sets `_current_run_ctx_var`, manages the prompt injection loop, removes `_consume_event_queue` fallback, and bridges EventBus events back into the stream.
- `agentpool/delegation/pool.py`: `SessionPool` is always composed (remove `session_pool.enabled` feature flag).
- `agentpool_server/acp_server/handler.py`, `agentpool_server/opencode_server/handler.py`, `agentpool_server/agui_server/`: Confirm/ensure `scope="descendants"` subscription.
- All tests using `agent.run_stream()` directly need to migrate to `session_pool.run_stream()`.
