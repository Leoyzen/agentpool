## ADDED Requirements

### Requirement: RunHandle lifecycle methods SHALL have logfire span instrumentation

`RunHandle.start()` SHALL be instrumented with `with logfire.span("orchestration.run_handle.start", ...)` inside the method body (async generator — do NOT use `@logfire.instrument`). `RunHandle._execute_turn()` SHALL be instrumented with `with logfire.span(...)` inside the method body (also an async generator) to create a per-turn span with `turn_id` attribute. These spans provide the parent context for pydantic-ai model request spans and tool execution spans.

#### Scenario: RunHandle.start creates top-level span
- **WHEN** `RunHandle.start(initial_prompt)` is called
- **THEN** a span is created with `session_id` and `agent_type` attributes

#### Scenario: _execute_turn creates per-turn span
- **WHEN** `RunHandle._execute_turn(agent, event_bus, session, prompts)` is called
- **THEN** a span is created with `turn_id` and `session_id` attributes
- **AND** pydantic-ai model request spans (from `logfire.instrument_pydantic_ai()`) are children of this span
