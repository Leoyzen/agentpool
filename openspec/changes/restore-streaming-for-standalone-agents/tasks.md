## 1. Review Pre-Migration Pattern

- [ ] 1.1 Review pre-migration `_stream_events()` code from commit `bf58c0740^` to extract: background task pattern, event queue lifecycle, `iteration_done` signaling, `CancelledError` handling, `iteration_error` propagation, and `StreamCompleteEvent` emission
- [ ] 1.2 Identify all code inside current `_stream_events()` that is graph-specific (GraphBuilder, Step, Graph.iter, state.event_queue drain, GraphTask mapping, EndMarker/ErrorMarker handling) — this will be removed

## 2. Core Implementation

- [ ] 2.1 Remove pydantic-graph wrapping from `_stream_events()`:
  - Delete GraphBuilder, Step, graph creation, and graph iteration logic
  - Restore background task running `_run_agentlet_core()` with a local event queue
  - Restore consumer loop draining event queue with real-time yields
  - Preserve `RunStartedEvent` emission at stream start
  - Preserve `StreamCompleteEvent` emission at stream end
- [ ] 2.2 Ensure `_run_agentlet_core()` is called with correct arguments from the restored background task
- [ ] 2.3 Verify `_execute_node()` and `MessageNodeStep` remain untouched (graph execution path unchanged)
- [ ] 2.4 Verify cancellation semantics match pre-migration behavior (signal `iteration_done`, set `run_ctx.cancelled`, cancel iteration task in finally block)

## 3. Testing

- [ ] 3.1 Run existing streaming tests to verify no regressions (`uv run pytest -k stream`)
- [ ] 3.2 Add test asserting `agent.run_stream()` produces events in real-time (not batched at the end) — simulate slow model and check first event arrives before iteration completes
- [ ] 3.3 Add test asserting `agent.run_stream()` cancellation works correctly (cancel consumer mid-stream, verify `run_ctx.cancelled` is set)
- [ ] 3.4 Add test asserting `run_ctx.event_bus` branch in `_run_agentlet_core()` works correctly
- [ ] 3.5 Add test asserting event ordering is correct: `RunStartedEvent` → `PartDeltaEvent`s → `StreamCompleteEvent`
- [ ] 3.6 Run full test suite to verify no regressions (`uv run pytest`)

## 4. Documentation & Cleanup

- [ ] 4.1 Add docstring to `_stream_events()` explaining it uses direct iteration for real-time streaming, while graph execution uses `_execute_node()` via `MessageNodeStep`
- [ ] 4.2 Verify type checking passes (`uv run mypy src/agentpool/agents/native_agent/agent.py`)
- [ ] 4.3 Verify linting passes (`uv run ruff check src/agentpool/agents/native_agent/agent.py`)
- [ ] 4.4 Remove any unused imports introduced by graph wrapping (GraphBuilder, Step, StepContext, EndMarker, ErrorMarker, NodeID, AgentPoolState if no longer needed in agent.py)
