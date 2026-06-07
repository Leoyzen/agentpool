## Why

The business layer maintains **three distinct event routing paths** for subagent events, creating redundancy and inconsistency. The protocol layer already solved this problem elegantly with `_event_consumer_loop()` using `scope="descendants"`, but the business layer still carries legacy manual event handling code from before that solution existed.

This cleanup eliminates the redundant paths and makes the business layer agnostic to event routing — agents simply run, and the protocol layer handles all event forwarding uniformly.

## What Changes

- **Remove manual EventBus subscription** from `subagent_tools.py` async mode (`_consume_events_to_fs()` and its EventBus subscribe/unsubscribe logic)
- **Remove manual event emission** from `workers.py` sync mode — stop emitting events via `ctx.events.emit_event()` and let SessionPool/TurnRunner handle all event routing
- **Keep `run_stream()` for sync mode** — workers need blocking execution that returns final result. Use `session_pool.run_stream()` instead of direct `worker.run_stream()` to ensure events enter EventBus
- **Use `receive_request()` for async mode** — background tasks are fire-and-forget, this is the correct API
- **Preserve filesystem output** for async mode via explicit post-run write (not side-effect of event consumption)
- **Delete legacy dual-path code** that branches between SessionPool and non-SessionPool execution in `subagent_tools.py`
- **Update tests** to verify events still reach the frontend through the unified protocol-layer path

## Capabilities

### New Capabilities
- None

### Modified Capabilities
- `unified-event-routing`: Clarify that business layer MUST NOT perform manual event routing. All event forwarding is the responsibility of the protocol layer via EventBus `scope="descendants"` subscription.

## Impact

- `src/agentpool_toolsets/builtin/subagent_tools.py` — Remove async mode manual EventBus subscription and filesystem consumer; add explicit post-run filesystem write
- `src/agentpool_toolsets/builtin/workers.py` — Remove `ctx.events.emit_event()` calls; switch to `session_pool.run_stream()` for sync mode
- `src/agentpool_commands/pool.py` — Remove manual `SubAgentEvent` wrapping and `emit_event()` calls (same pattern as workers.py)
- `tests/` — Update subagent tool tests, worker tests, and command tests to assert EventBus-based routing instead of local event emission
- No API changes; purely internal refactoring
- Frontend behavior unchanged (events still arrive via same SSE stream)