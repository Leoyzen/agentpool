## Why

Subagent sessions produce orphan traces instead of nested child spans under the parent agent's trace. The codebase has ~11% span instrumentation coverage (~12 of ~108 critical-path locations), with zero instrumentation in the RunLoop, Turn, delegation, and capability layers. This makes distributed tracing in SigNoz/Jaeger unusable for debugging multi-agent workflows.

## What Changes

- Add `@logfire.instrument` to non-generator critical-path methods: `SessionController.receive_request()`, `_start_run_handle()`, `_consume_run()`
- Add `with logfire.span(...)` inside async generator method bodies (NOT `@logfire.instrument`): `RunHandle.start()`, `_execute_turn()`, `NativeTurn.execute()`, `ACPTurn.execute()`, `BaseAgent.run_stream()`
- Add `with logfire.span("delegation.subagent")` in `SubagentCapability.spawn_subagent()` and `RunLoopDelegationService.spawn_subagent()` to bridge parent→child trace context across `asyncio.create_task()` boundaries
- Add `@logfire.instrument` to team execution: `base_team._execute_parallel()`, `_execute_sequential()`
- Add `@logfire.instrument` to lifecycle durable operations: `DurableJournal.append/upsert/resume`, `DurableSnapshotStore.save/load`
- Add `@logfire.instrument` to graph layer: `MessageNodeStep._execute()`, `SignalEmittingGraphRun.__anext__()`
- Add `with logfire.span(...)` inside subagent fire-and-forget background task body: `subagent_tools._start_async_task()` → `_safe_background_run()`
- Populate ACP `_meta.traceparent` (W3C trace context) when acting as ACP client; extract when acting as ACP agent
- Add span verification tests using `logfire.testing.capture_spans()`

## Capabilities

### New Capabilities
- `span-instrumentation`: Logfire/OpenTelemetry span coverage requirements for the critical execution path (RunLoop, Turn, delegation, capabilities, lifecycle, graph, protocol entry points)

### Modified Capabilities
- `session-orchestration`: SessionController methods require `@logfire.instrument` spans to propagate trace context to background tasks
- `unified-session-lifecycle`: RunHandle lifecycle methods require span instrumentation for trace visibility

## Impact

- **Files modified**: `orchestrator/session_controller.py`, `orchestrator/run.py`, `orchestrator/turn.py`, `capabilities/subagent_capability.py`, `capabilities/runloop_delegation.py`, `agents/native_agent/turn.py`, `agents/acp_agent/turn.py`, `agents/base_agent.py`, `delegation/base_team.py`, `lifecycle/journal.py`, `lifecycle/snapshot_store.py`, `messaging/graph_adapter.py`, `messaging/signal_adapter.py`, `agentpool_toolsets/builtin/subagent_tools.py`, ACP client/agent connection files
- **Dependencies**: `logfire` (already in deps), `opentelemetry.trace.propagation.tracecontext` (transitive via logfire)
- **Risk**: Low — purely additive instrumentation, no behavioral changes. Existing `logfire.instrument_pydantic_ai()` and `logfire.instrument_mcp()` auto-instrumentation unaffected.
- **Testing**: New span hierarchy tests; existing tests unaffected (observability disabled in test env)
