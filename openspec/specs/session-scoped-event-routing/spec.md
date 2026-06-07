## ADDED Requirements

### Requirement: EventBus consumer is session-scoped
The system SHALL create one EventBus consumer per session that runs for the entire session lifecycle, not per HTTP request.

#### Scenario: Session creation starts consumer
- **WHEN** a session is created via `OpenCodeSessionPoolIntegration.create_session()`
- **THEN** an EventBus consumer task is started for that session

#### Scenario: Session closure stops consumer
- **WHEN** a session is closed via `OpenCodeSessionPoolIntegration.close_session()`
- **THEN** the EventBus consumer task for that session is cancelled and cleaned up

#### Scenario: Multiple requests share same consumer
- **WHEN** two HTTP requests are made to the same session
- **THEN** only one EventBus consumer exists for that session
- **AND** both requests' events are consumed by the same consumer

### Requirement: SessionStatusBridge is session-scoped
The system SHALL create one SessionStatusBridge per session that runs for the entire session lifecycle.

#### Scenario: Bridge starts with session
- **WHEN** a session is created
- **THEN** a SessionStatusBridge is started for that session

#### Scenario: Bridge stops with session
- **WHEN** a session is closed
- **THEN** the SessionStatusBridge for that session is stopped

### Requirement: Auto-resume events are delivered
The system SHALL deliver all events produced during auto-resume turns to the frontend via the session-scoped EventBus consumer.

#### Scenario: Auto-resume after subagent completion
- **WHEN** a subagent task completes after the lead agent's turn finishes
- **AND** the subagent calls `inject_prompt()` triggering auto-resume
- **THEN** the auto-resume turn's events are consumed by the session-scoped consumer
- **AND** the events are broadcast to the frontend

#### Scenario: Multiple auto-resume iterations
- **WHEN** multiple injections are queued causing multiple auto-resume iterations
- **THEN** all iterations' events are consumed and broadcast

### Requirement: RunHandle complete_event covers full run_loop
The system SHALL set `RunHandle.complete_event` only after `TurnRunner.run_loop()` fully completes, including all auto-resume turns.

#### Scenario: Sync endpoint waits for auto-resume
- **WHEN** the sync message endpoint calls `receive_request()`
- **AND** the run triggers auto-resume after the first turn
- **THEN** the endpoint waits until auto-resume completes before returning

#### Scenario: Complete event not set mid-loop
- **WHEN** `_run_turn_unlocked()` completes but auto-resume is pending
- **THEN** `complete_event` is NOT set
- **AND** `complete_event` is only set after `_process_queued_work()` returns

## MODIFIED Requirements

### Requirement: OpenCode session pool routing
The OpenCode server SHALL route all message processing through SessionPool and consume events via session-scoped resources.

#### Scenario: Message processing without per-request consumer
- **WHEN** a message is sent to the OpenCode server
- **THEN** the message is routed through `SessionPool.receive_request()`
- **AND** no temporary EventBus consumer is created for the request
- **AND** the response waits for `run_loop()` completion via `RunHandle.complete_event`

#### Scenario: Event consumption via session-scoped consumer
- **WHEN** agent events are published to the EventBus
- **THEN** the session-scoped consumer consumes them
- **AND** converts them to OpenCode events via `OpenCodeEventAdapter`
- **AND** broadcasts them via `ServerState.broadcast_event()`
