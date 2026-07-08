## ADDED Requirements

### Requirement: TurnRunner SHALL work with ACP agents via functional ACPTurn

`TurnRunner` SHALL create and execute `ACPTurn` instances for ACP agents. Previously, `TurnRunner` was broken for ACP agents because `ACPAgent.create_turn()` returned an `ACPTurn` that would crash at runtime due to the `cast()` hack. With `ACPClientAdapter`, `ACPTurn` is now functional, and `TurnRunner` SHALL use it as the single execution path.

#### Scenario: TurnRunner executes ACP agent turn
- **WHEN** `TurnRunner` receives a prompt for an ACP agent session
- **THEN** it SHALL call `agent.create_turn()` to get an `ACPTurn`
- **AND** the `ACPTurn` SHALL use `ACPClientAdapter` for ACP communication
- **AND** `TurnRunner` SHALL execute the turn via `turn.execute()`
- **AND** SHALL stream events through the EventBus as `RichAgentStreamEvent`

#### Scenario: TurnRunner handles ACP agent cancellation
- **WHEN** a run is cancelled while `TurnRunner` is executing an `ACPTurn`
- **THEN** `ACPTurn.execute()` SHALL catch `CancelledError` from the stream iteration
- **AND** SHALL return without yielding `StreamCompleteEvent`
- **AND** `TurnRunner` SHALL publish `RunFailedEvent` with cancellation reason

### Requirement: RunHandle SHALL support ACP agent runs

`RunHandle` SHALL track ACP agent runs with the same lifecycle as native agent runs: pending → running → completed/failed. The `RunHandle.complete_event` SHALL be set after ACP turn cleanup finishes. `close_session()` SHALL await this event with a timeout for graceful shutdown.

#### Scenario: ACP run completes normally
- **WHEN** an `ACPTurn` completes successfully
- **THEN** `RunHandle.status` SHALL transition to `completed`
- **AND** `complete_event` SHALL be set
- **AND** `StreamCompleteEvent` SHALL be published to the EventBus

#### Scenario: ACP run fails
- **WHEN** an `ACPTurn` raises an exception
- **THEN** `RunHandle.status` SHALL transition to `failed`
- **AND** `RunFailedEvent` SHALL be published to the EventBus
- **AND** `complete_event` SHALL be set after cleanup

## MODIFIED Requirements

### Requirement: RunHandle cancel interrupts current turn, not the run loop

`RunHandle.cancel()` SHALL set `run_ctx.cancelled = True` and wake `_idle_event` to unblock idle waits. `cancel()` SHALL call `agent._interrupt()` which cancels only the `_iteration_task` (the LLM API call task for native agents, or the stream iteration task for ACP agents). `cancel()` SHALL NOT cancel `run_ctx.current_task` (the `start()` task). After cancellation, the `start()` loop SHALL return to idle state and accept new `steer()` / `followup()` messages.

- `cancel()` SHALL be idempotent — calling it multiple times has no additional effect
- `cancel()` SHALL NOT call `fail()` or set `complete_event` — the run stays alive
- For ACP agents, `agent._interrupt()` SHALL cancel the stream iteration task (the `adapter.stream_events()` consumer), not the background prompt task
- `agent._iteration_task` SHALL be set before each turn execution and cleared after

#### Scenario: Cancel during active ACP turn
- **WHEN** `cancel()` is called while an ACP agent turn is executing
- **THEN** `run_ctx.cancelled` is set to `True`
- **AND** `agent._interrupt()` cancels the stream iteration task
- **AND** `ACPTurn.execute()` catches `CancelledError` from the stream
- **AND** returns WITHOUT yielding `StreamCompleteEvent`
- **AND** `start()` exits the turn loop, detects `run_ctx.cancelled`, publishes `RunFailedEvent`
- **AND** the event converter emits a `TurnCompleteUpdate(stop_reason="cancelled")`
- **AND** `start()` sets `_turn_complete_event` and returns to idle state
- **AND** `run_ctx.current_task` (the `start()` task) is NOT cancelled

#### Scenario: Cancel during idle
- **WHEN** `cancel()` is called while the RunHandle is idle (waiting on `_idle_event`)
- **THEN** `run_ctx.cancelled` is set to `True`
- **AND** `_idle_event` is set to unblock the idle wait
- **AND** `start()` wakes up, checks `_closing` (not set), checks `run_ctx.cancelled`
- **AND** since `cancelled` is `True` and no prompts are queued, goes back to idle
- **AND** the RunHandle remains alive

#### Scenario: Cancel then new prompt
- **WHEN** a run is cancelled and then a new prompt arrives via `steer()` or `followup()`
- **THEN** the message is queued in `_message_queue`
- **AND** `_idle_event` is set to wake the idle loop
- **AND** `start()` wakes, resets `run_ctx.cancelled` to `False` BEFORE creating the new turn
- **AND** a new turn is created and executed normally
