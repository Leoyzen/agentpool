# Telemetry & Span Instrumentation

AgentPool uses Logfire (backed by OpenTelemetry). All code in the **critical execution path** (RunLoop, Turn, delegation, protocol entry points) **MUST** be instrumented. Uninstrumented code produces orphan traces.

## Rules

1. **`@logfire.instrument`** for method-level spans. Format string params extract from args:
   ```python
   @logfire.instrument("session.receive_request {session_id}")
   async def receive_request(self, session_id: str, content: str, **kwargs) -> str | None: ...
   ```

2. **`with logfire.span(...)`** for spans that must stay open across `await` boundaries (e.g., delegation):
   ```python
   with logfire.span("delegation.subagent", parent_session_id=..., child_agent_name=name):
       return await session_pool.run_agent(name, prompt, parent_session_id=self._session_id)
   ```

3. **Never `asyncio.create_task()` without an active span.** `create_task()` copies `contextvars` — if a span is active, the child task inherits it as parent. If not, child spans are orphaned. Use `@logfire.instrument` on the calling method, or wrap in `with logfire.span(...)`.

4. **ACP cross-process: populate `_meta.traceparent`** (W3C trace context) when acting as ACP client; extract when acting as ACP agent. See [ACP _meta Propagation RFD](https://agentclientprotocol.com/rfds/meta-propagation).

## Span Naming

`protocol.{name}.{method}`, `orchestration.{component}.{method}`, `turn.{agent_type}`, `delegation.subagent`, `capability.{name}.{method}`, `lifecycle.{component}.{method}`, `graph.{component}.{method}`

## Required Span Attributes

`session_id`, `parent_session_id`, `agent_name`, `turn_id`, `run_id`

## Do NOT Instrument

`MemoryJournal` / `MemorySnapshotStore`, pure data transforms, test helpers, logging calls.

## Critical `create_task()` Call Sites That MUST Have a Span

| Call site | File | How |
|---|---|---|
| `_consume_run()` | `session_controller.py` | `@logfire.instrument` on `_start_run_handle()` |
| `event_bus.publish()` | `run.py` | `@logfire.instrument` on calling method |
| `_interrupt()` | `run.py` | `@logfire.instrument` on `cancel()` |
| Background tasks | `subagent_tools.py` | `with logfire.span(...)` in task body |
| `_execute_parallel()` | `base_team.py` | `@logfire.instrument` on `_execute_parallel()` |
