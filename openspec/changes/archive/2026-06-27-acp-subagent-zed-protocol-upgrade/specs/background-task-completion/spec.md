## ADDED Requirements

### Requirement: child_done_events and _consumer_done_events serve different layers
`child_done_events` (on `AgentRunContext`) tracks framework-level child session completion for RunExecutor re-iteration. `_consumer_done_events` (on `ProtocolEventConsumerMixin`) tracks protocol-level consumer loop exit for ACP completion notification. Both are set when a child session completes — `complete_background_task()` or the `_run_turn_unlocked()` finally safety net sets `child_done_events`, and the consumer loop's finally block sets `_consumer_done_events`. Neither mechanism substitutes for the other.

#### Scenario: Both events set on child completion
- **GIVEN** child session C1 is running with an active event consumer
- **WHEN** C1's turn completes
- **THEN** `child_done_events["C1"]` SHALL be set (by `complete_background_task()` or `_run_turn_unlocked()` finally)
- **AND** `_consumer_done_events["C1"]` SHALL be set (by consumer loop finally block)
- **AND** neither mechanism SHALL substitute for the other

### Requirement: create_child_session registers done_event on parent run_ctx
`AgentContext.create_child_session()` SHALL create an `anyio.Event` per child session and register it on the parent's `AgentRunContext.child_done_events` dict, keyed by child session ID. This enables the RunExecutor to wait for child session completion before finishing the parent agent's turn.

#### Scenario: done_event created and registered
- **WHEN** `create_child_session(agent_name="worker", agent_type="native")` is called
- **AND** `ctx.run_ctx` is not None
- **THEN** an `anyio.Event` SHALL be created and stored as `run_ctx.child_done_events[child_session_id]`
- **AND** the event SHALL be in the unset state

#### Scenario: Multiple children get separate events
- **WHEN** `create_child_session()` is called twice from the same parent context
- **THEN** two separate `anyio.Event` instances SHALL be created
- **AND** both SHALL be registered in `run_ctx.child_done_events` under their respective child session IDs

#### Scenario: No run_ctx available
- **WHEN** `create_child_session()` is called and `ctx.run_ctx` is None
- **THEN** no `done_event` SHALL be created or registered
- **AND** the child session SHALL still be created normally

### Requirement: AgentRunContext.child_done_events replaces pending_background_tasks counter
`AgentRunContext` SHALL use `child_done_events: dict[str, anyio.Event]` instead of `pending_background_tasks: int` and `background_tasks_complete: asyncio.Event` for tracking child session completion. The RunExecutor re-iteration loop SHALL wait on all events in this dict.

#### Scenario: Re-iteration loop waits on child_done_events
- **WHEN** the agent finishes its first iteration
- **AND** `run_ctx.child_done_events` is non-empty
- **THEN** the RunExecutor SHALL snapshot all event values (e.g., `list(run_ctx.child_done_events.values())`) before awaiting
- **AND** SHALL wait until all snapshotted `anyio.Event` values are set
- **AND** after waking, SHALL check `run_ctx.queued_steer_messages` for re-iteration

#### Scenario: Re-iteration loop skips wait when dict is empty
- **WHEN** the agent finishes its first iteration
- **AND** `run_ctx.child_done_events` is empty
- **THEN** the RunExecutor SHALL NOT wait
- **AND** SHALL proceed to check `run_ctx.queued_steer_messages`

#### Scenario: Dict mutation safety during wait
- **GIVEN** the RunExecutor is waiting on 2 snapshotted events
- **WHEN** `complete_background_task()` pops a key from `child_done_events` concurrently
- **THEN** no `RuntimeError: dictionary changed size during iteration` SHALL occur
- **AND** the RunExecutor SHALL continue waiting on the snapshotted events

### Requirement: complete_background_task helper ensures correct steer-then-signal ordering
`AgentRunContext` SHALL provide an `async complete_background_task(child_session_id: str, message: str)` method that calls `steer_callback` first (to queue the steer message), then pops the child's `done_event` from `child_done_events` using `.pop(child_session_id, None)` (returns the event or None), then sets the popped event (if not None) to wake the RunExecutor. The pop-then-set pattern ensures graceful handling when the key was already popped by another path. This ordering (steer before signal) ensures the RunExecutor always sees queued steer messages when it wakes.

