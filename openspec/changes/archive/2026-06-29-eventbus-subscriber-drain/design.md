## Context

The EventBus (`src/agentpool/orchestrator/core.py`) currently coalesces consecutive same-type events at publish time. The `publish()` method buffers `PartDeltaEvent` and `ToolCallProgressEvent` instances per session, merging them on type-change, cap=20, or lifecycle event triggers. This was introduced in the archived change `2026-06-26-event-coalescing`, which explicitly rejected timer-based flushing in favor of type-change + cap triggers.

The subscriber-side consumer loop in `ProtocolEventConsumerMixin._event_consumer_loop()` (in `src/agentpool_server/mixins.py`) uses a simple `async for envelope in stream:` pattern — it receives pre-coalesced events one at a time.

The anyio memory object stream (`anyio.create_memory_object_stream`) already provides the infrastructure for subscriber-side drain: `receive()` blocks for the first event, then `receive_nowait()` can drain all immediately-available events until `WouldBlock` signals no more are ready.

## Goals / Non-Goals

**Goals:**
- Move coalescing from publish-side to subscriber-side — events enter subscriber queues immediately
- Exploit the async event loop's natural scheduling gap as the batch boundary
- Eliminate publish-side buffer state (`_buffers`, `_last_keys`, `_buf_lock`, `_max_buffer`)
- Eliminate the cap=20 warning that fires frequently during long text generation
- Preserve existing merge semantics (same merge keys, same merge helpers)
- Maintain per-session isolation
- Zero-detectable latency increase for subscribers

**Non-Goals:**
- Changing the merge algorithm itself (same merge keys, same concatenation logic)
- Modifying protocol converters (ACP, OpenCode, AG-UI, OpenAI API)
- Introducing new event types
- Adding timer-based or time-window coalescing
- Modifying the replay buffer mechanism
- Changing the `_send()` backpressure strategy (hybrid timeout → drop)

## Decisions

### Decision 1: Subscriber-side drain with `receive_nowait()` loop

**Chosen**: Replace the `async for envelope in stream:` consumer loop with a drain pattern:
1. `await stream.receive()` — blocks for the first event (natural wait point). If `EndOfStream` is raised, the stream is closed with no items — terminate the consumer loop.
2. Loop `stream.receive_nowait()` collecting all immediately-available events
3. `WouldBlock` exception ends the drain — all queued events collected, stream still open
4. `EndOfStream` exception from `receive_nowait()` ends the drain — all queued events collected, stream closed. Process the non-empty batch, then terminate the consumer loop.
5. Merge the drained batch using existing merge helpers
6. Deliver merged events to `_handle_event()` one by one

**Rationale**: The anyio memory object stream's `receive_nowait()` provides the exact "is there more right now?" semantics we need. When the producer hits an `await` point (e.g., awaiting LLM API response), the event loop schedules the subscriber. The subscriber drains everything the producer has sent so far in one batch. This is the natural async time gap the previous design searched for but couldn't find without timers.

**Alternatives considered**:
- *Keep publish-side, lower cap to 5*: Reduces latency but doesn't eliminate the fundamental "buffer-first" problem. Still has lock contention and state management overhead.
- *Deferred flush callback*: Schedule a `call_soon` to flush after current event loop iteration. Works but adds state management for the callback itself, and `call_soon` semantics differ across event loops.
- *`anyio.lowlevel.checkpoint()` in publish*: Yields control but doesn't trigger subscriber drain — the subscriber might not be scheduled next.
- *Timer-based (rejected in original design)*: Still rejected. Adds timer management, race conditions, and arbitrary latency floor.

### Decision 2: Merge helpers are already module-level functions

**Chosen**: The merge helpers (`_merge_text_deltas`, `_merge_thinking_deltas`, `_merge_tool_call_deltas`, `_merge_progress_events`, `_merge_envelopes`, `_merge_key`, `_is_immediate`) are already module-level functions in `core.py`. No relocation is needed. The subscriber-side drain code imports and calls them directly.

**Rationale**: The merge logic is pure — it operates on lists of `EventEnvelope` objects with no dependency on EventBus state. They are already standalone functions, testable without an EventBus instance.

### Decision 3: `publish()` becomes a thin wrapper around `_send()`

**Chosen**: `publish()` drops all coalescing logic and becomes:
1. Wrap event in `EventEnvelope`
2. Drop `PartDeltaEvent` with `delta=None` (preserve existing behavior)
3. Call `_send()` directly

**Rationale**: Without coalescing, `publish()` is just "send to all matching subscribers." The `_send()` method already handles replay buffer, subscriber matching, backpressure, and dead stream cleanup. No reason to duplicate or wrap it further.

### Decision 4: Reusable `drain_and_merge()` utility, used by all consumers

