## Context

AgentPool's event pipeline currently has two independent paths from event production to protocol delivery:

```
Path 1 (PydanticAI): Model → RunExecutor.event_queue (asyncio.Queue) → consumer loop (100ms poll) → yield → SessionController → EventBus.publish()
Path 2 (Tools):      Tool → StreamEventEmitter._emit() → EventBus.publish() (direct)
```

Both paths dispatch events one-at-a-time with no coalescing. The `batch_stream_deltas()` processor in `processors.py:184` exists but is dead code — never wired into any production pipeline.

The recently completed `introduce-anyio-structured-concurrency` change migrated EventBus subscriber management to `anyio.create_memory_object_stream` with hybrid backpressure. This change builds on that foundation.

Lead agent and subagent each have their own independent session EventBus. `SubAgentEvent` wrapping happens at the protocol layer (via `ProtocolEventConsumerMixin` multi-session subscription), not through a parent session's EventBus. Each session's coalescing is fully independent.

## Goals / Non-Goals

**Goals:**
- Add type-change-triggered event coalescing at EventBus layer — merge consecutive same-type `PartDeltaEvent` and `ToolCallProgressEvent`
- Unify dual-path architecture — all events flow through a single `EventBus.publish()` path
- Simplify RunExecutor by removing intermediate `asyncio.Queue` and consumer poll loop
- Remove conditional routing in `StreamEventEmitter._emit()`
- Preserve `run_stream()` public API via EventBus subscription wrapper
- Maintain per-session isolation — zero cross-session interference

**Non-Goals:**
- Cross-type event batching (e.g., merging `PartDeltaEvent` with `ToolCallProgressEvent`)
- Introducing new event types (e.g., `EventBatch`)
- Time-window-based coalescing, background flush tasks, or timers
- Modifying protocol converters (ACP, OpenCode, AG-UI, OpenAI API)
- Replacing `asyncio.Queue` in non-EventBus contexts (e.g., `GraphStreamingAdapter`)

## Decisions

### Decision 1: Type-change trigger + buffer cap (no timers)

**Chosen**: Flush buffered events when the merge key changes, buffer reaches cap (default 20, configurable via `EventBus(max_coalesce_buffer=N)`), or an immediate lifecycle event arrives. No time windows, no background flush task, no `time.monotonic()` checks.

**Rationale**: The natural event lifecycle provides sufficient flush triggers — `StreamCompleteEvent` flushes any residual buffer at stream end, type changes (text → thinking → tool_call) flush between phases, and the buffer cap prevents unbounded latency on long same-type sequences. The cap is configurable to allow tuning per deployment.

### Decision 2: EventBus-level coalescing (not RunExecutor or StreamProcessor)

**Chosen**: Coalescing happens inside `EventBus.publish()`, the single convergence point for all events.

**Rationale**: All event sources (PydanticAI, tools, deferred bridge) converge at EventBus. Coalescing here provides automatic full coverage with zero producer changes. Per-session isolation is natural — `_buffers` and `_last_keys` are keyed by `session_id`.

**Alternatives considered**:
- *RunExecutor drain-then-merge*: Only covers PydanticAI events, misses tool events, preserves dual-path complexity.
- *StreamProcessor pipeline*: Opt-in only, adds async generator overhead, misses direct-publish tool events.

### Decision 3: RunExecutor fire-and-forget with error event

**Chosen**: `execute()` becomes an async function that publishes events directly to EventBus. `RunErrorEvent` is published in the `except` block before exception propagation. `StreamCompleteEvent(cancelled=True)` for pre-response cancellation.

**Rationale**: Eliminates the intermediate `asyncio.Queue` and 100ms poll loop. Publishing `RunErrorEvent` before exception propagation ensures consumers receive the error notification even if TaskGroup teardown closes subscriptions.

### Decision 4: `run_stream()` as EventBus subscription wrapper (with SessionPool guard)

**Chosen**: `Agent.run_stream()` is overridden to subscribe to EventBus, start execution, and yield events — BUT only when no SessionPool is available (standalone mode). When a SessionPool IS available, the existing Path A delegation (`SessionPool.run_stream()`) is preserved.

```python
class Agent:
    async def run_stream(self, prompt, session_id=None, ...):
        # Path A: SessionPool delegation (existing behavior, unchanged)
        if self.agent_pool and self.agent_pool.session_pool:
            async for event in self.agent_pool.session_pool.run_stream(...):
                yield event
            return
        
        # Path B: Standalone — create local EventBus, subscribe, execute, yield
        session_id = session_id or str(uuid4())
        local_bus = EventBus()
        stream = await local_bus.subscribe(session_id, scope="session")
        async with anyio.create_task_group() as tg:
            tg.start_soon(self._executor.execute,
                session_id=session_id, event_bus=local_bus, ...)
            async with stream:
                async for envelope in stream:
                    event = envelope.event
                    yield event
                    if isinstance(event, (StreamCompleteEvent, RunErrorEvent)):
                        break
            tg.cancel_scope.cancel()
```

