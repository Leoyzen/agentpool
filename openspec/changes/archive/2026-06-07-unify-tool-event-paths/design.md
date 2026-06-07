## Context

### Current Problem

In SessionPool mode (`run_ctx.event_bus is not None`), `NativeAgent._run_agentlet_core()` processes pydantic-ai stream events through two divergent paths:

**Path A** (local queue): `FunctionToolCallEvent` and other stream events flow into a local `asyncio.Queue`, then through `_stream_events()` which yields them. `TurnRunner` publishes each yielded event to `EventBus`.

**Path B** (direct publish): When `process_tool_event()` handles `FunctionToolResultEvent`, it detects `run_ctx.event_bus is not None` and publishes `ToolCallCompleteEvent` directly to `EventBus` — bypassing the local queue entirely.

This dual-path architecture causes two observable bugs in the opencode TUI:
1. **Missing `ToolCallStartEvent` in stream path**: Path A has no mapping from `FunctionToolCallEvent` to `ToolCallStartEvent`. The TUI never receives the start event via the stream bridge, so tool call UI elements lack initialization metadata.
2. **Race condition on `ToolCallCompleteEvent`**: Path B publishes completion directly to EventBus with no ordering guarantee relative to Path A. The EventBus event processor (`event_processor.py`) expects to see a start event before completion (`ctx.has_tool_part()` check). When completion arrives first, it is silently dropped.

### Additional Complexity: `EventBusHooksAdapter` (to be disabled)

`get_agentlet()` wraps hooks with `EventBusHooksAdapter` when `event_bus is not None`. This adapter publishes its own `ToolCallStartEvent` (at `before_tool_execute`) and `ToolCallCompleteEvent` (at `after_tool_execute`) **directly to EventBus**, completely separate from the stream path.

After Plan B, the stream path will produce:
- `ToolCallStartEvent` (mapped from `FunctionToolCallEvent` in `_run_agentlet_core()`)
- `ToolCallCompleteEvent` (from `process_tool_event()` return value enqueued)

This makes the hooks adapter's tool event publishing fully redundant. Both `_run_agentlet_core()` and `RunExecutor` will produce the same events through the stream path. The hooks adapter's `before_tool_execute` and `after_tool_execute` wrappers will be made transparent passthroughs (no EventBus publish). This eliminates duplicates and keeps the architecture clean.

### Existing Spec

The `unified-event-routing` spec mandates "All events flow through EventBus with stream bridge" and "No dual-consumer race". The current code violates both. Plan B restores compliance.

## Goals / Non-Goals

**Goals:**
- Unify all pydantic-ai stream events into a single FIFO event path in SessionPool mode
- Ensure `ToolCallStartEvent` is generated and emitted before `ToolCallCompleteEvent` within the stream path
- Eliminate the race condition where `ToolCallCompleteEvent` arrives at EventBus before its corresponding start event
- Maintain backward compatibility for standalone mode (`run_ctx.event_bus is None`)

**Non-Goals:**
- No changes to EventBus or event processor architecture
- No changes to TurnRunner stream forwarding behavior
- No new features or capabilities
- **Not changing `run_executor.py` graph logic** — only updating its `process_tool_event()` caller to enqueue the returned event

## Decisions

### Decision: Remove direct EventBus publish from `process_tool_event()`

**Rationale**: `process_tool_event()` currently has dual behavior based on `run_ctx.event_bus` presence. When an event_bus exists, it publishes `ToolCallCompleteEvent` directly and returns `None`. This is the root cause of the race condition. By making `process_tool_event()` always return `combined` (never publish), we eliminate the special case and restore single-responsibility: event transformation only, event routing delegated to the caller.

**Alternative considered**: Keep direct publish but add an async lock or ordering token. Rejected because it adds complexity without solving the missing `ToolCallStartEvent` problem.

### Decision: Add `FunctionToolCallEvent → ToolCallStartEvent` mapping in `_run_agentlet_core()` event_bus branch

**Rationale**: The stream path currently lacks `ToolCallStartEvent` entirely. We add the mapping directly in `_run_agentlet_core()`: when `FunctionToolCallEvent` is received, create a `ToolCallStartEvent(tool_name, arguments)` and put it into the local event queue before the original event.

**Also handle `PartStartEvent(part=BaseToolCallPart)`**: `process_tool_event()` treats both `PartStartEvent(part=BaseToolCallPart)` and `FunctionToolCallEvent` identically for tool call tracking (both store the `BaseToolCallPart` in `pending_tool_calls`). The mapping in `_run_agentlet_core()` must handle both event types to ensure `ToolCallStartEvent` is emitted regardless of which pydantic-ai event type represents the tool call initiation.

