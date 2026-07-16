## 1. P0: Fix span breakage (delegation → child trace nesting)

- [x] 1.1 Add `with logfire.span("delegation.subagent", parent_session_id=..., child_agent_name=name)` wrapping `session_pool.run_agent()` call in `SubagentCapability.spawn_subagent()` (`capabilities/subagent_capability.py`). **Note**: This is a `@staticmethod` — use `with logfire.span(...)` with manual attributes from `ctx.deps`, NOT `@logfire.instrument`.
- [x] 1.2 Fix double-iteration bug AND add span wrapping in `RunLoopDelegationService.spawn_subagent()` (`capabilities/runloop_delegation.py`). Replace direct `run_handle.start("")` iteration with EventBus subscription (matching `session_pool.run_agent()` pattern) to avoid two concurrent `start()` iterations. Then wrap in `with logfire.span("delegation.subagent", ...)`.
- [x] 1.3 Add `with logfire.span("orchestration.run_handle.start", session_id=self.session_id, agent_type=self.agent_type)` inside `RunHandle.start()` method body (`orchestrator/run.py`), wrapping the main `while` loop. **Do NOT use `@logfire.instrument`** — `start()` is an async generator; use manual `with logfire.span(...)` to keep span open across yields.
- [x] 1.4 Add `with logfire.span("orchestration.run_handle.execute_turn", turn_id=turn_id, session_id=self.session_id)` inside `RunHandle._execute_turn()` method body (`orchestrator/run.py`), wrapping the turn execution. **Do NOT use `@logfire.instrument`** — async generator.
- [x] 1.5 Add `with logfire.span("turn.native", turn_id=self.run_ctx.turn_id, session_id=self.run_ctx.session_id)` inside `NativeTurn.execute()` method body (`agents/native_agent/turn.py`). **Do NOT use `@logfire.instrument`** — async generator.
- [x] 1.6 Add `with logfire.span("turn.acp", turn_id=self.run_ctx.turn_id, session_id=self.run_ctx.session_id)` inside `ACPTurn.execute()` method body (`agents/acp_agent/turn.py`). **Do NOT use `@logfire.instrument`** — async generator.
- [x] 1.7 Write span hierarchy test using `logfire.testing.capture_spans()`: verify `delegation.subagent` → `orchestration.run_handle.start` → `turn.native` parent-child nesting. Verify span stays open across multiple yields (not just first yield).

## 2. P1: SessionController and RunHandle instrumentation

- [x] 2.1 Add `@logfire.instrument("session.receive_request {session_id}")` to `SessionController.receive_request()` (`orchestrator/session_controller.py`). **Note**: This method is NOT on the primary delegation path (`send_message` bypasses it via `_route_message`). Diagnostic value for other callers only.
- [x] 2.2 Add `@logfire.instrument("session.start_run_handle {session_id}")` to `SessionController._start_run_handle()` (`orchestrator/session_controller.py`). This is a sync method — span is intentionally short-lived, provides context for `asyncio.create_task()`.
- [x] 2.3 Add `@logfire.instrument("session.consume_run")` to `SessionController._consume_run()` (`orchestrator/session_controller.py`).
- [x] 2.4 Add `with logfire.span("agent.run_stream", ...)` inside `BaseAgent.run_stream()` method body (`agents/base_agent.py`). **Do NOT use `@logfire.instrument`** — verify if async generator first; if so, use manual span.
- [x] 2.5 Add `@logfire.instrument` to `RunHandle.steer()` and `RunHandle.followup()` (`orchestrator/run.py`). These are regular async methods, not generators.

## 3. P1: Team execution and subagent tools instrumentation

- [x] 3.1 Add `@logfire.instrument("team.execute_parallel")` to `base_team._execute_parallel()` (`delegation/base_team.py`).
- [x] 3.2 Add `@logfire.instrument("team.execute_sequential")` to `base_team._execute_sequential()` (`delegation/base_team.py`).
- [x] 3.3 Add `with logfire.span("subagent.background_task", task_id=task_id)` inside `_safe_background_run()` nested function body in `subagent_tools.py` (`agentpool_toolsets/builtin/subagent_tools.py`). The span must be INSIDE the task body, not at the `create_task` call site.

## 4. P1: Lifecycle durable operations instrumentation

- [x] 4.1 Add `@logfire.instrument("lifecycle.journal.append")` to `DurableJournal.append()` (`lifecycle/journal.py`). **Watch**: Called per-event — monitor span cardinality.
- [x] 4.2 Add `@logfire.instrument("lifecycle.journal.upsert")` to `DurableJournal.upsert()` (`lifecycle/journal.py`).
- [x] 4.3 Add `@logfire.instrument("lifecycle.journal.resume")` to `DurableJournal.resume()` (`lifecycle/journal.py`).
- [x] 4.4 Add `@logfire.instrument("lifecycle.snapshot.save")` to `DurableSnapshotStore.save()` (`lifecycle/snapshot_store.py`).
- [x] 4.5 Add `@logfire.instrument("lifecycle.snapshot.load")` to `DurableSnapshotStore.load()` (`lifecycle/snapshot_store.py`).

## 5. P1: Graph layer instrumentation

- [x] 5.1 Add `@logfire.instrument("graph.step.execute")` to `MessageNodeStep._execute()` (`messaging/graph_adapter.py`).
- [x] 5.2 Add `@logfire.instrument("graph.signal.next")` to `SignalEmittingGraphRun.__anext__()` (`messaging/signal_adapter.py`).

## 6. P1: ACP cross-process trace context propagation

- [x] 6.1 Inject W3C `traceparent` into ACP `field_meta` (Python attribute, serialized as `_meta` in JSON) via `TraceContextTextMapPropagator.inject()` before sending `session/prompt` requests. Identify the single lowest-level method in `acp/client/connection.py` that sends all `session/prompt` requests and inject there.
- [x] 6.2 Extract W3C `traceparent` from ACP `field_meta` via `TraceContextTextMapPropagator.extract()` when receiving requests in ACP agent handler (`acp_server/handler.py`). Use OTel API (`use_span()` / `start_span(context=...)`) to activate the extracted context — logfire does NOT expose "start span with arbitrary parent context."
- [x] 6.3 Write test using `logfire.testing.capture_spans()`: verify `field_meta` dict in outgoing ACP request contains `traceparent` key with W3C format (`00-<trace-id>-<span-id>-<flags>`) when a logfire span is active.

## 7. P1: Audit and miscellaneous

- [x] 7.1 Audit `host/factory.py` for `asyncio.create_task()` calls in the critical path. If agent run tasks are spawned there without a span, add instrumentation.
- [x] 7.2 Add span test for team parallel execution: verify `_execute_parallel` span covers all member executions.
- [x] 7.3 Add span test for fire-and-forget background task: verify `subagent.background_task` span exists and has `task_id` attribute.

## 8. Verification

- [x] 8.1 Run full test suite: `uv run pytest` — verify no regressions from instrumentation (logfire is no-op when unconfigured).
- [x] 8.2 Run `uv run ruff check src/` and `uv run --no-group docs mypy src/` — verify no lint/type errors.
- [x] 8.3 Manual verification: run a delegation scenario with observability enabled and confirm nested spans in SigNoz/Logfire UI.
