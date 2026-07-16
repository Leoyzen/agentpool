## ADDED Requirements

### Requirement: SessionController critical methods SHALL have logfire span instrumentation

`SessionController.receive_request()`, `_start_run_handle()`, and `_consume_run()` SHALL be instrumented with `@logfire.instrument`. The span on `_start_run_handle()` ensures the `asyncio.create_task(self._consume_run(...))` call site has an active span, so the background task inherits the parent trace.

#### Scenario: receive_request creates a span
- **WHEN** `SessionController.receive_request(session_id, content)` is called
- **THEN** a span is created with `session_id` attribute

#### Scenario: _start_run_handle span propagates to background task
- **WHEN** `_start_run_handle()` calls `asyncio.create_task(self._consume_run(...))`
- **THEN** the background task inherits the `_start_run_handle` span as parent via contextvars
