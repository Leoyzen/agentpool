## ADDED Requirements

### Requirement: SessionController receives and routes all requests with agent-type awareness
The system SHALL route all session-bound requests through `SessionController.receive_request()`. `receive_request()` SHALL be fire-and-forget, returning `None`. Protocol handlers SHALL continue consuming events via `EventBus` subscription before calling `receive_request()`. `receive_request()` SHALL inspect the session's agent type and route accordingly:
- **Native agents (Phase 1)**: acquire `SessionState._request_lock`, then check `SessionState.current_run_id`. If idle, create a `RunHandle` and start execution via existing `TurnRunner`. If active, enqueue via `TurnRunner.inject_prompt()` / `queue_prompt()`.
- **Native agents (Phase 2)**: acquire `SessionState._request_lock`, then check `SessionState.current_run_id`. If idle, create a `RunHandle` with PydanticAI `AgentRun` and start execution via `RunExecutor`. If active, call `pydantic_ai_run.enqueue(..., priority)`.
- **Non-native agents**: delegate to `LegacyTurnRunner.inject_prompt()` / `queue_prompt()` compatibility layer.

#### Phase 1 Scenario: Idle native session receives new request
- **WHEN** `receive_request()` is called on a native session with `current_run_id` equal to `None` (Phase 1)
- **THEN** the system acquires `_request_lock`, verifies `current_run_id` is still `None`
- **AND** creates a new `RunHandle`
- **AND** adds the `RunHandle` to `SessionPool._runs`
- **AND** sets `SessionState.current_run_id` while still holding `_request_lock`
- **AND** releases `_request_lock`
- **AND** initiates turn execution via existing `TurnRunner`

#### Phase 2 Scenario: Idle native session receives new request
- **WHEN** `receive_request()` is called on a native session with `current_run_id` equal to `None` (Phase 2)
- **THEN** the system acquires `_request_lock`, verifies `current_run_id` is still `None`
- **AND** creates a new `RunHandle` with PydanticAI `AgentRun`
- **AND** adds the `RunHandle` to `SessionPool._runs`
- **AND** sets `SessionState.current_run_id` while still holding `_request_lock`
- **AND** releases `_request_lock`
- **AND** initiates turn execution via `RunExecutor`

#### Phase 1 Scenario: Active native session receives follow-up request
- **WHEN** `receive_request()` is called on a native session with `current_run_id` not equal to `None` (Phase 1)
- **THEN** the system acquires `_request_lock`, verifies `current_run_id` is still not `None`
- **AND** delegates to `TurnRunner.inject_prompt()` or `queue_prompt()`
- **AND** the active run continues without interruption
- **AND** releases `_request_lock`

#### Phase 1 Scenario: Idle non-native session receives new request
- **WHEN** `receive_request()` is called on a non-native session with `current_run_id` equal to `None` (Phase 1)
- **THEN** the system acquires `_request_lock`, verifies `current_run_id` is still `None`
- **AND** creates a new `RunHandle`
- **AND** adds the `RunHandle` to `SessionPool._runs`
- **AND** sets `SessionState.current_run_id` while still holding `_request_lock`
- **AND** releases `_request_lock`
- **AND** initiates turn execution via existing `TurnRunner` (still using manual queue during Phase 1)
- **AND** `TurnRunner` acquires `SessionState.turn_lock` for turn serialization

#### Phase 1 Scenario: Active non-native session receives follow-up request
- **WHEN** `receive_request()` is called on a non-native session with `current_run_id` not equal to `None` (Phase 1)
- **THEN** the system acquires `_request_lock`, verifies `current_run_id` is still not `None`
- **AND** delegates to `TurnRunner.inject_prompt()` or `queue_prompt()`
- **AND** `TurnRunner` acquires `SessionState.turn_lock` for turn serialization
- **AND** the active run continues without interruption
- **AND** releases `_request_lock`

