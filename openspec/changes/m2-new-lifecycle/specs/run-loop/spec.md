## ADDED Requirements

### Requirement: RunLoop is a state machine with idle/running/done states

RunLoop SHALL maintain a state machine with three states: `idle`, `running`, and `done`. The state machine SHALL transition as follows: `idle → running` when a prompt is available, `running → idle` when a Turn completes and more prompts may arrive, `running → done` when `close()` is called, `idle → done` when `close()` is called. The current state SHALL be readable via the `is_running` property (returns `True` when `running`, `False` otherwise).

#### Scenario: Standalone single-Turn execution

- **WHEN** RunLoop is constructed with an `ImmediateTrigger` and `DirectChannel` (default dimensions)
- **AND** `start(initial_prompt="Hello")` is called
- **THEN** RunLoop SHALL transition: idle → running (executing Turn) → done (no more prompts)
- **AND** exactly one Turn SHALL be executed

#### Scenario: Protocol session multi-Turn execution

- **WHEN** RunLoop is constructed with a `ProtocolTrigger` and `ProtocolChannel`
- **AND** `start()` is called without an initial prompt
- **THEN** RunLoop SHALL transition to `idle` and wait for prompts from the TriggerSource
- **AND** each prompt SHALL trigger a `idle → running → idle` cycle
- **AND** RunLoop SHALL remain in `idle` between prompts until `close()` is called

### Requirement: RunLoop.start() attempts crash recovery before execution

RunLoop's `start()` method SHALL call `journal.resume(snapshot_store)` before beginning normal execution. If `resume()` returns `None`, RunLoop SHALL perform a fresh start. If `resume()` returns a `ResumeResult` with `is_inflight=True`, RunLoop SHALL replay journaled events to the CommChannel (with `_replaying=True` to prevent re-journaling), publish a `StateUpdate(state=IDLE, stop_reason="crash_recovery")`, and resume from the recovered state. If `resume()` returns a `ResumeResult` with `is_inflight=False`, RunLoop SHALL resume from the recovered snapshot state.

#### Scenario: Fresh start with no prior state

- **WHEN** `start()` is called on a RunLoop with a `MemoryJournal` (no prior state)
- **THEN** `journal.resume()` SHALL return `None`
- **AND** RunLoop SHALL save an initial snapshot via `snapshot_store.save()`
- **AND** RunLoop SHALL proceed to normal execution

#### Scenario: Crash recovery from in-flight Turn

- **WHEN** `start()` is called on a RunLoop with a `DurableJournal` that has in-flight entries
- **THEN** `journal.resume()` SHALL return a `ResumeResult` with `is_inflight=True`
- **AND** RunLoop SHALL set `comm_channel._replaying = True`
- **AND** RunLoop SHALL replay all events from `ResumeResult.events` to `comm_channel.publish()`
- **AND** RunLoop SHALL set `comm_channel._replaying = False`
- **AND** RunLoop SHALL publish `StateUpdate(state=IDLE, stop_reason="crash_recovery")`
- **AND** RunLoop SHALL NOT re-execute the interrupted Turn

### Requirement: RunLoop.steer() injects into active Turn

RunLoop SHALL provide a `steer(content: str)` method that injects a steer message into the currently active Turn. Steer messages SHALL be delivered to the Turn's execution context so the model sees them in the next iteration. If no Turn is active (state is `idle`), `steer()` SHALL queue the message as a followup instead.

#### Scenario: Steer during active Turn

- **WHEN** `steer("Use Python 3.13")` is called while RunLoop state is `running`
- **THEN** the steer message SHALL be injected into the active Turn's context
- **AND** the model SHALL receive the steer content in its next iteration

#### Scenario: Steer when idle

- **WHEN** `steer("Process this next")` is called while RunLoop state is `idle`
- **THEN** the message SHALL be queued as a followup for the next Turn
- **AND** RunLoop SHALL wake from idle to process the queued message

### Requirement: RunLoop.followup() queues for next Turn

RunLoop SHALL provide a `followup(content: str)` method that queues a message for the next Turn. Followup messages SHALL NOT interrupt the active Turn — they SHALL be processed only after the current Turn completes and the next Turn begins.

#### Scenario: Followup during active Turn

- **WHEN** `followup("Then check the tests")` is called while RunLoop state is `running`
- **THEN** the message SHALL be added to the message queue
- **AND** the active Turn SHALL continue without interruption
- **AND** the message SHALL be included in the next Turn's prompts

### Requirement: RunLoop.close() performs graceful shutdown

RunLoop SHALL provide a `close()` method that performs graceful shutdown. `close()` SHALL drain pending messages in the queue (processing them as final Turns), transition to `done` state, and clean up all dimension resources (TriggerSource, CommChannel, EventTransport). After `close()`, no further Turns SHALL be executed.

#### Scenario: Close with pending messages

- **WHEN** `close()` is called while RunLoop has pending followup messages
- **THEN** RunLoop SHALL process all pending messages as final Turns
- **AND** after the last Turn completes, RunLoop SHALL transition to `done`
- **AND** `comm_channel.close()`, `trigger_source.close()`, and `event_transport.close()` SHALL be called

#### Scenario: Close while idle

- **WHEN** `close()` is called while RunLoop state is `idle`
- **THEN** RunLoop SHALL transition directly to `done`
- **AND** `comm_channel.close()`, `trigger_source.close()`, and `event_transport.close()` SHALL be called

### Requirement: RunLoop snapshots at Turn boundaries

RunLoop SHALL call `snapshot_store.save()` after each Turn completes and before processing the next prompt. RunLoop SHALL also call `snapshot_store.save_turn_result(turn_id, result)` to record the completed Turn's result for idempotency. Snapshots SHALL NOT be taken mid-Turn.

