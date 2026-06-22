## Context

The ACP server's architecture has two parallel session management systems:

1. **SessionPool path** (via `SessionController`/`TurnRunner`/`EventBus`): The unified orchestration layer introduced during the sessionpool migration. Used by `ACPProtocolHandler` for prompt dispatch and event consumption.

2. **Legacy path** (via `ACPSessionManager`/`ACPSession`): The original session management that maintains its own `_active: dict[str, ACPSession]` and has a fallback code path in `ACPSession.process_prompt()` that directly calls `agent.run_stream()` when `SessionPool` is unavailable.

Additionally, there are redundant infrastructure components:
- `EventBusHooksAdapter` publishes `RunStartedEvent` to EventBus, but `RunExecutor` already does this
- `SessionStatusBridge` subscribes to EventBus separately from the main OpenCode consumer, handling only lifecycle events — and causes an existing double-broadcast bug of `SessionStatusEvent(type="busy")`
- AG-UI and OpenAI API servers subscribe to EventBus via `ProtocolEventConsumerMixin` but have no-op `_handle_event`

Current ACP prompt processing flow:
```
ACPProtocolHandler (always used in production)
  └── SessionPool.receive_request()
       └── TurnRunner.run_loop()
            └── agent.run_stream() → events → EventBus

Legacy path (dead code in production):
  acp_agent.prompt() → session.process_prompt()
    ├── [SessionPool available] → session_pool.run_stream()
    └── [Fallback] → agent.run_stream() directly  ← TARGET FOR REMOVAL
```

Target flow after cleanup:
```
ACPProtocolHandler  ← ONLY PATH
  └── SessionPool.receive_request()
       └── TurnRunner.run_loop()
            └── agent.run_stream() → events → EventBus
```

## Goals / Non-Goals

**Goals:**
- Eliminate the non-SessionPool fallback path in `ACPSession.process_prompt()` and remove the dead legacy `acp_agent.prompt()` path (lines 656-698)
- Remove `EventBusHooksAdapter` entirely
- Merge `SessionStatusBridge` into `OpenCodeSessionPoolIntegration._handle_event()`, fixing the existing double-broadcast bug
- Eliminate the wasteful no-op event processing loop in AG-UI and OpenAI API servers
- Rename `ACPSessionManager._active` to `_acp_sessions`, separating lifecycle tracking (delegated to `SessionController`) from protocol-specific state storage
- Consolidate duplicate `RunFailedEvent` types

**Non-Goals:**
- Remove or modify event type definitions (they remain available for future subscriptions)
- Change the `ACPSession` class structure (protocol-specific session state is legitimate)
- Migrate MCP or A2A servers to SessionPool (architecturally different)
- Remove `ProtocolEventConsumerMixin` entirely — it remains the foundation for ACP and OpenCode consumers

## Decisions

### Decision 1: Remove ACPSession.process_prompt() fallback + dead legacy acp_agent.prompt()

**Chosen**: Remove the `else` branch in `ACPSession.process_prompt()` that calls `agent.run_stream()` directly. Also remove the dead legacy prompt path in `acp_agent.py` (lines 656-698) that calls `session.process_prompt()` when `_protocol_handler.handle_prompt()` returns `None` — which never happens.

**Rationale**: `ACPProtocolHandler.handle_prompt()` always returns a `PromptResponse`, so the legacy fallthrough in `acp_agent.prompt()` is dead code. When the legacy path was reachable (pre-SessionPool migration), `session_pool` was always available because `AgentPool.__aenter__()` creates it unconditionally. The fallback existed only for tests creating `ACPSession` without pool lifecycle.

**Alternatives considered**:
- Keep fallback with deprecation warning → defeats the purpose of cleanup; adds noise

### Decision 2: Fold SessionStatusBridge into OpenCodeSessionPoolIntegration + fix double-broadcast

**Chosen**: Move `RunStartedEvent`, `StreamCompleteEvent`, `RunFailedEvent` handling into `OpenCodeSessionPoolIntegration._handle_event()`. Remove the separate `SessionStatusBridge` subscription. As part of this merge, remove the duplicate `RunStartedEvent → SessionStatusEvent(type="busy")` broadcast from the OpenCode event adapter (`event_processor.py` line ~185) to fix the existing double-broadcast bug. Also simplify `set_session_status()` in `session_pool_integration.py` to broadcast directly instead of going through `_status_bridges`.

