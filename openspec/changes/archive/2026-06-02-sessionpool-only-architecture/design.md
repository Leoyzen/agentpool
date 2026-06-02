## Context

AgentPool currently supports two execution paths for streaming agent runs:

1. **Legacy path**: `BaseAgent.run_stream()` creates its own `AgentRunContext`, manages `session_id` on the agent instance, sets `_current_run_ctx_var`, and yields events directly to the caller.
2. **SessionPool path**: `TurnRunner._run_turn_unlocked()` creates its own `AgentRunContext`, but does NOT set `_current_run_ctx_var`, does NOT initialize `session_id` on the agent, and publishes events to `EventBus`.

This bifurcation causes:
- ContextVar-dependent code (tools, child tasks) fails in SessionPool path because `_current_run_ctx_var` is never set.
- `run_ctx.event_queue` is consumed by multiple independent consumers (native agent's `merge_queue_into_iterator`, ClaudeCodeAgent's `merge_queue_into_iterator`, ACPAgent's `merge_queue_into_iterator`, and TurnRunner's `_consume_event_queue`), causing event loss or duplication.
- `session_id` lives on the agent instance (`self.session_id`) but SessionPool also tracks it, creating two sources of truth.
- `StreamEventEmitter._emit()` tries to access `run_ctx.event_bus` which does not exist, falling back to a global class variable.
- The `_stream_event_bus_set` flag is checked once at turn start, causing race conditions when `StreamEventEmitter._event_bus` changes mid-turn.

The design unifies all execution under SessionPool, making `BaseAgent` a pure execution engine and `SessionPool` the sole authority for session and run lifecycle, while preserving correct event flow for all event types.

## Goals / Non-Goals

**Goals:**
- `SessionPool` is the single, mandatory entry point for all streaming execution when `AgentPool` is used.
- `BaseAgent` holds no session-scoped mutable state (`session_id`, `_active_run_ctx`, `_current_stream_task`, `_event_queue`).
- `AgentRunContext` carries `session_id` and `event_bus`, enabling correct event routing without agent instance state.
- All events (stream and tool events) flow through `EventBus` exclusively; the dual-consumer race on `run_ctx.event_queue` is eliminated.
- Tool events remain visible in the stream yielded by `_run_stream_once()` via a TurnRunner-managed bridge.
- `TurnRunner` sets `_current_run_ctx_var` so ContextVar-dependent code works uniformly.
- Protocol handlers (ACP, OpenCode, AG-UI) receive child session events automatically via `EventBus.subscribe(scope="descendants")`.

**Non-Goals:**
- Removing `run()` and `run_stream()` from `BaseAgent` public API — they remain as deprecated convenience wrappers.
- Changing the ACP, AG-UI, or OpenCode protocol wire formats.
- Modifying `SessionManager` or `SessionData` schemas in the storage layer.
- Supporting arbitrary-depth session trees beyond parent-child (grandchildren are allowed but not explicitly optimized).
- Making shared agents (non-native types like ACP, ClaudeCode) fully session-safe for concurrent use. This is deferred to a future change. Per-session agents (NativeAgent with per-session config) are the recommended pattern.

## Decisions

### Decision 1: BaseAgent is a pure execution engine

Remove `session_id`, `_active_run_ctx`, `_current_stream_task`, and `_event_queue` from `BaseAgent`. The agent instance is stateless with respect to sessions. `_run_stream_once(run_ctx, *prompts, session_id=..., **kwargs)` is the sole execution method; `session_id` becomes a required parameter.

**Rationale:** Eliminates the split source of truth for `session_id` and prevents shared agents from carrying stale session state.

**Caveat:** `conversation` (MessageHistory) and `tools` (ToolManager) remain instance-level. Concurrent sessions on shared agents will interleave conversation history. This is a known limitation. The recommended pattern is per-session agents (NativeAgentConfig), which SessionController already supports.

**Alternative considered:** Keep instance-level `session_id` but have SessionPool override it before each call. Rejected because it still creates temporal coupling and is error-prone.

### Decision 2: TurnRunner is the sole run orchestrator

