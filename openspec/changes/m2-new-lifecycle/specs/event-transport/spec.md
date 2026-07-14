## ADDED Requirements

### Requirement: EventTransport is a Protocol with publish, subscribe, ack, and close methods

EventTransport SHALL be a `@runtime_checkable` Protocol abstracting the wire protocol between RunLoop and external consumers. It SHALL enable language-agnostic protocol servers and MQ-based decoupling.

#### Scenario: Protocol conformance

- **WHEN** a class implements `publish`, `subscribe`, `ack`, and `close` methods with the correct signatures
- **THEN** `isinstance(instance, EventTransport)` SHALL return `True`

### Requirement: EventTransport.publish() publishes an EventEnvelope

`publish(envelope)` SHALL publish an `EventEnvelope` to the transport. For in-process transport, this pushes to an asyncio queue. For MQ-backed transport, this writes to a message queue.

#### Scenario: Publish envelope

- **WHEN** `publish(EventEnvelope(seq=1, session_id="s1", ...))` is called
- **THEN** the envelope SHALL be delivered to all active subscribers on the matching topic

### Requirement: EventTransport.subscribe() returns an async iterator of envelopes

`subscribe(topic, from_seq)` SHALL return an `AsyncIterator[EventEnvelope]`. For MQ-backed transports, `from_seq` enables replay (consumer requests events from a past position). For in-process transport, this iterates an asyncio queue with optional replay buffer.

#### Scenario: Subscribe to topic

- **WHEN** `subscribe(topic="s1", from_seq=0)` is called
- **THEN** an async iterator SHALL be returned
- **AND** all subsequent envelopes published to topic "s1" SHALL be yielded

#### Scenario: Subscribe with replay

- **WHEN** `subscribe(topic="s1", from_seq=10)` is called on a transport with envelopes at seq 5-20
- **THEN** envelopes with `seq >= 10` SHALL be yielded first
- **AND** then new envelopes as they arrive

### Requirement: EventTransport.ack() acknowledges processed events

`ack(seq)` SHALL acknowledge that an event has been processed. For MQ-backed transports, this commits the consumer offset. For in-process transport, this is a no-op.

#### Scenario: Ack in-process is no-op

- **WHEN** `ack(seq=5)` is called on an `InProcessTransport`
- **THEN** no action SHALL be taken (no-op)

### Requirement: EventTransport.close() cleans up resources

`close()` SHALL release all transport resources (connections, queues, listeners).

#### Scenario: Close transport

- **WHEN** `close()` is called
- **THEN** all transport resources SHALL be released
- **AND** no further envelopes SHALL be accepted by `publish()`

### Requirement: InProcessTransport is the default implementation

`InProcessTransport` SHALL implement EventTransport using in-process asyncio queues. It SHALL require zero infrastructure. It SHALL be the default for standalone and in-process protocol sessions. Events SHALL pass as Python objects without serialization.

#### Scenario: In-process delivery without serialization

- **WHEN** `publish(envelope)` is called on an `InProcessTransport`
- **THEN** the envelope SHALL be delivered as a Python object (no JSON serialization)

#### Scenario: Replay buffer for late subscribers

- **WHEN** an `InProcessTransport` is configured with `replay_buffer_size=100` and a subscriber attaches after 50 events have been published
- **THEN** the subscriber SHALL receive the last 50 events from the replay buffer
- **AND** then receive new events as they arrive

### Requirement: EventEnvelope is the language-agnostic serialization format

`EventEnvelope` SHALL be a dataclass with the following fields, designed to be forward-compatible with M6's Pydantic model: `schema_version: str = "1.0.0"`, `event_type: str`, `session_id: str`, `turn_id: str | None = None`, `timestamp: str` (ISO 8601 format), `payload: dict[str, Any]`, `seq: int | None = None` (optional, set by Journal-backed transports, not set by InProcessTransport), and `metadata: dict[str, Any]` (optional, for extensible metadata). All events SHALL be serialized to EventEnvelope before transport. M2 uses a dataclass; M6 will upgrade to a Pydantic model with the same field names and types.

#### Scenario: Envelope carries Journal sequence number when available

- **WHEN** an EventEnvelope is created for an event by a Journal-backed transport
- **THEN** `envelope.seq` SHALL be the sequence number returned by `journal.append()` or `journal.upsert()`
- **AND** the envelope SHALL NOT generate its own sequence number

#### Scenario: InProcessTransport leaves seq as None

- **WHEN** an EventEnvelope is created by an `InProcessTransport`
- **THEN** `envelope.seq` SHALL be `None` (no journal sequence in-process)
- **AND** `envelope.timestamp` SHALL be an ISO 8601 string

#### Scenario: Envelope is JSON-serializable

- **WHEN** an EventEnvelope is serialized to JSON
- **THEN** all fields SHALL be representable as JSON (no Python-specific types in `payload`)
- **AND** `timestamp` SHALL be an ISO 8601 string (not a Unix float)

### Requirement: EventTransport defaults to InProcessTransport

When no EventTransport is explicitly configured, `InProcessTransport` SHALL be used as the default. RunLoop SHALL create and own the EventTransport lifecycle, passing it to CommChannel for optional delegation during `start()`.

#### Scenario: Default transport

- **WHEN** no EventTransport is provided to RunLoop
- **THEN** RunLoop SHALL create an `InProcessTransport` internally
- **AND** RunLoop SHALL pass the EventTransport reference to the CommChannel
- **AND** events SHALL be delivered in-process without serialization

#### Scenario: RunLoop owns EventTransport lifecycle

- **WHEN** RunLoop is constructed with or without an explicit EventTransport
- **THEN** RunLoop SHALL ensure the EventTransport is available during `start()`
- **AND** RunLoop SHALL call `event_transport.close()` during `close()` to release resources
- **AND** CommChannel MAY use the EventTransport for cross-process event delivery