**Rationale**: The bridge subscribes to EventBus separately, creating a second queue and replay buffer drain per session. Its events are a strict subset of what the main consumer receives. Handling them inline eliminates one subscription per session AND fixes the double-broadcast bug (busy status fires from both the bridge AND the adapter for every `RunStartedEvent`).

### Decision 3: Use `_skip_event_processing` flag for no-op consumers

**Chosen**: Add a `_skip_event_processing: bool = False` class variable to `ProtocolEventConsumerMixin`. When `True`, the consumer loop still subscribes to EventBus and detects `SpawnSessionStart` for child consumer lifecycle, but skips `_handle_event()` for all other events. AG-UI and OpenAI API servers set `_skip_event_processing = True`.

**Rationale**: The original proposal's "lightweight ChildConsumerManager" approach had a chicken-and-egg problem: `SpawnSessionStart` events are published to the parent session's EventBus topic. To detect them, you must subscribe to EventBus. The `_skip_event_processing` flag retains the subscription (needed for spawn detection) while eliminating the wasteful per-event no-op loop.

**Alternatives considered**:
- Remove mixin entirely + separate spawn detection mechanism → requires changes to TurnRunner/RunExecutor, too invasive
- Keep full subscription with no-op loop → wasteful overhead per event

### Decision 4: Rename `_active` to `_acp_sessions`, delegate lifecycle to SessionController

**Chosen**: Rename `ACPSessionManager._active: dict[str, ACPSession]` to `_acp_sessions: dict[str, ACPSession]`. This dict stores protocol-specific `ACPSession` runtime objects (MCP providers, command store, client references). Session lifecycle queries (existence, agent name, run status) are delegated to `SessionController.get_session()`. The `_acp_sessions` dict is indexed by `session_id` but does NOT duplicate lifecycle state.

**Rationale**: Oracle identified a fundamental type mismatch: `SessionController._sessions` stores `SessionState` (metadata), while `_active` stores `ACPSession` (runtime objects with subprocess connections, MCP state, command stores). You cannot replace one dict with the other. The corrected approach separates concerns: `SessionController` owns lifecycle, `_acp_sessions` owns protocol wrappers. The `_lock` on `ACPSessionManager` is removed since `SessionController._lock` covers lifecycle operations.

**Migration**:
- `acp_agent.py:558` (`first_session` for `list_sessions()`) → query `SessionController.list_sessions()` then resolve `ACPSession` from `_acp_sessions`
- `acp_agent.py:1110-1111` (pool swap) → iterate `_acp_sessions` for cleanup, delegate lifecycle clearing to `SessionController`

### Decision 5: Consolidate RunFailedEvent

**Chosen**: Remove `BaseAgent.RunFailedEvent` (inner class, signal-based). All run failure reporting goes through `events.py:RunFailedEvent` via `RunHandle.fail()` → EventBus.

**Rationale**: Zero external consumers of the signal-based variant. The EventBus-based `events.py:RunFailedEvent` is the canonical path, consumed by the main OpenCode consumer (`→ SessionErrorEvent`) and `ACPProtocolHandler` (`→ ACP client error notification`).

## Risks / Trade-offs

- **Risk**: Tests that create `ACPSession` without `SessionPool` will break → **Mitigation**: Update test fixtures to always provide a `SessionPool` (already the norm in integration tests). ~6 test functions affected.
- **Risk**: Removing `ACPSessionManager._lock` may cause races if `_acp_sessions` is accessed without `SessionController._lock` → **Mitigation**: All `_acp_sessions` mutation sites also touch `SessionController._sessions` which holds the lock
- **Trade-off**: `SessionStatusBridge` removal means status updates go through the main consumer loop → Acceptable; status updates are not latency-sensitive
- **Trade-off**: AG-UI/OpenAI API servers still subscribe to EventBus (needed for spawn detection) but skip event processing → Retains subscription overhead but eliminates per-event processing waste
- **Trade-off**: `_acp_sessions` still exists as a dict → This is the minimal viable change; a deeper refactoring to store `ACPSession` on `SessionState` would touch too many subsystems