#### Scenario: Snapshot after Turn completion

- **WHEN** a Turn completes with `turn_id="turn_001"`
- **THEN** RunLoop SHALL call `snapshot_store.save(current_state)` to persist the state snapshot
- **AND** RunLoop SHALL call `snapshot_store.save_turn_result("turn_001", turn_result)` for idempotency
- **AND** the message queue SHALL be cleared

#### Scenario: Idempotency check on crash recovery

- **WHEN** RunLoop is recovering from a crash and a Turn with `turn_id="turn_001"` is about to execute
- **AND** `snapshot_store.has_turn_result("turn_001")` returns `True`
- **THEN** RunLoop SHALL skip execution of that Turn
- **AND** RunLoop SHALL clear the message queue and continue to the next prompt

### Requirement: RunLoop publishes StateUpdate on every state transition

RunLoop SHALL publish a `StateUpdate` event through `comm_channel.publish()` on every state transition. StateUpdate SHALL include `session_id` and `state` (one of `RunState.RUNNING`, `RunState.IDLE`, `RunState.DONE`). For `IDLE` transitions after crash recovery, StateUpdate SHALL include `stop_reason="crash_recovery"`.

#### Scenario: StateUpdate on running transition

- **WHEN** RunLoop transitions from `idle` to `running`
- **THEN** RunLoop SHALL call `comm_channel.on_state_change(RunState.RUNNING)`
- **AND** RunLoop SHALL publish `StateUpdate(session_id=..., state=RunState.RUNNING)` via `comm_channel.publish()`

#### Scenario: StateUpdate on done transition

- **WHEN** RunLoop transitions to `done` (via `close()`)
- **THEN** RunLoop SHALL call `comm_channel.on_state_change(RunState.DONE)`
- **AND** RunLoop SHALL publish `StateUpdate(session_id=..., state=RunState.DONE)` via `comm_channel.publish()`

### Requirement: RunLoop delegates Turn execution to agent's create_turn()

RunLoop SHALL NOT execute Turns directly. Instead, RunLoop SHALL call `agent.create_turn(prompts=message_queue, turn_id=turn_id, ...)` to obtain a Turn object, then iterate `turn.execute()` to stream events. Each event from `turn.execute()` SHALL be published via `comm_channel.publish()`.

#### Scenario: Turn execution and event streaming

- **WHEN** RunLoop has messages in the queue and state is `running`
- **THEN** RunLoop SHALL call `agent.create_turn()` with the current message queue and a generated `turn_id`
- **AND** RunLoop SHALL iterate `turn.execute()` as an async iterator
- **AND** each yielded event SHALL be published via `comm_channel.publish()`
- **AND** after the iterator is exhausted, RunLoop SHALL snapshot the state

### Requirement: RunLoop accepts dimension injections with defaults

RunLoop SHALL accept optional `trigger_source`, `journal`, `snapshot_store`, `comm_channel`, `event_transport`, and `session_id` parameters. When any dimension is `None`, RunLoop SHALL use the default implementation: `ImmediateTrigger` for trigger_source, `MemoryJournal` for journal, `MemorySnapshotStore` for snapshot_store, `DirectChannel` for comm_channel (with the journal injected), `InProcessTransport` for event_transport, and `"default"` for session_id.

#### Scenario: Default dimensions for standalone execution

- **WHEN** RunLoop is constructed with only `agent` and no dimension parameters
- **THEN** RunLoop SHALL use `ImmediateTrigger` as the trigger source
- **AND** RunLoop SHALL use `MemoryJournal` as the journal
- **AND** RunLoop SHALL use `MemorySnapshotStore` as the snapshot store
- **AND** RunLoop SHALL use `DirectChannel` as the comm channel (with the MemoryJournal injected)
- **AND** RunLoop SHALL use `InProcessTransport` as the event transport
- **AND** session_id SHALL be `"default"`

#### Scenario: Custom comm_channel receives journal injection

- **WHEN** RunLoop is constructed with a custom `comm_channel` but no explicit `journal`
- **THEN** RunLoop SHALL create a `MemoryJournal` as the default journal
- **AND** RunLoop SHALL inject the journal into `comm_channel._journal` to ensure a single journal instance

### Requirement: RunLoop owns the EventTransport lifecycle

RunLoop SHALL own the EventTransport lifecycle. During `start()`, RunLoop SHALL call `event_transport` initialization (if applicable). During `close()`, RunLoop SHALL call `event_transport.close()` to release transport resources. CommChannel MAY delegate cross-process event delivery to the EventTransport by calling `event_transport.publish(envelope)` after journaling. When no EventTransport is provided, RunLoop SHALL create an `InProcessTransport` as the default.

#### Scenario: RunLoop starts EventTransport

- **WHEN** RunLoop is constructed with an `InProcessTransport` and `start()` is called
- **THEN** the EventTransport SHALL be available for CommChannel to use for event delivery
- **AND** RunLoop SHALL ensure the EventTransport is ready before entering the main loop

#### Scenario: RunLoop closes EventTransport on shutdown

- **WHEN** `close()` is called on RunLoop
- **THEN** RunLoop SHALL call `event_transport.close()` to release transport resources
- **AND** after close, no further envelopes SHALL be accepted by the EventTransport

#### Scenario: CommChannel delegates to EventTransport

- **WHEN** a CommChannel is configured with an EventTransport reference
- **AND** `publish(event)` is called on the CommChannel
- **THEN** the CommChannel SHALL journal the event (if not replaying)
- **AND** the CommChannel MAY delegate delivery to `event_transport.publish(envelope)` for cross-process consumers
