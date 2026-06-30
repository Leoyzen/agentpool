## ADDED Requirements

### Event ordering preserved through direct delegation

Event ordering SHALL be preserved through direct `RunHandle.start()` delegation — events flow from `turn.execute()` through `RunHandle.start()` to the caller without intermediary queues or `drain_and_merge` coalescing.

**Scenarios:**

1. **WHEN** a turn executes, **THEN** `RunStartedEvent` SHALL be the first event yielded — it is yielded by `turn.execute()` first and flows through `RunHandle.start()` directly.

2. **WHEN** a turn completes or errors, **THEN** `StreamCompleteEvent` or `RunErrorEvent` SHALL be the last event yielded — the terminal event breaks the consumer loop.

3. **WHEN** consecutive `run_stream()` calls are made, **THEN** each call SHALL yield events in correct order — the first call's `RunHandle` is fully drained (via `gen.aclose()` in `finally`) before the second call begins.

4. **WHEN** `drain_and_merge` coalescing is bypassed (direct yield from `turn.execute()`), **THEN** no test SHALL depend on coalesced events in standalone mode — events are yielded as produced by `turn.execute()`, with lower latency and no coalescing artifacts.
