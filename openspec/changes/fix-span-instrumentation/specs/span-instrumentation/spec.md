## ADDED Requirements

### Requirement: Critical-path methods SHALL have logfire span instrumentation

All methods in the critical execution path (RunLoop, Turn, delegation, team execution, lifecycle durable operations, graph steps, subagent tools) SHALL be instrumented with either `@logfire.instrument` decorator or `with logfire.span(...)` context manager. Span names SHALL follow the convention: `layer.component.method` (e.g., `orchestration.run_handle.start`, `turn.native`, `delegation.subagent`).

Async generator methods (`RunHandle.start()`, `RunHandle._execute_turn()`, `NativeTurn.execute()`, `ACPTurn.execute()`, `BaseAgent.run_stream()`) SHALL use `with logfire.span(...)` inside the method body wrapping the main loop, NOT `@logfire.instrument` as a decorator.

#### Scenario: RunLoop entry point is instrumented
- **WHEN** `RunHandle.start()` is called
- **THEN** a span named `orchestration.run_handle.start` is created with `session_id` and `agent_type` attributes
- **AND** the span stays open across all `yield` points until the generator is exhausted

#### Scenario: Turn execution is instrumented
- **WHEN** `NativeTurn.execute()` or `ACPTurn.execute()` is called
- **THEN** a span named `turn.native` or `turn.acp` is created with `turn_id` and `session_id` attributes
- **AND** the span stays open across all `yield` points

#### Scenario: Team parallel execution is instrumented
- **WHEN** `base_team._execute_parallel()` is called
- **THEN** a span is created covering all parallel member executions

### Requirement: Subagent delegation SHALL produce nested child spans

When a parent agent delegates to a subagent via `SubagentCapability.spawn_subagent()` or `RunLoopDelegationService.spawn_subagent()`, the delegation SHALL be wrapped in a `logfire.span("delegation.subagent")` that stays open for the duration of the subagent's execution. The child session's `RunHandle.start()` span SHALL be a child of the delegation span, not an orphan.

`SubagentCapability.spawn_subagent()` is a `@staticmethod` — the span MUST use `with logfire.span(...)` with manual attributes, NOT `@logfire.instrument` with format string params (which would fail since `self` is unavailable).

#### Scenario: Subagent span nests under parent
- **WHEN** parent agent calls `spawn_subagent(name, prompt)`
- **THEN** a `delegation.subagent` span is created with `parent_session_id` and `child_agent_name` attributes
- **AND** the child session's `run_loop` span has the `delegation.subagent` span as its parent

#### Scenario: Deprecated delegation path does not double-iterate
- **WHEN** `RunLoopDelegationService.spawn_subagent()` is used (deprecated path)
- **THEN** the span wrapping is applied
- **AND** the method does NOT create two concurrent iterations of `run_handle.start()` (pre-existing bug SHALL be fixed)

### Requirement: asyncio.create_task() call sites SHALL have an active span

Every `asyncio.create_task()` call in the critical execution path SHALL execute while a logfire span is active, so that the child task inherits the parent span via contextvars. This is achieved by `@logfire.instrument` on the calling method or `with logfire.span(...)` wrapping the call.

`SessionController._start_run_handle()` is a synchronous method — its span is intentionally short-lived, providing context for `create_task()` and ending on return. The child task's `RunHandle.start()` span is the long-lived span.

#### Scenario: SessionController creates background task with span
- **WHEN** `SessionController._start_run_handle()` calls `asyncio.create_task(self._consume_run(...))`
- **THEN** a span is active (via `@logfire.instrument` on `_start_run_handle`)
- **AND** the `_consume_run` background task inherits the span as parent

#### Scenario: Fire-and-forget background task has span
- **WHEN** `subagent_tools._start_async_task()` creates a background task
- **THEN** the task body wraps execution in `with logfire.span(...)`

### Requirement: ACP cross-process trace context SHALL propagate via _meta.traceparent

When agentpool acts as an ACP client (sending `session/prompt` to an external agent), it SHALL inject W3C trace context into the request's `_meta` field (Python attribute: `field_meta`) using `TraceContextTextMapPropagator.inject()`. When acting as an ACP agent (receiving requests), it SHALL extract trace context using `TraceContextTextMapPropagator.extract()` and activate it via OTel's `use_span()` API before creating child spans.

#### Scenario: ACP client injects traceparent
- **WHEN** agentpool sends `session/prompt` to an external ACP agent
- **THEN** the request's `field_meta` dict contains a `traceparent` key with W3C trace context format (`00-<trace-id>-<span-id>-<flags>`)

#### Scenario: ACP agent extracts traceparent
- **WHEN** agentpool receives a `session/prompt` request with `field_meta` containing `traceparent`
- **THEN** the agent extracts the trace context via `TraceContextTextMapPropagator.extract()`
- **AND** activates it via `use_span()` so child spans link to the client's trace

### Requirement: Durable lifecycle operations SHALL be instrumented

`DurableJournal.append()`, `upsert()`, and `resume()` SHALL have `@logfire.instrument` to track SQLite write latency. `DurableSnapshotStore.save()` and `load()` SHALL be instrumented similarly. `MemoryJournal` and `MemorySnapshotStore` SHALL NOT be instrumented (no diagnostic value).

**Note**: `DurableJournal.append()` is called per-event via `CommChannel.publish()`. If span cardinality becomes an issue, consider sampling or DEBUG-level-only instrumentation.

#### Scenario: Durable journal append is traced
- **WHEN** `DurableJournal.append(event)` is called
- **THEN** a span records the operation with `session_id` attribute

### Requirement: Span attributes SHALL include standard identifiers

All spans in the critical execution path SHALL include relevant standard attributes: `session_id`, `parent_session_id` (for delegation), `agent_name`, `turn_id`, `run_id`. Attributes on `self` (not method args) SHALL be set manually via `logfire.current_span().set_attribute()` or passed as kwargs to `logfire.span()`.

#### Scenario: Turn span includes turn_id
- **WHEN** a turn span is created
- **THEN** the span includes `turn_id` and `session_id` attributes
