## MODIFIED Requirements

### Requirement: ACP cancel_session does not kill the RunHandle

`cancel_session()` SHALL only call `SessionController.cancel_run_for_session()`. It SHALL NOT call `run_handle.fail()`. Legacy clients blocking on `_turn_complete_event.wait()` SHALL unblock when the cancelled turn finishes — `ACPTurn.execute()` returns WITHOUT yielding `StreamCompleteEvent`, and `start()` publishes `RunFailedEvent` then sets `_turn_complete_event`.

- `cancel_session()` SHALL NOT publish `RunFailedEvent` directly — `start()` publishes it when it detects `run_ctx.cancelled` after the turn
- The event consumer SHALL still send `session/update` with `turn_complete` and `stop_reason="cancelled"` after the cancelled turn finishes
- `handle_prompt()` SHALL wait on `run_handle._turn_complete_event` instead of `run_handle.complete_event` for legacy clients

#### Scenario: Cancel during active ACP turn
- **WHEN** `cancel()` is called while an ACP agent turn is executing
- **THEN** `run_ctx.cancelled` is set to `True`
- **AND** `ACPTurn.execute()` catches `CancelledError` from the stream iteration
- **AND** returns WITHOUT yielding `StreamCompleteEvent`
- **AND** `start()` publishes `RunFailedEvent(exception=RuntimeError("Run cancelled"))`
- **AND** the event converter emits a single `TurnCompleteUpdate(stop_reason="cancelled")`

#### Scenario: Cancel during idle
- **WHEN** `cancel()` is called while the RunHandle is idle (waiting on `_idle_event`)
- **THEN** `run_ctx.cancelled` is set to `True`
- **AND** `_idle_event` is set to unblock the idle wait
- **AND** the RunHandle remains alive

#### Scenario: Cancel then new prompt
- **WHEN** a run is cancelled and then a new prompt arrives via `steer()` or `followup()`
- **THEN** the message is queued in `_message_queue`
- **AND** `_idle_event` is set to wake the idle loop
- **AND** `start()` wakes, resets `run_ctx.cancelled` to `False` BEFORE creating the new turn
- **AND** a new turn is created and executed normally

### Requirement: AgentPoolACPAgent SHALL operate as terminal agent behind Conductor

`AgentPoolACPAgent` SHALL be refactored to operate as a terminal agent in the Conductor's proxy chain. It SHALL respond to standard `initialize` (not `proxy/initialize`), process `session/prompt` directly, and emit `session/update` notifications. The legacy `ACPSession.process_prompt()` dual path SHALL be removed — all prompt processing SHALL route through `ACPProtocolHandler.handle_prompt()`.

#### Scenario: AgentPoolACPAgent as terminal agent
- **WHEN** a Conductor initializes the chain and AgentPoolACPAgent is the terminal component
- **THEN** AgentPoolACPAgent SHALL respond to `initialize` with its capabilities
- **AND** SHALL NOT respond to `proxy/initialize`
- **AND** SHALL process `session/prompt` directly without proxy/successor wrapping

#### Scenario: Legacy process_prompt removed
- **WHEN** a prompt is received by the ACP server
- **THEN** it SHALL route exclusively through `ACPProtocolHandler.handle_prompt()`
- **AND** SHALL NOT fall through to the legacy `ACPSession.process_prompt()` path