**Chosen**: The drain-and-merge logic is implemented as a reusable `drain_and_merge(stream)` async utility function (in `core.py` or a shared module). `ProtocolEventConsumerMixin._event_consumer_loop()` calls it, and so do the standalone `run_stream()` Path B in `base_agent.py` and the `serve_mcp.py` consumer. All EventBus consumers use the same coalescing behavior.

**Rationale**: There are three existing consumer paths that bypass `ProtocolEventConsumerMixin`:
1. `ProtocolEventConsumerMixin._event_consumer_loop()` — ACP, OpenCode, AG-UI, OpenAI API servers
2. `base_agent.py` standalone `run_stream()` Path B — used when no SessionPool is available
3. `serve_mcp.py` consumer — MCP server's stream completion handler

Without a shared utility, paths 2 and 3 would silently lose coalescing (behavioral regression). A reusable function ensures all consumers get consistent drain-and-merge behavior. The function is trivially composable: `async for merged_batch in drain_and_merge(stream): ...`.

**Alternative considered**: *Drain in EventBus.subscribe() returning a coalescing wrapper stream*: Would hide coalescing from consumers but adds a wrapper layer and makes it harder to debug raw event flow. Also complicates the `subscribe()` API contract.

### Decision 5: Merge happens before `_handle_event()`, not after

**Chosen**: The consumer loop drains all available events, merges them, then calls `_handle_event()` for each merged envelope. `_handle_event()` receives pre-merged events, same as today.

**Rationale**: Protocol converters (`_handle_event` implementations) already expect pre-merged events from the publish-side coalescing. Keeping the same contract means zero changes to ACP event converters, OpenCode event adapters, etc.

## Risks / Trade-offs

- **[Merge happens per-consumer, not globally]** → If multiple subscribers exist for the same session (currently discouraged by `eventbus-single-subscriber-per-session` spec), each merges independently. This is actually correct — each subscriber should see its own coalesced view. No mitigation needed.

- **[Consumer must be awake to merge]** → If the consumer is slow or blocked, events accumulate in the anyio memory stream buffer (bounded by `max_queue_size`). This is the same backpressure behavior as today — `_send()` already handles `WouldBlock` by dropping subscribers. No new risk.

- **[Merge logic duplication if multiple consumer types]** → Three existing consumer paths (ProtocolEventConsumerMixin, standalone run_stream() Path B, serve_mcp.py) all need coalescing. Mitigated by Decision 4: a shared `drain_and_merge()` utility function that all consumers call.

- **[EndOfStream during drain]** → `receive_nowait()` can raise `EndOfStream` mid-drain when the send stream is closed but items remain in the buffer. The drain helper must process the non-empty batch before signaling termination. Handled in Decision 1 step 4.

- **[`receive_nowait()` may not drain all events in one batch]** → If the producer is extremely fast (all events already queued), `receive_nowait()` drains them all. If the producer sends events in a tight loop without `await`, they may span multiple event loop iterations. This is actually desirable — each iteration's batch is a natural coalescing unit.

- **[Behavioral change: events delivered in potentially larger batches]** → Subscribers may receive larger merged events than before (cap=20 limited batch size). Protocol converters must handle larger text deltas. This is already the case for non-coalesced events, so no change needed.

## Migration Plan

1. **Verify merge helpers** are module-level functions (already done in codebase — verify, don't relocate)
2. **Implement `drain_and_merge(stream)` utility** — async generator that drains via `receive()` + `receive_nowait()` loop, handles `WouldBlock` and `EndOfStream`, merges via existing helpers, yields merged envelopes
3. **Update `ProtocolEventConsumerMixin._event_consumer_loop()`** to use `drain_and_merge()` (additive, can coexist with publish-side coalescing temporarily)
4. **Update `base_agent.py` Path B `run_stream()`** to use `drain_and_merge()` instead of `async for envelope in stream:`
5. **Update `serve_mcp.py` consumer** to use `drain_and_merge()` (or explicitly document uncoalesced events if functionally harmless)
6. **Remove publish-side coalescing** from `publish()` (switch to direct `_send()`)
7. **Remove coalescing state** from EventBus `__init__` (`_buffers`, `_last_keys`, `_buf_lock`, `_max_buffer`, `max_coalesce_buffer` parameter)
8. **Update tests** — coalescing tests move from testing `publish()` behavior to testing `drain_and_merge()` behavior
9. **Remove cap warning** — the `"Coalescing buffer cap reached, flushing"` warning is gone with the buffer

**Rollback**: Steps 6-7 can be reverted independently if subscriber-side drain proves problematic. Steps 2-5 are additive and don't affect existing behavior (with publish-side coalescing still active, drain-and-merge is a no-op since events arrive pre-coalesced).

## Open Questions

- Should `drain_and_merge()` be an async generator (`async for merged in drain_and_merge(stream)`) or return `(list[EventEnvelope], bool terminated)` tuples? **Recommendation**: Async generator — cleaner consumer code, natural iteration over merged batches.