#### Phase 2 Scenario: Active native session receives follow-up request
- **WHEN** `receive_request()` is called on a native session with `current_run_id` not equal to `None` (Phase 2)
- **THEN** the system acquires `_request_lock`, verifies `current_run_id` is still not `None`
- **AND** calls `pydantic_ai_run.enqueue()` with `priority='when_idle'`
- **AND** the active run continues without interruption
- **AND** releases `_request_lock`

#### Phase 2 Scenario: Active native session receives steering request
- **WHEN** `receive_request()` is called with an explicit steering flag on a native session with an active run (Phase 2)
- **THEN** the system calls `pydantic_ai_run.enqueue()` with `priority='asap'`
- **AND** the message is injected at the earliest opportunity before the next LLM call

#### Scenario: Non-native session receives request (Phase 2)
- **WHEN** `receive_request()` is called on a non-native session (Phase 2)
- **THEN** the system delegates to `LegacyTurnRunner.inject_prompt()` or `queue_prompt()`
- **AND** `LegacyTurnRunner` acquires `SessionState.turn_lock` for turn serialization
- **AND** existing non-native queue behavior is preserved

#### Scenario: Concurrent requests race on native session
- **WHEN** two `receive_request()` calls arrive for the same idle native session simultaneously
- **THEN** the first caller acquires `_request_lock`, creates a `RunHandle`, sets `current_run_id`, and starts execution
- **AND** the second caller waits for the lock, sees `current_run_id` is no longer `None`, and enqueues its message

### Requirement: RunHandle tracks per-session execution state
`RunHandle` SHALL be a first-class ephemeral object defined in `orchestrator/run.py` with `run_id: str`, `status: "pending" | "running" | "completed" | "failed"`, `run_ctx: AgentRunContext`, `session_id: str`, `agent_type: str`, and an agent-type-specific run reference. For native agents, the reference SHALL be a PydanticAI `AgentRun`. For non-native agents, the reference SHALL be the `LegacyTurnRunner` instance or a task handle. `SessionController` SHALL manage `RunHandle` lifecycle (creation, tracking, cleanup). `SessionState` SHALL hold `current_run_id: str | None` and `_request_lock: asyncio.Lock` but SHALL NOT hold the `RunHandle` object directly.

#### Scenario: Native run creation
- **WHEN** a new native turn starts
- **THEN** `SessionController` creates a `RunHandle` with status `"pending"` and PydanticAI `AgentRun` reference
- **AND** adds it to `SessionPool._runs`
- **AND** sets `SessionState.current_run_id` to the run's ID

#### Scenario: Non-native run creation (Phase 1)
- **WHEN** a new non-native turn starts (Phase 1)
- **THEN** `SessionController` creates a `RunHandle` with status `"pending"`
- **AND** adds it to `SessionPool._runs`
- **AND** sets `SessionState.current_run_id` to the run's ID
- **AND** existing `TurnRunner` acquires `SessionState.turn_lock` for turn serialization

#### Scenario: Non-native run creation (Phase 2)
- **WHEN** a new non-native turn starts (Phase 2)
- **THEN** `LegacyTurnRunner` creates a `RunHandle` with status `"pending"`
- **AND** adds it to `SessionPool._runs`
- **AND** sets `SessionState.current_run_id` to the run's ID
- **AND** acquires `SessionState.turn_lock` for turn serialization

#### Scenario: Run completion cleanup
- **WHEN** a turn completes (successfully or with error)
- **THEN** cleanup acquires `SessionState._request_lock`
- **AND** `RunHandle.status` transitions to `"completed"` or `"failed"`
- **AND** `SessionState.current_run_id` is set to `None`
- **AND** the `RunHandle` object is removed from `SessionPool._runs`
- **AND** `RunHandle.complete_event` is set AFTER all cleanup (unsetting `current_run_id`, removing from `SessionPool._runs`) and AFTER releasing `_request_lock`

