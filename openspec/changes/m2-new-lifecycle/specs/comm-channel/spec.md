## ADDED Requirements

### Requirement: CommChannel is a Protocol with attach, on_state_change, publish, recv, and close methods

CommChannel SHALL be a `@runtime_checkable` Protocol abstracting event delivery and feedback reception. It SHALL own the Journal reference and handle event persistence internally (append for deltas, upsert for entity-state events).

#### Scenario: Protocol conformance

- **WHEN** a class implements `attach`, `on_state_change`, `publish`, `recv`, `close` methods and the `_replaying` attribute
- **THEN** `isinstance(instance, CommChannel)` SHALL return `True`

### Requirement: CommChannel.attach() connects to the RunLoop

`attach(run_loop)` SHALL store a reference to the RunLoop, enabling the feedback loop. This method SHALL be called by RunLoop during `start()`.

#### Scenario: Attach enables feedback

- **WHEN** `attach(run_loop)` is called
- **THEN** the CommChannel SHALL store the RunLoop reference
- **AND** feedback received via `recv()` SHALL be routed to `run_loop.steer()` or `run_loop.followup()` based on RunLoop state

### Requirement: CommChannel.on_state_change() receives state transitions via observer pattern

`on_state_change(state)` SHALL be called by RunLoop on every state transition (idle/running/done). CommChannel implementations SHALL use this to track RunLoop state without directly accessing RunLoop internals. This is critical for bidirectional channels that route incoming messages as steer (when running) or prompt (when idle).

#### Scenario: State change to running

- **WHEN** RunLoop transitions to `running` and calls `on_state_change(RunState.RUNNING)`
- **THEN** the CommChannel SHALL update its internal state tracking
- **AND** subsequent feedback SHALL be routed as steer

#### Scenario: State change to idle

- **WHEN** RunLoop transitions to `idle` and calls `on_state_change(RunState.IDLE)`
- **THEN** the CommChannel SHALL update its internal state tracking
- **AND** subsequent feedback SHALL be routed as followup (new prompt)

### Requirement: CommChannel.publish() journals events before delivery

`publish(event)` SHALL call `journal.append()` for delta events or `journal.upsert(key, event)` for entity-state events BEFORE delivering to the consumer. This ensures crash safety: if the process dies after journal append but before delivery, the event is recoverable.

#### Scenario: Publish delta event

- **WHEN** `publish(PartDeltaEvent(delta="hello"))` is called and `_replaying` is `False`
- **THEN** `journal.append(event)` SHALL be called first
- **AND** the event SHALL then be delivered to the consumer

#### Scenario: Publish entity-state event

- **WHEN** `publish(ToolCallUpdateEvent(tool_call_id="abc", ...))` is called and `_replaying` is `False`
- **THEN** `journal.upsert("tool_call:abc", event)` SHALL be called first
- **AND** the event SHALL then be delivered to the consumer

#### Scenario: Publish during replay skips journaling

- **WHEN** `publish(event)` is called and `_replaying` is `True`
- **THEN** journaling SHALL be skipped (events are already in the journal)
- **AND** the event SHALL be delivered to the consumer

### Requirement: CommChannel uses upsert key derivation based on event type

CommChannel SHALL derive the upsert key from the event type: `ToolCallUpdateEvent` → `f"tool_call:{tool_call_id}"`, `StateUpdate` → `f"state:{session_id}"`, `MessageReplacementEvent` → `f"msg:{message_id}"`, `PlanUpdateEvent` → `f"plan:{plan_id}"`. Events not matching any entity pattern SHALL use `append()` semantics.

#### Scenario: ToolCallUpdate uses upsert with tool_call_id

- **WHEN** a `ToolCallUpdateEvent(tool_call_id="tc_001")` is published
- **THEN** `journal.upsert("tool_call:tc_001", event)` SHALL be called

#### Scenario: PartDelta uses append

- **WHEN** a `PartDeltaEvent(delta="text")` is published
- **THEN** `journal.append(event)` SHALL be called

### Requirement: CommChannel.recv() polls for feedback

`recv()` SHALL return a `Feedback` object if feedback is available, or `None` if no feedback is available. For unidirectional channels, `recv()` SHALL always return `None`.

#### Scenario: Feedback available

- **WHEN** `recv()` is called on a bidirectional channel with pending feedback
- **THEN** a `Feedback` object SHALL be returned

#### Scenario: No feedback available

- **WHEN** `recv()` is called and no feedback is pending
- **THEN** `None` SHALL be returned without blocking

### Requirement: CommChannel.close() cleans up resources

`close()` SHALL release all resources held by the CommChannel (queues, connections, listeners).

