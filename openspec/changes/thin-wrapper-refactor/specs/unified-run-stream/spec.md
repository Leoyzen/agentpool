## ADDED Requirements

### Requirement: RunExecutor is the sole run execution path
All agent streaming execution SHALL go through `RunExecutor`, which calls `agent_run.next(node)` explicitly. The `BaseAgent.run_stream()` standalone path (Path B) SHALL be removed. `BaseAgent.run_stream()` SHALL delegate to `RunExecutor` or be removed entirely.

#### Scenario: BaseAgent.run_stream delegates to RunExecutor
- **WHEN** `BaseAgent.run_stream()` is called
- **THEN** execution SHALL route through `RunExecutor` which calls `agent_run.next(node)` explicitly

#### Scenario: No standalone producer/consumer pattern
- **WHEN** `BaseAgent.run_stream()` source code is inspected
- **THEN** it SHALL NOT contain a `_producer` async function or `asyncio.ensure_future` call for event production

### Requirement: pdai Capability hooks fire on all run paths
The `RunExecutor` SHALL call `agent_run.next(node)` explicitly (not bare `async for`) so that pdai Capability hooks (`wrap_node_run`, `before_model_request`, `after_node_run`) fire on every run path, including standalone runs, session pool runs, and subagent runs.

#### Scenario: Capability hook fires on standalone run
- **WHEN** an agent with a `wrap_node_run` Capability is run via `BaseAgent.run_stream()` standalone
- **THEN** the `wrap_node_run` hook SHALL be called at least once during execution

#### Scenario: Capability hook fires on session pool run
- **WHEN** an agent with a `before_model_request` Capability is run via `SessionPool.run_stream()`
- **THEN** the `before_model_request` hook SHALL be called before each model request

### Requirement: _run_stream_once removed or refactored
The `BaseAgent._run_stream_once()` method, which implements the standalone producer/consumer pattern bypassing `RunExecutor`, SHALL be removed or refactored to delegate to `RunExecutor`.

#### Scenario: _run_stream_once does not create producer task
- **WHEN** `BaseAgent._run_stream_once()` source code is inspected
- **THEN** it SHALL NOT create an `asyncio.ensure_future` producer task that publishes to EventBus directly

### Requirement: Event ordering preserved after unification
The unified `RunExecutor` path SHALL produce events in the same order as the previous `BaseAgent.run_stream()` path. All existing event ordering tests SHALL pass without modification.

#### Scenario: StreamCompleteEvent is last event
- **WHEN** an agent run completes successfully via any path
- **THEN** `StreamCompleteEvent` SHALL be the last event yielded before the stream closes

#### Scenario: ToolCallStartEvent before ToolCallCompleteEvent
- **WHEN** a tool is called during an agent run
- **THEN** `ToolCallStartEvent` SHALL be yielded before `ToolCallCompleteEvent` for that tool
