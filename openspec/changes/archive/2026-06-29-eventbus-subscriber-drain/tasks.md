## 1. Verify Merge Helpers Are Module-Level

- [x] 1.1 Verify `_is_immediate()`, `_merge_key()`, `_merge_text_deltas()`, `_merge_thinking_deltas()`, `_merge_tool_call_deltas()`, `_merge_progress_events()`, `_merge_envelopes()` are already module-level functions in `core.py` (no relocation needed)
- [x] 1.2 Verify all internal EventBus references use the module-level functions (not `self._merge_*` calls)
- [x] 1.3 Run existing tests to confirm no behavior change

## 2. Implement drain_and_merge() Utility

- [x] 2.1 Create `drain_and_merge(stream)` async generator function in `core.py` that: calls `await stream.receive()` (handles `EndOfStream` → terminates), then loops `stream.receive_nowait()` collecting events until `WouldBlock` (normal end) or `EndOfStream` (stream closed mid-drain → process batch then terminate), merges via `merge_envelopes()`, yields merged envelopes
- [x] 2.2 Ensure `EndOfStream` from `receive_nowait()` processes the non-empty batch before terminating — not just empty list
- [x] 2.3 Ensure `EndOfStream` from initial `receive()` terminates immediately with no batch
- [x] 2.4 Add unit tests for `drain_and_merge()`: consecutive same-type events merge, type-change creates separate batches, `WouldBlock` ends drain, `EndOfStream` mid-drain processes batch then terminates, `EndOfStream` on initial receive terminates, empty stream returns nothing
- [x] 2.5 Verify that `SpawnSessionStart` events in a drain batch are still routed to `_on_spawn_session_start()` before `_handle_event()`

## 3. Update ProtocolEventConsumerMixin Consumer Loop

- [x] 3.1 Replace `async for envelope in stream:` in `_event_consumer_loop()` with `drain_and_merge(stream)` — iterate over merged envelopes, dispatch each to `_on_spawn_session_start()` and `_handle_event()`
- [x] 3.2 Ensure `_skip_event_processing` logic still works — drain occurs but `_handle_event()` is skipped when flag is True
- [x] 3.3 Preserve the `finally` block cleanup (done_event, scope, group, stream unsubscribe, `_after_consumer_loop` hook)
- [x] 3.4 Handle `ConsumerShutdown` exception from `_handle_event()` — break the consumer loop as before
- [x] 3.5 Update the `hasattr(envelope, "event")` fallback for raw events in test contexts

## 4. Update Standalone run_stream() Path B

- [x] 4.1 Update `base_agent.py` standalone `run_stream()` to use `drain_and_merge(stream)` instead of `async for envelope in stream:`
- [x] 4.2 Verify coalescing behavior matches ProtocolEventConsumerMixin

## 5. Update serve_mcp.py Consumer

- [x] 5.1 Update `serve_mcp.py` consumer to use `drain_and_merge(stream)` (or explicitly document uncoalesced events if only `StreamCompleteEvent` is consumed and coalescing is irrelevant)
- [x] 5.2 Verify MCP server still functions correctly

## 6. Remove Publish-Side Coalescing from EventBus

- [x] 6.1 Simplify `publish()` to: wrap in `EventEnvelope`, drop `PartDeltaEvent` with `delta=None`, call `_send()` directly
- [x] 6.2 Remove `_drain_buffer()` method from EventBus
- [x] 6.3 Remove coalescing state from `__init__`: `_buffers`, `_last_keys`, `_buf_lock`, `_max_buffer`
- [x] 6.4 Remove `max_coalesce_buffer` parameter from `__init__` signature
- [x] 6.5 Remove the `"Coalescing buffer cap reached, flushing"` warning log
- [x] 6.6 Update `close_session()` to remove the `_drain_buffer()` call (no buffer to drain)

## 7. Update Tests

- [x] 7.1 Move coalescing tests from `publish()`-side assertions to consumer-side drain assertions — publish events, then consume via `drain_and_merge()` and verify merged output
- [x] 7.2 Remove tests that verify cap=20 flush behavior (no cap exists anymore)
- [x] 7.3 Remove `max_coalesce_buffer=20` from all test EventBus constructor calls (~14 files)
- [x] 7.4 Add test: 100 consecutive text deltas published, subscriber drains all in one batch, receives single merged event, no warning logged
- [x] 7.5 Add test: lifecycle event in drain batch delivered individually alongside merged batchable events
- [x] 7.6 Add test: passthrough event (SubAgentEvent) in drain batch delivered individually alongside merged batchable events
- [x] 7.7 Add test: per-session drain isolation — two sessions' consumers drain independently
- [x] 7.8 Add test: merge helpers callable as module-level functions without EventBus instance
- [x] 7.9 Add test: `PlanUpdateEvent` last-wins merge behavior in drain batch
- [x] 7.10 Add test: `EndOfStream` from `receive_nowait()` mid-drain processes batch then terminates
- [x] 7.11 Add test: `EndOfStream` from initial `receive()` terminates immediately
- [x] 7.12 Verify existing event converter tests (ACP, OpenCode) still pass with subscriber-side coalescing

## 8. Update Configuration and Documentation

- [x] 8.1 Remove `max_coalesce_buffer` from any YAML config schema or documentation that references it
- [x] 8.2 Update EventBus docstring to reflect subscriber-side coalescing architecture
- [x] 8.3 Update `ProtocolEventConsumerMixin` docstring to mention drain-and-merge responsibility
- [x] 8.4 Document `drain_and_merge()` utility function with docstring and usage examples
- [x] 8.5 Run `uv run pytest` full suite to verify no regressions
- [x] 8.6 Run `uv run ruff check src/` and `uv run --no-group docs mypy src/` on changed files
