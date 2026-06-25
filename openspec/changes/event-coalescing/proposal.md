## Why

AgentPool's event pipeline has two independent paths (PydanticAI model events via RunExecutor queue, tool progress events via StreamEventEmitter direct publish), each dispatching events one-at-a-time through EventBus with no coalescing. LLM streaming produces 50+ `publish()` calls per second per session, creating unnecessary lock contention, subscriber iteration overhead, and architectural complexity. This change unifies the paths and adds type-change-triggered coalescing at the EventBus layer — no timers, no background tasks.

## What Changes

- **EventBus coalescing**: Add per-session buffer with type-change trigger. Consecutive same-type events (text deltas, thinking deltas, tool progress) are merged before dispatch. Flush triggers: merge key change, buffer cap (20 events), or immediate lifecycle event.
- **Unified event path**: Migrate PydanticAI events from RunExecutor's intermediate `asyncio.Queue` + consumer poll loop to direct `EventBus.publish()`. Remove the dual-path conditional in `StreamEventEmitter._emit()`.
- **RunExecutor simplification**: Remove `event_queue`, consumer poll loop, and async generator pattern. `execute()` becomes a fire-and-forget async function publishing directly to EventBus. Error propagation via `RunErrorEvent` published before exception.
- **SessionController simplification**: Remove `async for event in agent.run_stream()` yield/publish loop. Non-native agent `_consume_event_queue()` task removed after ensuring `run_ctx.event_bus` always set.
- **`run_stream()` backward compat**: Reimplement as EventBus subscription wrapper — subscribes before starting run, yields `envelope.event`, exits on `StreamCompleteEvent` or `RunErrorEvent`.
- **Remove dead code**: `batch_stream_deltas()` processor and its unused config flag.

## Capabilities

### New Capabilities
- `event-coalescing`: Type-change-triggered event coalescing at EventBus layer. Merges consecutive same-type `PartDeltaEvent` and `ToolCallProgressEvent` instances. Per-session isolation via independent buffers. Buffer cap prevents unbounded latency.

### Modified Capabilities
<!-- No existing spec requirements are changing. Coalescing is an optimization/architectural simplification, not a requirement change. -->

## Impact

- `src/agentpool/orchestrator/core.py` — EventBus gains `_buffers`, `_last_keys`, `_buf_lock`; `publish()` gains coalescing logic; `_drain_buffer()`, `_send()` extracted
- `src/agentpool/orchestrator/run_executor.py` — Remove `event_queue`, consumer poll loop; `execute()` from async generator to async function; add `event_bus` dependency; publish events directly
- `src/agentpool/agents/events/event_emitter.py` — `_emit()` simplified: always use EventBus path (remove conditional `run_ctx.event_queue` fallback)
- `src/agentpool/agents/events/processors.py` — `batch_stream_deltas()` removed (replaced by EventBus coalescing)
- `src/agentpool/agents/agent.py` — `run_stream()` reimplemented as EventBus subscription wrapper
- ~20 test files — RunExecutor generator → fire-and-forget pattern changes; EventBus coalescing tests added
