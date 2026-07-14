## MODIFIED Requirements

### Requirement: EventTransport Protocol operates on EventEnvelope

The `EventTransport` Protocol SHALL define `publish(envelope: EventEnvelope) -> None` and `subscribe(topic: str | None = None, from_seq: int | None = None) -> AsyncIterator[EventEnvelope]` methods that operate on serialized `EventEnvelope` objects, not raw Python event objects. When `subscribe()` is called without arguments, it SHALL return all events. When called with `topic` and/or `from_seq`, it SHALL return filtered events — this preserves backward compatibility with M2 consumers that call `subscribe(topic=..., from_seq=...)`. `InProcessTransport` SHALL transparently wrap Python events into `EventEnvelope` on publish and yield `EventEnvelope` objects on subscribe. `gRPCTransport` and `MessageQueueTransport` SHALL serialize and transmit the `EventEnvelope` over their respective transports.

#### Scenario: InProcessTransport transparent wrapping

- **WHEN** the RunLoop uses `InProcessTransport` (default) and publishes a `PartDeltaEvent`
- **THEN** the event SHALL be wrapped into an `EventEnvelope` (JSON-serializable) on the publish side
- **AND** the subscriber SHALL receive an `EventEnvelope` object containing `event_type="part_delta"` and the event payload
- **AND** no network serialization SHALL occur (the envelope is constructed in-process but not destructured before delivery to the subscriber)

#### Scenario: gRPCTransport envelope transmission

- **WHEN** the RunLoop uses `gRPCTransport` and publishes a `PartDeltaEvent`
- **THEN** the event SHALL be serialized to an `EventEnvelope` JSON object
- **AND** the envelope SHALL be transmitted via gRPC bidirectional streaming to the connected protocol server
- **AND** the protocol server SHALL receive the `EventEnvelope` and SHALL deserialize it to the appropriate event type

#### Scenario: MessageQueueTransport envelope transmission

- **WHEN** the RunLoop uses `MessageQueueTransport` and publishes a `PartDeltaEvent`
- **THEN** the event SHALL be serialized to an `EventEnvelope` JSON object
- **AND** the envelope SHALL be published to the configured message broker topic
- **AND** subscribed consumers SHALL receive the `EventEnvelope` and SHALL deserialize it to the appropriate event type

### Requirement: EventTransport selection via lifecycle config

The EventTransport implementation SHALL be selected via the `lifecycle.event_transport` YAML field. Valid values SHALL be: `inprocess` (default), `grpc`, and `mq`. When the field is absent, `inprocess` SHALL be used. Each non-default transport SHALL require its corresponding configuration section (`lifecycle.grpc.*` or `lifecycle.mq.*`).

#### Scenario: Default InProcessTransport when no config

- **WHEN** YAML configuration does not contain a `lifecycle.event_transport` field
- **THEN** the RunLoop SHALL use `InProcessTransport` as the EventTransport
- **AND** no additional configuration sections SHALL be required

#### Scenario: gRPC transport selected

- **WHEN** YAML configuration contains `lifecycle.event_transport: grpc`
- **THEN** the RunLoop SHALL use `gRPCTransport` as the EventTransport
- **AND** the `lifecycle.grpc.address` field SHALL be used (defaulting to `localhost:50051` if absent)

#### Scenario: MQ transport selected

- **WHEN** YAML configuration contains `lifecycle.event_transport: mq`
- **THEN** the RunLoop SHALL use `MessageQueueTransport` as the EventTransport
- **AND** the `lifecycle.mq.backend` and `lifecycle.mq.url` fields SHALL be required

### Requirement: subscribe() maintains backward compatibility with M2

The `subscribe()` method SHALL accept optional `topic: str | None = None` and `from_seq: int | None = None` parameters. M2's `subscribe(topic, from_seq)` signature is preserved — parameters are optional, so calling `subscribe()` with no arguments returns all events, and calling `subscribe(topic="s1", from_seq=0)` returns filtered events as in M2. This ensures backward compatibility with M2 consumers without requiring code changes.

#### Scenario: Subscribe with no arguments (M6 style)

- **WHEN** `subscribe()` is called with no arguments
- **THEN** an async iterator SHALL be returned that yields all events regardless of topic
- **AND** no filtering SHALL be applied

#### Scenario: Subscribe with topic and from_seq (M2 backward compat)

- **WHEN** `subscribe(topic="s1", from_seq=0)` is called
- **THEN** an async iterator SHALL be returned that yields events matching topic "s1"
- **AND** events with `seq >= 0` SHALL be yielded (replay support as in M2)

### Requirement: EventTransport preserves event ordering within a session

All EventTransport implementations SHALL preserve event ordering within a single session. Events published in sequence by the RunLoop SHALL be received in the same sequence by subscribers. This invariant SHALL hold for `InProcessTransport` (trivially, in-order delivery), `gRPCTransport` (gRPC stream preserves order), and `MessageQueueTransport` (single partition/stream per session).

#### Scenario: Ordered delivery through InProcessTransport

- **WHEN** the RunLoop publishes events A, B, C in sequence through `InProcessTransport`
- **THEN** the subscriber SHALL receive events A, B, C in that order
- **AND** no reordering SHALL occur

#### Scenario: Ordered delivery through MessageQueueTransport

- **WHEN** the RunLoop publishes events A, B, C in sequence through `MessageQueueTransport`
- **THEN** the subscriber SHALL receive events A, B, C in that order
- **AND** the message broker SHALL use a single stream/partition per session to guarantee ordering
