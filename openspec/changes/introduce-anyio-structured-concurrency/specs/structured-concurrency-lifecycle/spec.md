## ADDED Requirements

### Requirement: Per-session CancelScope for lifecycle management
The system SHALL create an `anyio.CancelScope` for each session upon session creation. All async work scoped to that session (agent runs, MCP connections, consumer tasks, subagent runs) SHALL be created within this scope or as children of this scope. Cancelling the session scope SHALL propagate cancellation to all nested scopes and tasks.

#### Scenario: Session scope creation
- **WHEN** a new session is created via `SessionController.spawn_session()`
- **THEN** a `CancelScope` is created and associated with the session
- **AND** the scope is stored as `SessionState.cancel_scope`

#### Scenario: Session scope cancellation on close
- **WHEN** `SessionController.close_session(session_id)` is called
- **THEN** the session's `CancelScope` is cancelled
- **AND** all tasks created within the scope receive `CancelledError`

#### Scenario: Session scope cleanup on pool shutdown
- **WHEN** `SessionPool.shutdown()` is called
- **THEN** all active session scopes are cancelled
- **AND** all nested scopes and tasks are guaranteed to have completed before `shutdown()` returns

### Requirement: Per-session TaskGroup for consumer tasks
The system SHALL create an `anyio.TaskGroup` per session in `ProtocolEventConsumerMixin` to manage event consumer task lifecycle. Consumer tasks SHALL be spawned via `TaskGroup.start_soon()`. When a session's consumer is stopped, the TaskGroup SHALL be exited, guaranteeing all consumer tasks have completed their cleanup.

#### Scenario: Consumer task spawned in TaskGroup
- **WHEN** `start_event_consumer(session_id)` is called
- **THEN** a per-session `TaskGroup` is created
- **AND** the consumer loop task is spawned via `tg.start_soon()`

#### Scenario: Consumer task cleanup guaranteed at stop
- **WHEN** `stop_event_consumer(session_id)` is called
- **THEN** the per-session `TaskGroup.__aexit__` is invoked
- **AND** all consumer tasks for that session are guaranteed to have completed (including `finally` blocks)
- **AND** the `EventBus.unsubscribe()` call happens after task completion

#### Scenario: Multiple concurrent consumers are isolated
- **WHEN** two sessions have active event consumers
- **AND** one session's consumer is stopped
- **THEN** only that session's `TaskGroup` exits
- **AND** the other session's consumer continues unaffected

### Requirement: TaskGroup for agent run lifecycle
The system SHALL wrap `RunExecutor._iteration_task` in a run-scoped `anyio.TaskGroup`. When the run is cancelled or completes, the TaskGroup SHALL guarantee the background iteration task has finished before the run context is cleaned up.

#### Scenario: Run TaskGroup guarantees iteration task completion
- **WHEN** `RunExecutor.execute()` returns (normally or via cancellation)
- **THEN** the run-scoped `TaskGroup.__aexit__` has completed
- **AND** the `_iteration_task` has finished (including any shielded cleanup)
- **AND** the `active_agent_run` ContextVar has been cleared

#### Scenario: Run cancellation cancels iteration task
- **WHEN** the run is cancelled via `run_ctx.cancelled = True`
- **THEN** the iteration task receives `CancelledError` through the TaskGroup
- **AND** the RunExecutor consumer loop drains remaining events before exiting

### Requirement: Shielded critical cleanup
The system SHALL wrap critical cleanup operations in `CancelScope(shield=True)` to prevent interruption during teardown. Critical cleanup includes: database writes for session persistence, MCP connection close, log flushing, and `complete_event.set()` on `RunHandle`.

#### Scenario: Shielded cleanup survives scope cancellation
- **WHEN** a session scope is cancelled
- **AND** the session has pending database writes
- **THEN** the database writes complete despite the cancellation
- **AND** the `complete_event` is set on the `RunHandle`

#### Scenario: MCP connection close is shielded
- **WHEN** a session is being torn down
- **AND** MCP connections need to send close handshakes
- **THEN** the close operations run in a shielded scope
- **AND** the operations complete within a timeout (5s default)

### Requirement: Deterministic AgentPool shutdown ordering
The system SHALL shut down in a deterministic order: first stop all protocol-level consumer tasks, then shut down the SessionPool, then nullify the SessionPool reference. `SessionPool` SHALL NOT be destroyed while any consumer task's `finally` block is still executing.

#### Scenario: Consumer tasks complete before SessionPool teardown
- **WHEN** `AgentPool.__aexit__` is called
- **THEN** all `ProtocolEventConsumerMixin` consumer tasks are stopped and awaited
- **THEN** `SessionPool.shutdown()` is called
- **THEN** `self._session_pool = None` is set
- **AND** no `RuntimeError("SessionPool not available")` is raised

### Requirement: TurnRunner background tasks use session TaskGroup
The system SHALL replace `TurnRunner._background_tasks` set with the session's `TaskGroup`. Auto-resume tasks spawned by `inject_prompt()`, `queue_prompt()`, `steer()`, and `followup()` SHALL be spawned in the session's `TaskGroup` instead of fire-and-forget.

#### Scenario: Auto-resume task completes before session close
- **WHEN** a session is closed while an auto-resume task is pending
- **THEN** the auto-resume task is cancelled via the session `TaskGroup`
- **AND** the task's cleanup runs before the session state is removed

#### Scenario: Auto-resume task exception is surfaced
- **WHEN** an auto-resume task raises an exception
- **THEN** the exception propagates through the `TaskGroup`
- **AND** the session's error handling is triggered

### Requirement: Non-critical TaskGroup tasks use exception isolation
Non-critical tasks spawned in a `TaskGroup` (auto-resume tasks, tool executions) SHALL be wrapped in exception-catching helpers that prevent one task's failure from cancelling sibling tasks. `anyio.TaskGroup` cancels ALL children when ANY child raises an unhandled exception; exception wrappers prevent this cascade for tasks that MUST NOT kill siblings.

#### Scenario: Auto-resume task failure does not cancel siblings
- **WHEN** two auto-resume tasks are spawned in the same `TaskGroup`
- **AND** one auto-resume task raises an exception
- **THEN** the other auto-resume task continues execution
- **AND** the `TaskGroup` does not cancel the surviving task

#### Scenario: Tool execution failure does not cancel sibling tools
- **WHEN** two tool executions are spawned in the run-scoped `TaskGroup`
- **AND** one tool execution raises an exception
- **THEN** the other tool execution continues
- **AND** the agent run continues (the failing tool result is reported as an error)

#### Scenario: Critical task failure still propagates through TaskGroup
- **WHEN** a critical task (event consumer loop, agent iteration task) raises an unhandled exception
- **THEN** the `TaskGroup` cancels all sibling tasks
- **AND** the session's error handling is triggered