**Rationale**: Standalone mode creates a local EventBus scoped to the stream lifetime — no need for Agent or RunExecutor to own infrastructure. The local EventBus is created, used, and discarded with the stream. Pool-managed agents (Path A) use the pool's shared EventBus via SessionPool delegation — this path is unchanged.

### Decision 5: Non-native agent event path

**Chosen**: Ensure `run_ctx.event_bus` is always set for all agent types in the SessionController path (already done at `core.py:1552`). Remove the conditional `_consume_event_queue()` task in `_run_turn_unlocked()` for all agent types after unification. For the standalone path (`BaseAgent.run_stream()` Path B without SessionPool), set `run_ctx.event_bus` to the agent's EventBus if available, preserving the `_consume_event_queue` fallback only when no EventBus exists.

**Rationale**: When `run_ctx.event_bus` is set, `StreamEventEmitter._emit()` uses the EventBus path exclusively and never falls through to `run_ctx.event_queue`. The bridge task becomes unnecessary. For standalone non-native agents, the fallback to `run_ctx.event_queue` is preserved as a safety net. Three locations have the same dual-routing pattern and must all be updated:
1. `StreamEventEmitter._emit()` (`event_emitter.py:354`)
2. `AgentContext.report_progress()` (`context.py:167`)
3. `_emit_deferred_event()` (`deferred_bridge.py:38`)

**Alternatives considered**: Simplifying only `_emit()` while leaving `report_progress()` and `deferred_bridge.py` with the old pattern. Rejected — would create architectural inconsistency and leave dead code paths.

### Decision 6: Coalescing merge key

Events are classified into two categories and grouped by merge key:

| Event | Classification | Merge Key |
|-------|---------------|-----------|
| `PartDeltaEvent` (text) | Batchable | `("delta_text", "")` |
| `PartDeltaEvent` (thinking) | Batchable | `("delta_thinking", "")` |
| `PartDeltaEvent` (tool_call) | Batchable | `("delta_tool_call", tool_call_id)` |
| `ToolCallProgressEvent` | Batchable | `("progress", f"{tool_call_id}:{status}")` |
| `PlanUpdateEvent` | Batchable (last-wins) | `("plan", "")` |
| `ToolResultMetadataEvent` | Passthrough | N/A (dispatched individually, triggers buffer drain) |
| `SubAgentEvent`, `CustomEvent` | Passthrough | N/A (dispatched individually, triggers buffer drain) |
| All lifecycle events | Immediate | N/A (bypass buffer) |

`itertools.groupby` (C implementation) provides zero-overhead grouping of consecutive same-key events.

### Decision 7: Lock strategy

**Chosen**: Single `anyio.Lock` (`_buf_lock`) protects only dict lookup + list append + key compare. Merge and send happen outside the lock.

**Why a lock is needed**: `_buffers` and `_last_keys` are shared dicts accessed by concurrent `publish()` calls. Before path unification (Phase 1), dual-path means `RunExecutor` and `StreamEventEmitter` concurrently publish to the same session. After unification, `close_session()` can still race with `publish()` for the same session. `anyio.Lock` is chosen over `asyncio.Lock` for consistency with the existing `_lock`. In the uncontended case (single publisher per session), lock overhead is negligible — one coroutine yield per acquire.

**Lock hierarchy**: `_buf_lock` (new, for coalescing buffer) → released → `_lock` (existing, for subscriber management inside `_send()`). The two locks are NEVER nested. `_buf_lock` is acquired briefly, then released before `_send()` acquires `_lock`. This prevents deadlocks. Documented as a comment block in `EventBus`:

```python
# Lock hierarchy:
#   _buf_lock  — guards _buffers, _last_keys (coalescing state)
#   _lock      — guards _subscribers, _stream_pairs, _replay_buffers (subscriber state)
# NEVER nest: _buf_lock → _lock is correct; _lock → _buf_lock is a DEADLOCK.
```

```python
async with self._buf_lock:
    buf = self._buffers.setdefault(session_id, [])
    last_key = self._last_keys.get(session_id)
    if last_key != key or len(buf) >= self._max_buffer:
        prev_batch = buf.copy()
        buf.clear()
        buf.append(envelope)
        self._last_keys[session_id] = key
        should_flush = True
    else:
        buf.append(envelope)

# _buf_lock released here. _send() acquires _lock internally.
if should_flush and prev_batch:
    for merged in _merge_envelopes(prev_batch):
        await self._send(session_id, merged)
```

`_drain_buffer()` implements atomic pop-under-lock — idempotent, safe for concurrent callers (second caller gets empty list).

