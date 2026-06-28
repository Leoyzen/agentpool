## MODIFIED Requirements

### Requirement: ACP cancel_session does not kill the RunHandle

`cancel_session()` SHALL only call `SessionController.cancel_run_for_session()`. It SHALL NOT call `run_handle.fail()`. Legacy clients blocking on `_turn_complete_event.wait()` SHALL unblock when the cancelled turn finishes — `NativeTurn.execute()` returns WITHOUT yielding `StreamCompleteEvent`, and `start()` publishes `RunFailedEvent` then sets `_turn_complete_event`.

- `cancel_session()` SHALL NOT publish `RunFailedEvent` directly — `start()` publishes it when it detects `run_ctx.cancelled` after the turn
- The event consumer SHALL still send `session/update` with `turn_complete` and `stop_reason="cancelled"` after the cancelled turn finishes
- `handle_prompt()` SHALL wait on `run_handle._turn_complete_event` instead of `run_handle.complete_event` for legacy clients

#### Scenario: Cancel unblocks legacy client
- **WHEN** a legacy client (no `turn_complete` capability) has a prompt in progress
- **AND** `cancel_session()` is called
- **THEN** `cancel_run_for_session()` sets `run_ctx.cancelled = True` and cancels `_iteration_task`
- **AND** `NativeTurn.execute()` catches the cancellation, returns WITHOUT yielding `StreamCompleteEvent`
- **AND** `start()` detects `run_ctx.cancelled`, publishes `RunFailedEvent(exception=RuntimeError("Run cancelled"))`
- **AND** the event converter emits a single `TurnCompleteUpdate(stop_reason="cancelled")`
- **AND** `start()` sets `_turn_complete_event`
- **AND** `handle_prompt()` unblocks from `_turn_complete_event.wait()`
- **AND** returns `PromptResponse` with `stop_reason="cancelled"`
- **AND** the RunHandle remains alive and idle

#### Scenario: Cancel with turn_complete-capable client
- **WHEN** a `turn_complete`-capable client has a prompt in progress
- **AND** `cancel_session()` is called
- **THEN** `cancel_run_for_session()` sets `run_ctx.cancelled = True` and cancels `_iteration_task`
- **AND** `NativeTurn.execute()` catches the cancellation, returns WITHOUT yielding `StreamCompleteEvent`
- **AND** `start()` publishes `RunFailedEvent(exception=RuntimeError("Run cancelled"))`
- **AND** the event converter receives `RunFailedEvent` and emits `TurnCompleteUpdate(stop_reason="cancelled")`
- **AND** `handle_prompt()` returns `PromptResponse` immediately (no blocking)
- **AND** the RunHandle remains alive and idle

#### Scenario: Cancel then new prompt on same session
- **WHEN** a run is cancelled via `cancel_session()`
- **AND** the user sends a new prompt on the same session
- **THEN** `handle_prompt()` calls `receive_request()`
- **AND** `session.current_run_id` is still valid (RunHandle is alive)
- **AND** `receive_request()` finds the existing RunHandle
- **AND** calls `steer()` to inject the new prompt
- **AND** `start()` wakes from idle, resets `run_ctx.cancelled`, and processes the new prompt
- **AND** events are published normally — no hang