`TurnRunner._run_turn_unlocked()` becomes the only code path that creates `AgentRunContext`, sets `_current_run_ctx_var`, manages the prompt injection loop, and publishes events to `EventBus`. `BaseAgent.run_stream()` delegates to `SessionPool` when available; for standalone usage (no AgentPool), it keeps a simplified legacy path.

**Rationale:** Consolidates all lifecycle logic in one place. Prevents the current duplication where both `run_stream()` and `TurnRunner` manage injection loops but with different cleanup paths.

**Alternative considered:** Have `run_stream()` call `TurnRunner` internally. Rejected because it introduces an unnecessary indirection and still leaves `run_stream()` as a public API that can bypass SessionPool.

### Decision 3: Events flow through EventBus with a stream bridge

`StreamEventEmitter._emit()` publishes directly to `EventBus` using `run_ctx.session_id` and `run_ctx.event_bus`. It no longer puts events into `run_ctx.event_queue`.

To preserve event visibility in the stream, `TurnRunner` creates a **per-run EventBus subscriber** that feeds EventBus events back into the stream. Inside `NativeAgent._stream_events()`, `merge_queue_into_iterator(stream, run_ctx.event_queue)` is replaced with `merge_queue_into_iterator(stream, turn_runner_queue)` where `turn_runner_queue` is the queue fed by the EventBus subscriber.

**Rationale:** Eliminates the dual-consumer race condition while preserving the unified event stream. The native agent's `process_tool_event()` logic continues to work because tool events still flow through the stream.

**Alternative considered:** Remove `merge_queue_into_iterator` entirely and have TurnRunner consume only from EventBus. Rejected because it breaks `process_tool_event()` and makes the stream interface inconsistent (direct consumers of `_run_stream_once()` would miss tool events).

### Decision 4: AgentRunContext carries session_id and event_bus

Add `session_id: str | None` and `event_bus: Any | None` to `AgentRunContext`. `TurnRunner` populates these fields when creating `run_ctx`. `StreamEventEmitter._emit()` reads `run_ctx.session_id` and `run_ctx.event_bus` instead of `agent.session_id` and the global `StreamEventEmitter._event_bus`.

**Rationale:** Decouples event emission from agent instance state. Enables per-run event routing.

**Note:** `AgentRunContext.session_id` is currently a `_DeprecatedField`. This change reverts that deprecation. The old deprecation warning is removed because session IDs move back to the run context (for a different reason than the original design).

### Decision 5: Remove _stream_event_bus_set race condition

Remove the `_stream_event_bus_set` flag and its associated fallback logic. `StreamEventEmitter._emit()` always publishes to `run_ctx.event_bus` when it is set. If `run_ctx.event_bus` is None (legacy standalone path), `_emit()` falls back to `run_ctx.event_queue.put(event)` to preserve standalone agent behavior. No events are dropped.

**Rationale:** The `_stream_event_bus_set` check was a workaround for the split between legacy and SessionPool paths. With a unified path, it's no longer needed.

### Decision 6: SessionPool is always enabled

Remove the `session_pool.enabled` feature flag from YAML config. `AgentPool` always composes `SessionPool`. Standalone usage (no `AgentPool`) keeps a simplified legacy path in `BaseAgent.run_stream()`.

**Rationale:** Feature flags were needed for gradual rollout during initial SessionPool development. Now that SessionPool is the target architecture, the flag adds unnecessary branching complexity.

**Caveat:** Standalone agents (created without AgentPool) cannot use SessionPool because SessionPool requires AgentPool for agent resolution. These agents keep their legacy `run_stream()` implementation.

### Decision 7: Protocol handlers subscribe with descendants scope

ACP, OpenCode, and AG-UI protocol handlers use `EventBus.subscribe(session_id, scope="descendants")` so that child session events are automatically routed to parent subscribers.

**Rationale:** Fixes the current bug where OpenCode uses `scope="session"` and misses all child session events. ACP already uses `descendants`; this makes all consistent.

### Decision 8: Interrupt uses run_ctx.current_task

`BaseAgent.interrupt()` uses `run_ctx.current_task` (stored in AgentRunContext by TurnRunner) for cancellation instead of `_current_stream_task` or `_iteration_task`. Each agent type's `_run_stream_once()` handles task cancellation appropriately.