### Decision 8: Coalescing buffer cleanup on session close

**Chosen**: `EventBus.close_session()` drains and flushes any pending coalescing buffer before closing subscribers. This prevents memory leaks and ensures no events are silently lost when a session closes.

```python
async def close_session(self, session_id: str) -> None:
    # Drain coalescing buffer first
    await self._drain_buffer(session_id)
    # Existing cleanup: replay buffers + subscribers
    self._replay_buffers.pop(session_id, None)
    ...
```

### Decision 9: CancelScope safety transition

**Chosen**: The current RunExecutor uses a background task + consumer queue pattern to provide CancelScope safety ("when the consumer is cancelled, the background task gets a shielded cleanup window"). The fire-and-forget pattern intentionally removes this guarantee because:

1. The consumer (`run_stream()` wrapper) now subscribes to EventBus rather than driving execution directly. Cancelling the consumer cancels the subscription, not the execution.
2. `RunExecutor.execute()` runs inside `anyio.create_task_group()` — if the task group is cancelled, all child tasks (including the PydanticAI iteration) are cancelled, which is the desired behavior.
3. `PendingMessageDrainCapability` cleanup is handled by the PydanticAI framework itself (it's an `after_node_run` capability hook), not by the RunExecutor's consumer loop.

The `RunErrorEvent` published in the `except` block ensures consumers receive error notification before the task group exits.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| Long same-type sequence causes unbounded buffering | Buffer cap (20 events) triggers flush even without type change. For LLM text at 30 tok/s, worst latency ~0.7s |
| `RunExecutor` fire-and-forget loses error propagation | `RunErrorEvent` published in `except` block BEFORE exception propagates; TaskGroup teardown does not close EventBus subscriptions |
| Non-native agent event path broken | Prerequisite: `run_ctx.event_bus` always set (already done); `_emit()` uses EventBus exclusively; standalone path preserves `_consume_event_queue` fallback |
| Concurrent tool progress events not merged | Type-change approach cannot merge interleaved progress from different tool_call_ids. Gracefully degrades to one-at-a-time dispatch — rare scenario, low impact |
| Test surface area (~20+ files) | Phased implementation; each phase independently revertible |
| `ObjectReceiveStream` iteration requires `async with` | Wrapper uses `async with stream:` for proper resource cleanup |
| Buffer leaks on session close | `close_session()` drains coalescing buffer before closing subscribers (Decision 8) |
| Lock deadlock between `_buf_lock` and `_lock` | Lock hierarchy documented (Decision 7): `_buf_lock` → `_lock`, never nested |
| Replay buffer delivers stale events on session reuse | `run_stream()` clears replay buffer for fresh session_id before subscribing (Decision 4) |
| `Agent.run_stream()` override bypasses SessionPool | Override preserves Path A delegation when SessionPool is available (Decision 4) |
| `EventEnvelope` is frozen — merge must create new envelopes | `_rebind()` helper creates new `EventEnvelope` with merged event, preserving `source_session_id` |

### Merge Function Signatures

```python
def _rebind(template: EventEnvelope, new_event: Any) -> EventEnvelope:
    """Create new EventEnvelope with merged event, preserving source_session_id."""
    return EventEnvelope(source_session_id=template.source_session_id, event=new_event)

def _merge_text_deltas(events: list[PartDeltaEvent]) -> PartDeltaEvent:
    """Concatenate TextPartDelta content_delta strings. Uses first event's index."""

def _merge_thinking_deltas(events: list[PartDeltaEvent]) -> PartDeltaEvent:
    """Concatenate ThinkingPartDelta content_delta strings. Uses first event's index."""

def _merge_tool_call_deltas(events: list[PartDeltaEvent]) -> PartDeltaEvent:
    """Concatenate ToolCallPartDelta args_delta strings. Uses first event's index and tool_call_id."""

def _merge_progress_events(events: list[ToolCallProgressEvent]) -> ToolCallProgressEvent:
    """Concatenate items sequences. Uses last event's title, status, replace_content, tool_name.
    Items with duplicate TerminalContentItem.terminal_id are kept (consumer handles dedup)."""
```

## Resolved Questions

| Question | Decision |
|----------|----------|
| Buffer cap default (20) | Configurable via `EventBus(max_coalesce_buffer=N)` |
| GraphStreamingAdapter migration | Excluded from this RFC; follow-up change |
| AgentRunContext.event_queue removal | Removed (task 4.3) |
| ToolResultMetadataEvent merge strategy | Passthrough — dispatched individually, triggers buffer drain |
| Standalone run_stream() EventBus creation | `run_stream()` Path B creates local EventBus scoped to stream lifetime |
| _buf_lock necessity | Kept — negligible overhead, prevents close_session/publish race |
