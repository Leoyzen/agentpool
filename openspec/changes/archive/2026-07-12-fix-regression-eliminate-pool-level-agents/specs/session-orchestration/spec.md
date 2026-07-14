## MODIFIED Requirements

### Requirement: RunHandle cancel interrupts current turn, not the run loop

`RunHandle.cancel()` SHALL set `run_ctx.cancelled = True` and wake `_idle_event` to unblock idle waits. `cancel()` SHALL call `agent._interrupt()` which cancels only the `_iteration_task` (the LLM API call task). `cancel()` SHALL NOT cancel `run_ctx.current_task` (the `start()` task). After cancellation, the `start()` loop SHALL return to idle state and accept new `steer()` / `followup()` messages.

- `cancel()` SHALL be idempotent — calling it multiple times has no additional effect
- `cancel()` SHALL NOT call `fail()` or set `complete_event` — the run stays alive
- `agent._interrupt()` SHALL only cancel `self._iteration_task`, not `run_ctx.current_task`
- `agent._iteration_task` SHALL be set before each `agent_run.next(node)` call and cleared after
- `RunHandle.complete()` SHALL invoke all registered cleanup callbacks before setting `complete_event`
- `RunHandle.fail()` SHALL invoke all registered cleanup callbacks before setting `complete_event`

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

#### Scenario: Complete invokes cleanup callbacks
- **WHEN** `RunHandle.complete()` is called after a run finishes normally
- **THEN** all registered cleanup callbacks SHALL be invoked in registration order
- **AND** `complete_event` SHALL be set only after all cleanup callbacks have executed
- **AND** the run status transitions to `completed`

#### Scenario: Fail invokes cleanup callbacks
- **WHEN** `RunHandle.fail()` is called after a run encounters an error
- **THEN** all registered cleanup callbacks SHALL be invoked in registration order
- **AND** `complete_event` SHALL be set only after all cleanup callbacks have executed
- **AND** the run status transitions to `failed`
- **AND** `RunFailedEvent` SHALL be published to EventBus before cleanup callbacks run
