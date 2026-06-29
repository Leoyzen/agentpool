## Why

The current EventBus coalescing uses a publish-side buffer that batches consecutive same-type events and flushes on type-change, cap=20, or lifecycle events. This "buffer-first, flush-when-forced" design causes subscriber latency (19-event bursts invisible until forced flush), frequent cap warnings during long text generation, and unnecessary state management (`_buffers`, `_last_keys`, `_buf_lock`). The async event loop already provides a natural batch boundary — the scheduling gap between producer `await` points. We should exploit this instead of reinventing it.

## What Changes

- **BREAKING**: Remove publish-side coalescing buffer from `EventBus.publish()` — eliminate `_buffers`, `_last_keys`, `_buf_lock`, `_max_buffer`, `max_coalesce_buffer` parameter, and the cap=20 warning
- **BREAKING**: Remove the coalescing logic from `EventBus.publish()` that calls `_is_immediate()`, `_merge_key()`, and `_merge_envelopes()` — these module-level functions remain, but `publish()` no longer invokes them
- **BREAKING**: Standalone `run_stream()` Path B (`base_agent.py`) and `serve_mcp.py` consumer lose publish-side coalescing — these paths bypass `ProtocolEventConsumerMixin` and must be updated to use the new `drain_and_merge()` utility
- Add subscriber-side drain logic: `publish()` sends events directly to subscriber queues via `send_nowait()`; subscribers drain all queued events on each wake using `receive_nowait()` until `WouldBlock`
- Merge helpers (`_merge_text_deltas`, `_merge_thinking_deltas`, `_merge_tool_call_deltas`, `_merge_progress_events`) are already module-level functions — they will be reused by the subscriber-side drain path
- Subscribers call `receive()` (blocks for first event), then loop `receive_nowait()` to drain all immediately-available events, merge them, and deliver as one batch
- `WouldBlock` exception from `receive_nowait()` signals the natural async time gap — all events drained, ready to deliver
- `EndOfStream` from `receive_nowait()` (stream closed mid-drain) processes the remaining batch before terminating
- Provide a reusable `drain_and_merge(stream)` utility function that any consumer can use, not just `ProtocolEventConsumerMixin`
- Remove `max_coalesce_buffer` configuration parameter from EventBus constructor

## Capabilities

### New Capabilities

- `event-coalescing`: Subscriber-side drain coalescing for EventBus events — defines how consecutive same-type events are merged at consumption time using queue drain semantics

### Modified Capabilities

- `eventbus-single-subscriber-per-session`: The single consumer per session now performs drain-based coalescing instead of receiving pre-coalesced events from the publisher

## Impact

- **`src/agentpool/orchestrator/core.py`**: Major refactor of EventBus — remove publish-side buffer state and methods, simplify `publish()` to thin `_send()` wrapper
- **`src/agentpool_server/mixins.py`**: `ProtocolEventConsumerMixin` consumer loop updated to use drain-and-merge pattern
- **`src/agentpool/agents/base_agent.py`**: Standalone `run_stream()` Path B updated to use `drain_and_merge()` utility
- **`src/agentpool_cli/serve_mcp.py`**: Consumer loop updated to use `drain_and_merge()` utility (or explicitly accept uncoalesced events if functionally harmless)
- **Event merge helpers**: Already module-level functions in `core.py` — reused by subscriber-side drain, no relocation needed
- **Configuration**: `max_coalesce_buffer` parameter removed from EventBus constructor and any YAML config that exposes it
- **Tests**: All coalescing tests updated to verify subscriber-side behavior instead of publish-side buffering
- **Performance**: Eliminates per-publish lock contention (`_buf_lock`), reduces memory (no `_buffers` dict), improves subscriber latency from O(cap) to 0ms