**Rationale:** `current_task` is already stored in `AgentRunContext` by both legacy and SessionPool paths. Using it for interrupt avoids the issue where `_iteration_task` only exists on NativeAgent.

## Risks / Trade-offs

- **[Risk]** Tests that call `agent.run_stream()` directly will need to migrate to `session_pool.run_stream()` or accept deprecation warnings.  
  → **Mitigation**: `run_stream()` remains as a deprecated wrapper that delegates to SessionPool when available. Tests continue to work but emit warnings.

- **[Risk]** Shared agents (non-native types like ACP, ClaudeCode) still share `conversation` and `tools` state. Concurrent sessions will interleave history.  
  → **Mitigation**: Document this limitation. Per-session agents (NativeAgentConfig) are the recommended pattern. A future change can address shared agent state.

- **[Risk]** Removing `_active_run_ctx` from `BaseAgent` may break `inject_prompt()` and `interrupt()` callers that relied on instance-level state.  
  → **Mitigation**: `inject_prompt()` delegates to `SessionPool.inject_prompt()` which looks up `session.active_run_ctx`. `interrupt()` uses `run_ctx.current_task`. Standalone agents keep legacy behavior.

- **[Risk]** EventBus descendant lookups on every `publish()` are O(tree_depth), not O(1).  
  → **Mitigation**: Session trees are typically shallow (1-3 levels). If performance becomes an issue, pre-compute a flattened descendant index per session.

- **[Risk]** Per-run EventBus subscriber adds overhead (one subscriber per turn).  
  → **Mitigation**: Subscriber is created and destroyed with the turn. Queue is bounded. Cleanup happens in TurnRunner's finally block.

- **[Risk]** Native agent's `merge_queue_into_iterator` was designed to merge tool events from `run_ctx.event_queue`. Replacing the queue source may introduce subtle timing changes.  
  → **Mitigation**: The TurnRunner-managed queue is fed by an EventBus subscriber, so events arrive asynchronously. The `merge_queue_into_iterator` timeout (0.01s) handles this.

- **[Risk]** Standalone agent usage (no AgentPool) keeps the legacy path, creating a maintenance burden.  
  → **Mitigation**: The legacy path is simplified (no SessionPool integration) and marked for future removal. Most production usage goes through AgentPool.

- **[Risk]** `AgentContext.report_progress()` currently puts events into `run_ctx.event_queue` or `agent._event_queue`. Both paths are removed.  
  → **Mitigation**: Update `report_progress()` to publish to `run_ctx.event_bus` when available.

## Migration Plan

1. **Phase 1**: Update `AgentRunContext` with `session_id` and `event_bus` fields. Remove the `_DeprecatedField` for `session_id`.
2. **Phase 2**: Update `StreamEventEmitter._emit()` to use `run_ctx.session_id` and `run_ctx.event_bus`. Remove `_stream_event_bus_set` logic.
3. **Phase 3**: Update `TurnRunner._run_turn_unlocked()` to set `_current_run_ctx_var`, create per-run EventBus subscriber, and bridge events back into the stream. Remove `_consume_event_queue`.
4. **Phase 4**: Update `BaseAgent` to remove session-scoped state and deprecate `run_stream()`.
5. **Phase 5**: Update `NativeAgent`, `ClaudeCodeAgent`, and `ACPAgent` to use TurnRunner-managed queue instead of `run_ctx.event_queue`.
6. **Phase 6**: Update `AgentPool` to always compose `SessionPool` (remove feature flag).
7. **Phase 7**: Update protocol handlers (ACP, OpenCode, AG-UI) to confirm `scope="descendants"`.
8. **Phase 8**: Update `AgentContext.report_progress()` to use EventBus.
9. **Phase 9**: Update tests and verify no regressions.

## Open Questions

- Should `BaseAgent.run()` and `run_stream()` emit a `DeprecationWarning` or a custom warning subclass?
- How should per-session conversation history work for shared agents (non-native types)? Is this in scope for a follow-up change?
- Should we pre-compute a flattened descendant index in EventBus for O(1) scope lookups?
