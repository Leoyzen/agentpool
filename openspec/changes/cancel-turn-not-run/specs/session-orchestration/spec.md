## MODIFIED Requirements

### Requirement: RunHandle cancel interrupts current turn, not the run loop

`RunHandle.cancel()` SHALL set `run_ctx.cancelled = True` and wake `_idle_event` to unblock idle waits. `cancel()` SHALL call `agent._interrupt()` which cancels only the `_iteration_task` (the LLM API call task). `cancel()` SHALL NOT cancel `run_ctx.current_task` (the `start()` task). After cancellation, the `start()` loop SHALL return to idle state and accept new `steer()` / `followup()` messages.

- `cancel()` SHALL be idempotent — calling it multiple times has no additional effect
- `cancel()` SHALL NOT call `fail()` or set `complete_event` — the run stays alive
- `agent._interrupt()` SHALL only cancel `self._iteration_task`, not `run_ctx.current_task`
- `agent._iteration_task` SHALL be set before each `agent_run.next(node)` call and cleared after

#### Scenario: Cancel during active LLM call
- **WHEN** `cancel()` is called while a native agent turn is executing an LLM API call
- **THEN** `run_ctx.cancelled` is set to `True`
- **AND** `agent._interrupt()` cancels `_iteration_task`
- **AND** `NativeTurn.execute()` catches `CancelledError` from the iteration task
- **AND** checks `run_ctx.cancelled` — since it is `True`, breaks out of the node loop
- **AND** returns WITHOUT yielding `StreamCompleteEvent`
- **AND** `start()` exits the `async for` loop, detects `run_ctx.cancelled`, publishes `RunFailedEvent(exception=RuntimeError("Run cancelled"))`
- **AND** the event converter emits a single `TurnCompleteUpdate(stop_reason="cancelled")`
- **AND** `start()` sets `_turn_complete_event` and returns to idle state
- **AND** `run_ctx.cancelled` remains `True` until the next turn starts (so `handle_prompt()` can observe it and return `stopReason="cancelled"` for legacy clients)
- **AND** `run_ctx.current_task` (the `start()` task) is NOT cancelled

#### Scenario: Cancel during idle
- **WHEN** `cancel()` is called while the RunHandle is idle (waiting on `_idle_event`)
- **THEN** `run_ctx.cancelled` is set to `True`
- **AND** `_idle_event` is set to unblock the wait
- **AND** `start()` wakes up, checks `_closing` (not set), checks `run_ctx.cancelled`
- **AND** since `cancelled` is `True` and no prompts are queued, goes back to idle
- **AND** the RunHandle remains alive

#### Scenario: Cancel then new prompt
- **WHEN** a run is cancelled and then a new prompt arrives via `steer()` or `followup()`
- **THEN** the message is queued in `_message_queue`
- **AND** `_idle_event` is set to wake the idle loop
- **AND** `start()` wakes, resets `run_ctx.cancelled` to `False` BEFORE creating the new turn
- **AND** a new turn is created and executed normally

#### Scenario: External cancellation (session close)
- **WHEN** `CancelledError` is raised in `NativeTurn.execute()` and `run_ctx.cancelled` is `False`
- **THEN** the `CancelledError` is re-raised (not swallowed)
- **AND** `start()` exits via `finally` block
- **AND** the RunHandle is cleaned up

### Requirement: RunHandle exposes per-turn completion event

`RunHandle` SHALL have a `_turn_complete_event: asyncio.Event` field. This event SHALL be set at the end of each turn execution (after `turn.execute()` returns, whether by completion, cancellation, or error). The event SHALL be cleared at the start of each new turn. `complete_event` SHALL remain separate and only be set when the RunHandle itself terminates (session close, unrecoverable error). `_turn_complete_event` SHALL also be set in `start()`'s `finally` block to ensure legacy clients unblock even if the RunHandle dies unexpectedly between turns.