#### Scenario: RunHandle cancellation
- **WHEN** `SessionPool.cancel_run(run_id)` is called
- **THEN** it SHALL acquire `SessionState._request_lock` (or the session's lock) before operating
- **AND** find the `RunHandle` in `SessionPool._runs`
- **AND** call `RunHandle.cancel()` which sets `run_ctx.cancelled = True` and cancels `run_ctx.current_task`
- **AND** if the task is already in cleanup (not in `_runs` anymore), return `False` gracefully

**Race safety**: `cancel_run()` must not call `Task.cancel()` while the task is in its `finally` block executing `_cleanup_run()`, as this could inject `CancelledError` into cleanup and leave state dirty. Acquiring `_request_lock` prevents this race.

#### Scenario: Concurrent request during active native run
- **WHEN** a second request arrives while `current_run_id` is not `None`
- **THEN** the system SHALL NOT create a second `RunHandle` for the same session
- **AND** the request SHALL be enqueued via `pydantic_ai_run.enqueue()` instead

### Requirement: BaseAgent run context lookup updated
`BaseAgent._get_session_run_ctx()` SHALL be updated to find the active `RunHandle` via `SessionPool.get_run(session.current_run_id)` instead of reading `session.active_run_ctx` directly. `BaseAgent.get_active_run_context()` SHALL use this updated lookup path as its fallback when `_background_run_ctx` is not set.

#### Scenario: Tool requests active run context
- **WHEN** a tool calls `agent.get_active_run_context()` during a turn
- **THEN** `BaseAgent` finds the session via `session_id`
- **AND** retrieves the `RunHandle` from `SessionPool._runs` using `session.current_run_id`
- **AND** returns `run_handle.run_ctx`

### Requirement: Execution MUST use agent.iter() + next() loop
The system SHALL drive agent execution using PydanticAI's `agent.iter()` API with explicit `agent_run.next()` calls in a loop. Bare `async for node in agent_run:` SHALL NOT be used because `PendingMessageDrainCapability`'s `when_idle` drain only fires at `after_node_run`, which is invoked by `_run_node_with_hooks` (used by `AgentRun.next()` and `Agent.run()`), not by `__anext__`.

#### Scenario: when_idle message queued during active run
- **WHEN** a `when_idle` message is enqueued while a run is active
- **THEN** the message remains queued while the agent processes tool calls and model requests
- **AND** when the agent would otherwise terminate, `PendingMessageDrainCapability` drains the queue at `after_node_run`
- **AND** the run continues with an additional model request

### Requirement: PydanticAI pending message queue replaces manual follow-up prompt queue for native agents only
The system SHALL use PydanticAI's `PendingMessageDrainCapability` for follow-up prompt delivery on native agents. `RunExecutor` (native-agent turn driver) SHALL NOT maintain `_post_turn_prompts` or `_injection_locks` for follow-up prompts. `BaseAgent._run_stream_once()` SHALL NOT contain its own internal prompt continuation loop for native agents.

**CRITICAL**: `PromptInjectionManager.inject()`/`consume()` (tool result augmentation via `after_tool_execute`) is NOT replaced by PydanticAI's queue. This mechanism modifies tool results, not conversation messages. It SHALL be preserved for native agents.

#### Scenario: Tool enqueues steering message on native agent
- **WHEN** a tool calls `ctx.enqueue(content, priority='asap')` during a native turn
- **THEN** PydanticAI's `PendingMessageDrainCapability` drains it before the next `ModelRequest`
- **AND** the message is injected into the active conversation

#### Scenario: External code enqueues follow-up message on native agent
- **WHEN** external code calls `pydantic_ai_run.enqueue(content, priority='when_idle')` while a native run is active
- **THEN** the message remains queued until the agent would otherwise terminate
- **AND** PydanticAI extends the run with an additional model request

#### Scenario: No manual auto-resume needed for native agents
- **WHEN** a follow-up message is queued after a native turn ends
- **THEN** PydanticAI's `after_node_run` hook automatically drains the queue
- **AND** no `_trigger_auto_resume()` or `_process_queued_work()` logic is executed

#### Scenario: Tool result augmentation still works for native agents
- **WHEN** a tool calls `agent.inject_prompt("also check tests")` during a native turn
- **THEN** `PromptInjectionManager.inject()` stores the message
- **AND** `NativeAgentHookManager.after_tool_execute` consumes it via `injection_manager.consume()`
- **AND** the injected context is added to the tool result (wrapped in `<injected-context>` tags)
- **AND** this is separate from PydanticAI's `enqueue()` conversation queue

### Requirement: close_session awaits graceful run completion
`SessionPool.close_session()` SHALL await the active `RunHandle.complete_event` with a 30-second timeout instead of acquiring `turn_lock`. If the timeout expires, it SHALL call `SessionController.cancel_run_for_session()` to forcefully terminate the run, then await `complete_event` again with a short timeout for cleanup. After the run completes or is cancelled, it SHALL proceed with session cleanup.

**Race condition mitigation**: `complete_event` SHALL be set in the run task's `finally` block AFTER all cleanup (`current_run_id = None`, `SessionPool._runs` removal, resource release). This prevents `close_session()` from calling `agent.__aexit__()` while the run task is still using the agent.

#### Scenario: Graceful close with active run
- **WHEN** `close_session()` is called on a session with an active run
- **THEN** it awaits `RunHandle.complete_event` with a 30-second timeout
- **AND** if the run completes within the timeout, session cleanup proceeds normally

#### Scenario: Forceful close on timeout
- **WHEN** `close_session()` is called and the active run does not complete within 30 seconds
- **THEN** it calls `SessionController.cancel_run_for_session()`
- **AND** it awaits `RunHandle.complete_event` again (with a shorter timeout, e.g., 5 seconds) for the run task's cleanup to complete
- **AND** only then proceeds with session cleanup

**Rationale**: After calling `cancel_run()`, the run task's `finally` block still needs time to execute cleanup (unset `current_run_id`, remove from `_runs`). `close_session()` must wait for this cleanup before calling `agent.__aexit__()` to avoid races.

#### Scenario: Reject new requests during close
- **WHEN** `receive_request()` is called on a session where `closing=True`
- **THEN** it SHALL check `closing` while holding `SessionState._request_lock` (or after acquiring it)
- **AND** reject the request with a clear error (e.g., `SessionClosingError`)
- **AND** no new `RunHandle` is created

**TOCTOU prevention**: `close_session()` SHALL acquire `SessionState._request_lock` before setting `closing=True` to prevent requests that are already past the initial `closing` check from acquiring the lock and creating a new run.

### Requirement: Pool-level concurrent run limit
`SessionPool` SHALL support an optional `max_concurrent_runs: int | None` limit. When set, `receive_request()` SHALL check the count of active runs in `SessionPool._runs` before creating a new run. If the limit is reached, it SHALL raise a clear exception (e.g., `MaxConcurrentRunsError`).

#### Scenario: Max concurrent runs reached
- **WHEN** `max_concurrent_runs=10` and 10 runs are already active
- **THEN** a new `receive_request()` SHALL raise `MaxConcurrentRunsError`
- **AND** the caller can retry or queue the request externally

### Requirement: Error propagation via EventBus
Run failures SHALL be published as `RunFailedEvent` on the EventBus. `RunHandle.fail()` SHALL set status to `"failed"`, set `complete_event`, remove from `SessionPool._runs`, and publish `RunFailedEvent` with `run_id`, `session_id`, and `exception` details.

**Breaking change**: `SessionPool.process_prompt()` previously blocked and propagated exceptions synchronously. After migration, runtime errors are published on EventBus via `RunFailedEvent`. Callers that catch exceptions from `process_prompt()` MUST update to subscribe to `RunFailedEvent`.

#### Scenario: Native run crashes with exception
- **WHEN** a native run raises an unhandled exception
- **THEN** `RunHandle.fail()` is called in the run task's `finally` block
- **AND** `RunFailedEvent` is published to EventBus
- **AND** protocol handlers subscribed to EventBus receive the error

#### Scenario: Non-native run crashes with exception
- **WHEN** a non-native run raises an unhandled exception
- **THEN** `LegacyTurnRunner` calls `RunHandle.fail()` in its `finally` block
- **AND** `RunFailedEvent` is published to EventBus