**Note**: `run_executor.py` implements a similar mapping pattern, but `RunExecutor` is test-only code and not a production reference. The mapping logic is sound regardless.

**Alternative considered**: Handle this in `process_tool_event()` instead. Rejected because `process_tool_event()` operates on individual events after they've been queued. The start event needs to be emitted *before* the tool call begins, so it belongs in the event dispatch loop.

### Decision: Route `process_tool_event()` results back into local `event_queue`

**Rationale**: Currently, the event_bus branch calls `process_tool_event()` but discards the return value (because it assumes direct publish happened). After removing direct publish, we must route the returned `ToolCallCompleteEvent` into the local queue so it flows through `_stream_events()` → TurnRunner → EventBus.

### Decision: Update `RunExecutor` to enqueue returned `ToolCallCompleteEvent`

**Rationale**: `RunExecutor.execute()` also calls `process_tool_event()` when driving graph-based execution. In the current code, when `run_ctx.event_bus` is set, `process_tool_event()` publishes directly and returns `None`, which `RunExecutor` discards. After the fix, `process_tool_event()` returns `ToolCallCompleteEvent`, so `RunExecutor` must enqueue it on its event queue (matching what `_run_agentlet_core()` will do).

**Impact**: Graph-based team execution in SessionPool mode would lose `ToolCallCompleteEvent` entirely without this update.

### Decision: Disable `EventBusHooksAdapter` tool event publishing

**Rationale**: After the stream path fix, `_run_agentlet_core()` and `RunExecutor` both produce `ToolCallStartEvent` and `ToolCallCompleteEvent` through the queue. The hooks adapter's `before_tool_execute` and `after_tool_execute` wrappers that publish these same events directly to EventBus are now fully redundant. Disabling them eliminates duplicates with ~4 lines of change.

**Scope**: Only disable `before_tool_execute` and `after_tool_execute` event publishing. Keep `before_run` if it serves other purposes (verify before removing). The adapter itself remains as a capability wrapper.

**Alternative considered**: Leave hooks adapter active and accept duplicates. Rejected because the duplication is unnecessary and creates event noise. The fix is trivial.

## Risks / Trade-offs

| Risk | Severity | Mitigation |
|------|----------|------------|
| `process_tool_event()` consumers depend on direct publish behavior | Medium | Three call sites: `_run_agentlet_core()` (event_bus and non-event_bus branches) and `RunExecutor`. All will be updated to capture and enqueue the return value. Non-event_bus branch already does this. |
| `RunExecutor` graph mode loses `ToolCallCompleteEvent` | High | Explicitly updating `RunExecutor` to enqueue returned events. Without this, graph-based team execution in SessionPool mode would break. |
| Standalone mode (`event_bus is None`) behavior change | Low | The `run_ctx.event_bus is None` branch in `process_tool_event()` returns `combined` already. Removing the `is not None` branch means it will always return `combined` — same behavior for standalone mode. |
| Event ordering in local queue may be wrong if multiple tool calls interleave | Low | Local `asyncio.Queue` is FIFO. Events are produced by a single `async for event in agent_run:` loop, so no interleaving is possible within a single run. |
| Test breakages from changed `process_tool_event()` behavior | Low | Update tests that mock or assert on direct EventBus publish. The red flag tests document current broken behavior and should be converted to positive assertions. |
| `EventBusHooksAdapter` disable causes unexpected side effects | Low | Only disabling `before_tool_execute` / `after_tool_execute` event publishing. The adapter remains as a capability wrapper. `before_run` is preserved unless verified redundant. |
| Performance: adding events to queue instead of direct publish adds latency | Very Low | Local queue operations are nanosecond-scale. EventBus publish still happens in TurnRunner, just slightly later. No measurable impact on user-facing latency. |

## Migration Plan

This is a bugfix with no migration needed:
- No configuration changes required
- No database schema changes
- No API contract changes
- Rollback: revert the two-file change

## Open Questions

1. **Does `EventBusHooksAdapter.before_run` serve any purpose after this change?** — `_stream_events()` already yields `RunStartedEvent` at the top. The hooks adapter's `before_run` may produce a duplicate. Verify if it can also be disabled.
2. **Should we add an integration test that exercises the full SessionPool → EventBus → opencode TUI path?** — The red flag tests cover the core logic, but an end-to-end test would prevent regression.
3. **Does the event_bus branch need `merge_queue_into_iterator`?** — The non-event_bus branch uses `merge_queue_into_iterator(stream, run_ctx.event_queue)` to merge injected prompts. The event_bus branch does not. In SessionPool mode, `run_ctx.event_queue` may contain injected prompts that need merging. Verify if this is a latent bug or intentional omission.
