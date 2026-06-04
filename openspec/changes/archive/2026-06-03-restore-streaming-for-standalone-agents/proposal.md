## Why

In commit `bf58c0740` ("feat(graph): migrate to pydantic-graph for team execution"), `NativeAgent._stream_events()` was wrapped inside a pydantic-graph `Step` to enable graph-based team orchestration. However, this wrapping introduces a critical regression for **standalone agent execution**: all streaming events (text deltas, thinking chunks, tool calls) are buffered inside the Step boundary and only emitted after the Step fully returns, resulting in a "batch processing" experience instead of real-time streaming. This breaks ACP, OpenCode, and AG-UI clients that expect live event updates.

## What Changes

- **Modify `NativeAgent._stream_events()`**: Remove pydantic-graph wrapping and restore direct background task iteration:
  - Run `agentlet.iter()` directly in a background task, pushing events to an async queue in real-time.
  - Remove the graph wrapping that was introduced in the migration commit.
- **No changes to `_run_agentlet_core()`**: The core streaming logic remains the shared streaming core.
- **No changes to graph-based execution**: `MessageNode.run()` / `run_stream()` via `MessageNodeStep` → `_execute_node()` continues to use graph execution as before.

## Capabilities

### New Capabilities
<!-- No new capabilities introduced — this is a bugfix for existing behavior. -->

### Modified Capabilities
<!-- No spec-level requirement changes — implementation detail fix only. -->

## Impact

- **Files**: `src/agentpool/agents/native_agent/agent.py` (primarily `_stream_events()` method)
- **Behavior**: Standalone `agent.run_stream()` calls restore real-time streaming; graph-based execution retains current behavior
- **APIs**: No public API changes
- **Tests**: Existing streaming tests should pass; may need new tests to verify standalone vs graph path event timing
- **Backward Compatibility**: Fully backward compatible — no breaking changes
