## Context

AgentPool uses Logfire (backed by OpenTelemetry) for distributed tracing. Auto-instrumentation covers PydanticAI model requests and MCP transport, and `@logfire.instrument` exists on `Tool.execute()` and ACP connection handlers. However, the core execution path — RunLoop (`RunHandle.start()`), Turn execution (`NativeTurn.execute()`, `ACPTurn.execute()`), subagent delegation (`SubagentCapability.spawn_subagent()`), SessionController, lifecycle dimensions, and graph steps — has zero span instrumentation.

When `SessionController._start_run_handle()` creates a background task via `asyncio.create_task(self._consume_run(...))`, no span is active at the call site. Python 3.7+ `create_task()` copies `contextvars` (which OTel uses for trace propagation), but since no span is open, the child task starts with no parent trace. This produces orphan traces for every subagent session.

ACP spec (v1/v2) reserves `_meta.traceparent`/`tracestate`/`baggage` for W3C trace context propagation ([RFD](https://agentclientprotocol.com/rfds/meta-propagation)), but agentpool never populates these fields.

## Goals / Non-Goals

**Goals:**
- Fix orphan traces: subagent sessions SHALL produce child spans nested under the parent agent's trace
- Instrument all critical-path methods with `@logfire.instrument` or `with logfire.span(...)`
- Propagate W3C trace context across ACP process boundaries via `_meta.traceparent`
- Establish span naming conventions and required attributes (documented in AGENTS.md)

**Non-Goals:**
- Full coverage of all ~108 locations identified in Issue #162 — phased approach (P0+P1 first)
- Instrumenting `MemoryJournal` / `MemorySnapshotStore` (in-memory, no diagnostic value)
- Custom OpenTelemetry SDK setup beyond what Logfire provides
- xeno-agent instrumentation (separate codebase)

## Decisions

### D1: `with logfire.span("delegation.subagent")` for delegation bridging

**Decision**: Wrap `session_pool.run_agent()` call in `SubagentCapability.spawn_subagent()` with a `logfire.span` that stays open during the entire subagent execution.

**Note**: `SubagentCapability.spawn_subagent()` is a `@staticmethod` — `@logfire.instrument` with format-string params cannot access `self`. Use `with logfire.span(...)` with manual attributes from `ctx.deps` (the `AgentContext`).

**Rationale**: `session_pool.run_agent()` awaits the subagent's completion via EventBus queue. The span stays open during this await. `asyncio.create_task()` inside `_start_run_handle()` (called synchronously from `_route_message()` ← `send_message()` ← `run_agent()`) copies the active span's context to the child task, making the child `RunHandle.start()` span a child of the delegation span.

**Call chain** (confirmed by code review):
```
with logfire.span("delegation.subagent"):        # span OPENS
  await session_pool.run_agent(name, prompt)     # span stays open during await
    → await self.create_session(...)              # await — span still active
    → await self.send_message(session_id, prompt) # await — span still active
      → await self._route_message(...)            # await — span still active
        → self._start_run_handle(...)             # SYNC — span still active
          → asyncio.create_task(_consume_run(...))# ← contextvars copied HERE
    → while True: await bus_queue.get()           # waits for completion — span still active
# span CLOSES after run_agent() returns
```

**Note**: `send_message()` goes directly to `_route_message()`, NOT through the deprecated `receive_request()`. The delegation span covers the primary path. `receive_request()` instrumentation (task 2.1) provides diagnostic value for other callers but is NOT part of the orphan-trace fix.

**Alternative**: Pass `traceparent` explicitly via session metadata. More complex, duplicates what contextvars already handles for in-process propagation. Use this only for ACP cross-process (D3).

### D2: `@logfire.instrument` for method-level spans (non-generator methods)

**Decision**: Use `@logfire.instrument("name {param}")` decorator on non-generator methods. Format string params extract from all arguments including `self`.

**Rationale**: The decorator automatically handles span entry/exit, argument recording, exception capture, and contextvars propagation. Less boilerplate than manual `with logfire.span(...)`.

**Caveat — `self` attributes**: Format string params extract from method args. For attributes on `self` (e.g., `session_id`, `agent_type` on `RunHandle`), use `self.session_id` in the format string (logfire extracts from `self` attributes) OR set attributes manually inside the method via `logfire.current_span().set_attribute(...)`.

### D3: `with logfire.span(...)` for async generator methods

**Decision**: For async generator methods (`RunHandle.start()`, `RunHandle._execute_turn()`, `NativeTurn.execute()`, `ACPTurn.execute()`, `BaseAgent.run_stream()`), use `with logfire.span(...)` inside the method body wrapping the main loop, NOT `@logfire.instrument` as a decorator.

**Rationale**: `@logfire.instrument` on async generators requires `allow_generator=True` to suppress warnings. While it does work (the span wraps the `async for` loop and stays open across yields), using `with logfire.span(...)` inside the method body is clearer, avoids the warning, and gives explicit control over span lifecycle. For generators with a single `yield`-based loop, place the `with` statement at the top of the method body.

**Alternative**: `@logfire.instrument("...", allow_generator=True)` — works but emits deprecation-style warnings without the flag and has a `yield from` limitation (doesn't forward `asend()` values). `RunHandle.start()` doesn't use `asend()`, so this is safe, but the manual `with` approach is more maintainable.

### D4: `TraceContextTextMapPropagator` for ACP cross-process

**Decision**: Use `opentelemetry.trace.propagation.tracecontext.TraceContextTextMapPropagator` to inject/extract W3C trace context into/from ACP `_meta` field.

**Rationale**: ACP spec reserves `_meta.traceparent`/`tracestate`/`baggage` for this purpose. Using the standard OTel propagator ensures interop with MCP SDKs and other ACP implementations. Agentpool's schema models alias `_meta` as `field_meta` in Python (serialized as `_meta` in JSON).

**Injection (client side)**: `propagator.inject(carrier)` reads the current OTel span from contextvars and writes `traceparent` to the carrier dict. Works inside a `with logfire.span(...)` block because logfire sets the OTel span as current.

**Extraction (agent side)**: `propagator.extract(carrier)` returns an OTel `Context` with the parent span. Logfire doesn't expose "start span with arbitrary parent context," so use the OTel API directly:
```python
from opentelemetry import trace
from opentelemetry.trace import use_span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

context = TraceContextTextMapPropagator().extract(incoming_field_meta or {})
span = trace.get_tracer(__name__).start_span("acp.agent.handle_prompt", context=context)
with use_span(span):
    # child spans created here nest under the extracted parent
    ...
```

### D5: Phased implementation (P0 → P1)

**Decision**: Implement in two phases:
- **P0** (6 locations): Fix span breakage — delegation span (2 paths), RunHandle.start, _execute_turn, NativeTurn.execute, ACPTurn.execute
- **P1** (17 locations): High diagnostic value — SessionController methods, BaseAgent.run_stream, team execution, durable journal/snapshot, graph adapter, subagent tools, ACP traceparent

**Rationale**: P0 alone fixes the orphan trace problem. P1 adds diagnostic depth. P2/P3 (capability lifecycle, hooks, MCP client, skills) deferred to follow-up.

### D6: Fix pre-existing double-iteration bug in deprecated path

**Decision**: Task 1.2 should fix the double-iteration bug in `RunLoopDelegationService.spawn_subagent()` in addition to adding span instrumentation.

**Rationale**: The deprecated path calls `controller.receive_request()` which starts a `_consume_run()` background task that iterates `run_handle.start("")`. Then `RunLoopDelegationService.spawn_subagent()` ALSO iterates `run_handle.start("")` directly. This creates two concurrent iterations on the same `RunHandle`, causing a race condition. Fix: replace the direct `run_handle.start("")` iteration with EventBus subscription (matching `session_pool.run_agent()`'s pattern), or skip `receive_request()` and iterate `start()` exclusively.

## Expected Span Hierarchy

```
delegation.subagent                          [from SubagentCapability.spawn_subagent]
  └─ session.start_run_handle                [from _start_run_handle, short-lived]
       └─ orchestration.run_handle.start     [from RunHandle.start, background task]
            └─ orchestration.run_handle.execute_turn  [from _execute_turn]
                 └─ turn.native / turn.acp   [from NativeTurn.execute / ACPTurn.execute]
                      └─ (pydantic-ai auto-instrumented model request spans)
```

**Note**: `_start_run_handle` is a synchronous method. Its span is intentionally short-lived — it provides context for `asyncio.create_task()` and ends when the method returns. The child task's `RunHandle.start()` span is the long-lived span that inherits the context via contextvars.

## Risks / Trade-offs

- [Span overhead in hot paths] → `@logfire.instrument` adds ~microseconds per call. Negligible compared to LLM latency for most methods. **Exception**: `DurableJournal.append()` is called per-event via `CommChannel.publish()` — consider sampling or only instrumenting at DEBUG level if span cardinality becomes an issue.
- [ACP agents that don't extract traceparent] → Graceful degradation: if the agent doesn't extract `_meta.traceparent`, its spans are orphaned as before. No behavioral impact.
- [Test breakage from new spans] → Observability is disabled in tests via env vars. `logfire.instrument` is a no-op when logfire is not configured. Span tests use `logfire.testing.capture_spans()` which works regardless of global config.
- [Deprecated path double-iteration] → Pre-existing bug, not caused by instrumentation. Fix in task 1.2 to avoid duplicate child spans from two concurrent `start()` iterations.
- [`host/factory.py` create_task] → Audit needed: if `host/factory.py` spawns agent run tasks, those would be orphans. Add to P1 if needed.