#### Scenario: Close releases resources

- **WHEN** `close()` is called
- **THEN** all internal queues and connections SHALL be cleaned up
- **AND** no further events SHALL be accepted by `publish()`

### Requirement: CommChannel has a _replaying flag

CommChannel SHALL have a `_replaying: bool` attribute. When set to `True` by RunLoop during crash recovery, `publish()` SHALL skip journaling (events are already in the journal). When set back to `False`, normal journaling resumes.

#### Scenario: Set replaying flag

- **WHEN** RunLoop sets `comm_channel._replaying = True` during crash recovery
- **THEN** subsequent `publish()` calls SHALL NOT journal events
- **AND** events SHALL still be delivered to the consumer

#### Scenario: Clear replaying flag

- **WHEN** RunLoop sets `comm_channel._replaying = False` after crash recovery
- **THEN** subsequent `publish()` calls SHALL resume journaling events

### Requirement: DirectChannel is the default for standalone execution

`DirectChannel` SHALL implement CommChannel for in-process event delivery. It SHALL use an `asyncio.Queue` to deliver events to the caller. It SHALL be the default when no CommChannel is explicitly provided. `recv()` SHALL always return `None` (unidirectional).

#### Scenario: Direct delivery to caller

- **WHEN** `publish(event)` is called on a `DirectChannel`
- **THEN** the event SHALL be placed on an internal queue
- **AND** the consumer (e.g., `agent.run_stream()`) SHALL be able to iterate the queue

#### Scenario: No feedback in DirectChannel

- **WHEN** `recv()` is called on a `DirectChannel`
- **THEN** `None` SHALL always be returned

### Requirement: ProtocolChannel delivers events via EventBus for session mode

`ProtocolChannel` SHALL implement CommChannel for protocol session execution. It SHALL deliver events via the existing `EventBus` to protocol consumers (ACP, OpenCode, AG-UI). Feedback SHALL arrive via an internal queue, populated by `SessionController.steer()` / `followup()`.

#### Scenario: Protocol event delivery

- **WHEN** `publish(event)` is called on a `ProtocolChannel`
- **THEN** the event SHALL be published to the `EventBus` for the session
- **AND** protocol consumers subscribed to the EventBus SHALL receive the event

#### Scenario: Protocol feedback

- **WHEN** `SessionController.steer(content)` is called
- **THEN** a `Feedback` object SHALL be enqueued in the `ProtocolChannel`'s feedback queue
- **AND** `recv()` SHALL return the `Feedback`

### Requirement: CommChannel owns the Journal reference

CommChannel SHALL hold the Journal reference. When a CommChannel is provided to RunLoop, the RunLoop SHALL inject its Journal instance into the CommChannel to ensure a single Journal instance is shared across resume (RunLoop) and append/upsert (CommChannel).

#### Scenario: Journal injection from RunLoop

- **WHEN** a CommChannel is provided to RunLoop constructor
- **THEN** RunLoop SHALL set `comm_channel._journal = self._journal`
- **AND** the CommChannel SHALL use this Journal for all event persistence

#### Scenario: DirectChannel creates its own Journal

- **WHEN** no CommChannel is provided to RunLoop
- **THEN** a `DirectChannel` SHALL be created with the RunLoop's Journal instance
- **AND** the DirectChannel SHALL use this Journal for event persistence

### Requirement: CommChannel MAY delegate event delivery to EventTransport

CommChannel MAY delegate cross-process event delivery to an EventTransport. When an EventTransport is available (provided by RunLoop), CommChannel SHALL wrap events as `EventEnvelope` and call `event_transport.publish(envelope)` after journaling, enabling external consumers (protocol servers, MQ subscribers) to receive events. For in-process execution with `DirectChannel`, the EventTransport delegation MAY be skipped in favor of direct queue delivery. The EventTransport reference SHALL be injected by RunLoop during `start()`.

#### Scenario: ProtocolChannel delegates to EventTransport

- **WHEN** a `ProtocolChannel` is configured with an EventTransport reference
- **AND** `publish(event)` is called
- **THEN** the event SHALL be journaled first (if not replaying)
- **AND** the event SHALL be wrapped as an `EventEnvelope` and published to the EventTransport
- **AND** the event SHALL also be delivered to the EventBus for in-process consumers

#### Scenario: DirectChannel skips EventTransport for in-process delivery

- **WHEN** a `DirectChannel` is used with default dimensions
- **AND** `publish(event)` is called
- **THEN** the event SHALL be journaled (if not replaying)
- **AND** the event SHALL be delivered directly to the internal asyncio queue
- **AND** EventTransport delegation MAY be skipped (no external consumers)
