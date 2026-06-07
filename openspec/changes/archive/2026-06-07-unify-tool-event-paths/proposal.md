## Why

In SessionPool mode, subagent tool calls fail to display in the opencode TUI due to a dual-path event architecture in `NativeAgent._run_agentlet_core()`. `FunctionToolCallEvent` flows through a local event queue while `ToolCallCompleteEvent` is published directly to `EventBus`, bypassing the queue. This causes two problems: (1) `ToolCallStartEvent` is never generated in the stream path because there's no mapping from `FunctionToolCallEvent`, and (2) `ToolCallCompleteEvent` can arrive at the EventBus processor before the start event, causing silent event drops.

Additionally, `EventBusHooksAdapter` publishes its own `ToolCallStartEvent` and `ToolCallCompleteEvent` directly to EventBus (separate from the stream path). Once the stream path is fixed to produce these events, the hooks adapter's tool event publishing becomes fully redundant and creates duplicates. Both issues are fixed in this change.

## What Changes

- **Modify `process_tool_event()`** in `src/agentpool/agents/native_agent/helpers.py` to always return combined results instead of conditionally publishing directly to `EventBus`
- **Add `ToolCallStartEvent` mapping** in `NativeAgent._run_agentlet_core()` for the event_bus branch, converting `FunctionToolCallEvent` to `ToolCallStartEvent`
- **Route all tool events through the local event_queue** in SessionPool mode, ensuring FIFO ordering guarantees
- **Remove direct EventBus publish from tool event path** — all events flow through `_stream_events()` → TurnRunner.publish → EventBus
- **Update `RunExecutor`** in `src/agentpool/orchestrator/run_executor.py` to enqueue the returned `ToolCallCompleteEvent` from `process_tool_event()` when `run_ctx.event_bus` is set (mirroring the `_run_agentlet_core()` fix)
- **Disable `EventBusHooksAdapter` tool event publishing** — make `before_tool_execute` and `after_tool_execute` transparent passthroughs, since the stream path now produces `ToolCallStartEvent` and `ToolCallCompleteEvent`
- **Update tests** that expect direct EventBus publish behavior from `process_tool_event()`

## Capabilities

### New Capabilities
<!-- No new capabilities — this is an architectural fix to existing event routing -->

### Modified Capabilities
- `unified-event-routing`: The "No dual-consumer race" scenario is violated by the current `ToolCallCompleteEvent` direct-publish path. This change restores the single-event-path invariant by routing all tool events through the local queue → TurnRunner → EventBus path. The `EventBusHooksAdapter` duplication is eliminated by disabling its redundant tool event publishing.

## Impact

- **Files**: `src/agentpool/agents/native_agent/agent.py`, `src/agentpool/agents/native_agent/helpers.py`, `src/agentpool/orchestrator/run_executor.py`, `src/agentpool/agents/native_agent/eventbus_hooks_adapter.py`
- **Modes affected**: SessionPool mode only (`run_ctx.event_bus is not None`); standalone mode unchanged
- **API changes**: None — purely internal event routing fix
- **Tests**: `tests/agents/test_native_agent_event_bus.py`, `tests/servers/opencode_server/test_subagent_completion_red_flags.py`, `tests/orchestrator/test_run_executor.py`
- **Risk**: Low — single FIFO path is simpler and eliminates race conditions; hooks adapter cleanup is ~4 lines and trivially safe