- `_turn_complete_event` SHALL be set after every turn, including cancelled and errored turns
- `_turn_complete_event` SHALL be cleared before each turn starts
- `_turn_complete_event` SHALL be set in `start()`'s `finally` block as a safety net for unexpected RunHandle death
- When a turn is cancelled, `RunFailedEvent` SHALL be published BEFORE setting `_turn_complete_event` so the event consumer processes the cancellation reason first
- `run_ctx.cancelled` SHALL be reset to `False` BEFORE creating a new turn (not just after a cancelled turn)
- `complete_event` SHALL only be set in `start()`'s `finally` block or `_cleanup_run()`

#### Scenario: Turn completes normally
- **WHEN** a turn finishes executing and yields `StreamCompleteEvent`
- **THEN** `_turn_complete_event` is set
- **AND** any legacy client waiting on `_turn_complete_event.wait()` unblocks
- **AND** `start()` continues to the idle/wait cycle

#### Scenario: Turn cancelled
- **WHEN** a turn is cancelled and `NativeTurn.execute()` returns WITHOUT yielding `StreamCompleteEvent`
- **THEN** `start()` detects `run_ctx.cancelled` after the `async for` loop exits
- **AND** publishes `RunFailedEvent(exception=RuntimeError("Run cancelled"))` to EventBus
- **AND** sets `_turn_complete_event` (after publishing `RunFailedEvent` so the event consumer processes it first)
- **AND** uses Python `continue` to skip post-turn processing (message history update, child task waiting)
- **AND** `run_ctx.cancelled` remains `True` — it is NOT reset here (it will be reset before the next turn starts, per the "Cancel then new prompt" scenario)
- **AND** legacy clients unblock from `_turn_complete_event.wait()` and observe `run_handle.run_ctx.cancelled == True`, returning `stopReason="cancelled"`

#### Scenario: New turn starts after previous
- **WHEN** `start()` picks up a queued message and begins a new turn
- **THEN** `_turn_complete_event` is cleared
- **AND** the new turn executes
- **AND** `_turn_complete_event` is set again when the turn finishes

### Requirement: SessionController._cleanup_run clears current_run_id

`SessionController._cleanup_run()` SHALL clear `session.current_run_id` when the run being cleaned up matches the session's current run. This ensures that if a RunHandle dies (unrecoverable error, session close), the session can accept new runs.

- `_cleanup_run(run_id)` SHALL pop the run from `_runs`
- If the session's `current_run_id` equals `run_id`, it SHALL be set to `None`
- If the session's `current_run_id` differs (new run already started), it SHALL NOT be modified

#### Scenario: Run dies from unrecoverable error
- **WHEN** a RunHandle dies due to an unrecoverable error
- **AND** `_cleanup_run()` is called
- **THEN** `session.current_run_id` is set to `None`
- **AND** the next `receive_request()` creates a new RunHandle

#### Scenario: Cleanup after new run already started
- **WHEN** `_cleanup_run(old_run_id)` is called
- **AND** `session.current_run_id` is already set to a new run_id
- **THEN** `session.current_run_id` is NOT modified
- **AND** the new run continues unaffected

### Requirement: SessionController.receive_request detects stale current_run_id

`SessionController.receive_request()` SHALL detect when `session.current_run_id` points to a missing or terminal-status run and clear it before starting a new run. This is a defense-in-depth safety net.

- If `current_run_id` is not `None`, check `self._runs.get(current_run_id)`
- If the run handle is missing or its status is `failed` / `completed` / `done`, clear `current_run_id`
- Then proceed to start a new run via `_start_run_handle()`

#### Scenario: Stale current_run_id after bug
- **WHEN** `receive_request()` is called
- **AND** `session.current_run_id` is set to "run-1"
- **AND** `self._runs.get("run-1")` returns `None` (already cleaned up)
- **THEN** `session.current_run_id` is set to `None`
- **AND** a new RunHandle is created and started

#### Scenario: current_run_id points to failed run
- **WHEN** `receive_request()` is called
- **AND** `session.current_run_id` is set to "run-1"
- **AND** `self._runs.get("run-1")` returns a RunHandle with `status == RunStatus.failed`
- **THEN** `session.current_run_id` is set to `None`
- **AND** a new RunHandle is created and started