#### Scenario: Normal completion flow
- **WHEN** `complete_background_task(child_session_id="C1", message="Result: done")` is called
- **THEN** `steer_callback(session_id, message)` SHALL be called first
- **AND** `child_done_events.pop("C1", None)` SHALL be called second (returns the event)
- **AND** the popped event's `.set()` SHALL be called third (if event is not None)
- **AND** the RunExecutor, upon waking, SHALL find the message in `queued_steer_messages`

#### Scenario: Unknown child session ID
- **WHEN** `complete_background_task(child_session_id="unknown", message="...")` is called
- **AND** the child session ID is not in `child_done_events`
- **THEN** `steer_callback` SHALL still be called
- **AND** the pop SHALL use `.pop("unknown", None)` (no-op, no exception)

#### Scenario: steer_callback is None
- **WHEN** `complete_background_task()` is called and `run_ctx.steer_callback` is None
- **THEN** the steer call SHALL be skipped (no-op)
- **AND** the `done_event` SHALL still be set
- **AND** the key SHALL still be popped from `child_done_events`
- **AND** a warning SHALL be logged

#### Scenario: steer_callback raises exception
- **WHEN** `complete_background_task()` is called and `steer_callback` raises `RuntimeError`
- **THEN** the exception SHALL be caught and logged at error level
- **AND** the event SHALL still be popped (`.pop(child_session_id, None)`) and set (if not None)
- **AND** the key SHALL still be removed from `child_done_events`

#### Scenario: complete_background_task called twice for same child
- **WHEN** `complete_background_task("C1", msg1)` is called, then `complete_background_task("C1", msg2)` is called again
- **THEN** the first call SHALL pop the event and set it
- **AND** the second call SHALL find the key missing (`.pop("C1", None)` returns None)
- **AND** the second call SHALL still call `steer_callback` with msg2
- **AND** no `.set()` SHALL be attempted on the second call (event was already popped, pop returned None)

### Requirement: close_session clears all child_done_events safely
`SessionPool.close_session()` SHALL set all remaining `done_event` values in the parent's `child_done_events` dict and clear the dict. This prevents the RunExecutor from hanging when a session is closed while background tasks are still pending. The iteration SHALL snapshot the dict values before setting to prevent `RuntimeError: dictionary changed size during iteration` if `complete_background_task()` concurrently pops keys.

#### Scenario: Session close unblocks re-iteration loop
- **GIVEN** a parent session with `child_done_events` containing 2 pending events
- **WHEN** `close_session(parent_session_id)` is called
- **THEN** the session's active `run_ctx.cancelled` SHALL be set to True (if a run is active)
- **AND** all `anyio.Event` values SHALL be snapshotted (`list(child_done_events.values())`) then set
- **AND** `child_done_events` SHALL be cleared
- **AND** the RunExecutor re-iteration loop SHALL exit promptly

### Requirement: Synchronous child sessions are harmless
When a tool creates a child session and synchronously awaits its completion (blocking the tool call), the `done_event` is created but set before the RunExecutor reaches the re-iteration loop. This SHALL have no adverse effect on the agent's behavior.

#### Scenario: Sync tool — event set before re-iteration
- **WHEN** a tool calls `create_child_session()` (event created)
- **AND** the tool `await`s `session_pool.run_stream(child_session_id, ...)` (blocks on child run)
- **AND** the child run completes (event set via `complete_background_task` or `_run_turn_unlocked()` finally)
- **AND** the tool returns its result
- **AND** the agent finishes iterating
- **THEN** the RunExecutor SHALL find `child_done_events` empty (event was popped)
- **AND** SHALL proceed without waiting

#### Scenario: Safety net fires without steer (tool didn't call complete_background_task)
- **GIVEN** a tool created a child session but did not call `complete_background_task()`
- **WHEN** the child's `_run_turn_unlocked()` finally block executes
- **THEN** the `done_event` SHALL be set (to unblock RunExecutor)
- **AND** `steer_callback` SHALL NOT be called (no result to deliver)

### Requirement: _run_turn_unlocked finally sets parent done_event for child sessions
When a child session's turn completes in `_run_turn_unlocked()`, the finally block SHALL look up the parent session via `_session.parent_session_id`, then access `parent_session.current_run_id`, then find the parent's `RunHandle` via `sessions._runs`, and set the corresponding `done_event` in `child_done_events`. This provides a framework-level safety net for when tools do not explicitly call `complete_background_task`.

