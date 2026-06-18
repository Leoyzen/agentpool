## ADDED Requirements

### Requirement: EventEnvelope carries source session metadata
The EventBus SHALL wrap every published event in an `EventEnvelope` that includes the source session ID.

#### Scenario: Publishing an event
- **WHEN** a producer calls `event_bus.publish("child-sid", some_event)`
- **THEN** the EventBus stores and distributes an `EventEnvelope` with `source_session_id="child-sid"` and `event=some_event`

#### Scenario: Consuming an event
- **WHEN** a consumer receives an item from an EventBus subscription queue
- **THEN** the item is an `EventEnvelope` instance
- **AND** `envelope.source_session_id` equals the session ID of the event producer
- **AND** `envelope.event` is the original event object (unmodified)

### Requirement: EventEnvelope provides transparent event access
The EventEnvelope SHALL support attribute access to the wrapped event's properties without explicit unwrapping.

#### Scenario: Accessing event properties through envelope
- **WHEN** an envelope wraps a `StreamCompleteEvent` with `message` attribute
- **THEN** accessing `envelope.message` returns the same value as `envelope.event.message`

#### Scenario: Envelope fields take precedence over event attributes
- **WHEN** an envelope has a field named `source_session_id`
- **THEN** accessing `envelope.source_session_id` returns the envelope's routing metadata
- **AND** accessing `envelope.event.source_session_id` returns the event's attribute (if any)

### Requirement: Consumers use source_session_id for routing
Protocol handlers and other consumers SHALL use `envelope.source_session_id` to determine the target session for event delivery.

#### Scenario: ACP handler routes child session events
- **WHEN** an ACP handler receives an envelope with `source_session_id="child-sid"`
- **THEN** the handler looks up the converter for "child-sid"
- **AND** sends the `SessionNotification` with `session_id="child-sid"`

#### Scenario: Consumer falls back to subscription session
- **WHEN** an envelope's `source_session_id` is empty or missing
- **THEN** the consumer MAY fall back to the session ID of its subscription

### Requirement: Producers do not inject session_id into events
Event producers SHALL NOT mutate event objects to add `session_id` attributes.

#### Scenario: RunExecutor yields events
- **WHEN** RunExecutor yields a `ToolCallStartEvent`
- **THEN** the event does not have a `session_id` attribute set by RunExecutor
- **AND** the EventBus assigns routing metadata via the envelope

#### Scenario: StreamEventEmitter emits events
- **WHEN** StreamEventEmitter emits a `SubAgentEvent`
- **THEN** the event does not have a `session_id` attribute set by the emitter
- **AND** the EventBus assigns routing metadata via the envelope