The lookup chain is: `_session.parent_session_id` → `sessions.get_session(parent_id)` → `.current_run_id` → `sessions._runs.get(run_id)` → `.run_ctx` → `.child_done_events.pop(child_sid, None)`. If ANY step in this chain returns None, the finally block SHALL be a no-op without raising an exception. The pop SHALL use `.pop(key, None)` to handle cases where `complete_background_task()` already popped the key.

#### Scenario: Child turn completion sets parent done_event
- **GIVEN** child session C1 has `parent_session_id = "P1"`
- **AND** parent P1 has `current_run_id = "R1"` with `run_ctx.child_done_events = {"C1": <Event>}`
- **WHEN** C1's `_run_turn_unlocked()` finally block executes
- **THEN** the system SHALL look up P1's RunHandle via `sessions._runs.get(P1.current_run_id)`
- **AND** SHALL pop `"C1"` from `child_done_events` using `.pop("C1", None)`
- **AND** SHALL set the popped event (if not None)

#### Scenario: complete_background_task already called by tool
- **GIVEN** child session C1 has `parent_session_id = "P1"`
- **AND** the tool already called `complete_background_task("C1", message)` which popped the key
- **WHEN** C1's `_run_turn_unlocked()` finally block executes
- **THEN** the lookup SHALL find the key missing from `child_done_events`
- **AND** `.pop("C1", None)` SHALL return None
- **AND** SHALL be a no-op (graceful, no exception raised)

#### Scenario: Parent run already completed
- **GIVEN** child session C1 has `parent_session_id = "P1"`
- **AND** parent P1's `current_run_id` is None (parent run already finished)
- **WHEN** C1's `_run_turn_unlocked()` finally block executes
- **THEN** the done_event lookup SHALL fail gracefully (no-op)
- **AND** no exception SHALL be raised

#### Scenario: Parent session not found
- **GIVEN** child session C1 has `parent_session_id = "P1"`
- **AND** session P1 does not exist in `sessions._sessions`
- **WHEN** C1's finally block executes
- **THEN** the lookup SHALL fail gracefully (no-op, no exception)

#### Scenario: Parent RunHandle not found
- **GIVEN** parent P1's `current_run_id = "R1"` but `sessions._runs.get("R1")` returns None
- **WHEN** C1's finally block executes
- **THEN** the lookup SHALL fail gracefully (no-op, no exception)

#### Scenario: Parent run_ctx is None
- **GIVEN** parent's RunHandle exists but `run_ctx` is None
- **WHEN** C1's finally block executes
- **THEN** the lookup SHALL fail gracefully (no-op, no exception)

#### Scenario: No parent_session_id (top-level session)
- **GIVEN** child session C1 has `parent_session_id = None`
- **WHEN** C1's finally block executes
- **THEN** the lookup SHALL fail gracefully (no-op, no exception)

### Requirement: Migration from pending_background_tasks to child_done_events
The existing `pending_background_tasks: int`, `background_tasks_complete: asyncio.Event`, and `_create_set_event()` on `AgentRunContext` SHALL be replaced by `child_done_events: dict[str, anyio.Event]`. The `asyncio.Event` type SHALL be replaced with `anyio.Event` to align with `_consumer_done_events` on `ProtocolEventConsumerMixin`. The RunExecutor re-iteration loop SHALL be updated to use the dict-based approach. Existing tests SHALL be updated accordingly.

#### Scenario: Fields removed
- **WHEN** `AgentRunContext` is instantiated
- **THEN** `pending_background_tasks` and `background_tasks_complete` SHALL NOT exist as fields
- **AND** `child_done_events: dict[str, anyio.Event]` SHALL exist, defaulting to `{}`

#### Scenario: RunExecutor uses dict-based wait
- **WHEN** the RunExecutor re-iteration loop checks for pending background tasks
- **THEN** it SHALL check `bool(run_ctx.child_done_events)` instead of `run_ctx.pending_background_tasks > 0`
- **AND** it SHALL wait on all events in the dict instead of `run_ctx.background_tasks_complete.wait()`

#### Scenario: RunExecutor reset logic updated
- **WHEN** the RunExecutor re-iteration loop resets state before re-iterating with steer messages
- **THEN** it SHALL call `run_ctx.child_done_events.clear()` instead of `pending_background_tasks = 0` + `background_tasks_complete.set()`

#### Scenario: SessionPool.close_session updated
- **WHEN** `SessionPool.close_session()` accesses `run_handle.run_ctx`
- **THEN** it SHALL iterate `child_done_events` values (snapshot first) and set them, then clear the dict
- **AND** it SHALL NOT reference `background_tasks_complete` (field removed)
