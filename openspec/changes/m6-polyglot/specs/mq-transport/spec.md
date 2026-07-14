## ADDED Requirements

### Requirement: MessageQueueTransport implements EventTransport via pluggable message broker backends

`MessageQueueTransport` SHALL implement the `EventTransport` Protocol using a message broker as the communication substrate. The transport SHALL define a `MQBackend` Protocol with `publish(topic, envelope)`, `subscribe(topic) -> AsyncIterator[EventEnvelope]`, and `ack(message_id)` methods. Three concrete backend implementations SHALL be provided: `RedisStreamsBackend`, `NATSJetStreamBackend`, and `KafkaBackend`. Backend selection SHALL be configurable via `lifecycle.mq.backend` YAML field.

#### Scenario: Publish event through Redis Streams backend

- **WHEN** `lifecycle.event_transport: mq` and `lifecycle.mq.backend: redis` are configured
- **AND** the RunLoop publishes a `PartDeltaEvent` through `MessageQueueTransport`
- **THEN** the event SHALL be serialized to an `EventEnvelope` and published to a Redis Stream
- **AND** the stream name SHALL be derived from the session ID (e.g., `agentpool:events:{session_id}`)
- **AND** any consumer subscribed to the stream SHALL receive the EventEnvelope

#### Scenario: Subscribe to events through NATS JetStream backend

- **WHEN** `lifecycle.event_transport: mq` and `lifecycle.mq.backend: nats` are configured
- **AND** a protocol server subscribes to the event stream for session `sess_001`
- **THEN** the `NATSJetStreamBackend` SHALL create a JetStream consumer for the subject `agentpool.events.sess_001`
- **AND** events published by the RunLoop SHALL be delivered to the subscriber as `EventEnvelope` objects

### Requirement: MessageQueueTransport supports distributed protocol servers

`MessageQueueTransport` SHALL enable protocol servers to run on different machines from the RunLoop. The RunLoop and protocol server SHALL communicate exclusively through the message broker — no direct TCP connection is required. Multiple protocol server instances MAY subscribe to the same session's event stream, enabling horizontal scaling of the protocol layer.

#### Scenario: Protocol server on different machine

- **WHEN** a RunLoop is running on machine A with `MessageQueueTransport` configured
- **AND** a protocol server is running on machine B, connected to the same message broker
- **THEN** events published by the RunLoop on machine A SHALL be delivered to the protocol server on machine B
- **AND** steer/followup messages from the protocol server SHALL be delivered back to the RunLoop through the message broker

#### Scenario: Multiple protocol servers subscribe to same session

- **WHEN** two protocol server instances subscribe to the event stream for session `sess_001`
- **THEN** both instances SHALL receive all events published to the session
- **AND** the message broker SHALL use a fan-out delivery model (not competing consumers) for event distribution

### Requirement: MessageQueueTransport is opt-in via lifecycle config

`MessageQueueTransport` SHALL NOT be the default transport. It SHALL be activated only when `lifecycle.event_transport: mq` is specified in YAML configuration. The message broker connection SHALL be configurable via `lifecycle.mq.*` YAML fields, including `backend` (redis/nats/kafka), `url` (broker connection string), and backend-specific options.

#### Scenario: Default transport remains InProcess

- **WHEN** no `lifecycle.event_transport` field is present in YAML configuration
- **THEN** the RunLoop SHALL use `InProcessTransport` as the EventTransport
- **AND** no message broker connection SHALL be established

#### Scenario: MQ transport with Redis backend and custom URL

- **WHEN** YAML configuration contains `lifecycle.event_transport: mq`, `lifecycle.mq.backend: redis`, and `lifecycle.mq.url: "redis://broker.internal:6379"`
- **THEN** the `MessageQueueTransport` SHALL create a `RedisStreamsBackend` connected to `redis://broker.internal:6379`
- **AND** the RunLoop SHALL publish events through Redis Streams on the specified broker

### Requirement: MQBackend Protocol allows custom broker implementations

The `MQBackend` Protocol SHALL define three async methods: `publish(topic: str, envelope: EventEnvelope) -> None`, `subscribe(topic: str) -> AsyncIterator[EventEnvelope]`, and `ack(message_id: str) -> None`. Users SHALL be able to implement custom backends (e.g., RabbitMQ, Apache Pulsar) by implementing this Protocol and registering it via entry points or direct configuration.

#### Scenario: Custom MQBackend implementation

- **WHEN** a user implements a `RabbitMQBackend` class satisfying the `MQBackend` Protocol
- **AND** configures `lifecycle.mq.backend: custom` with `lifecycle.mq.custom_class: "mymodule:RabbitMQBackend"`
- **THEN** the `MessageQueueTransport` SHALL instantiate the custom backend
- **AND** events SHALL be published and subscribed through the custom backend without modification to AgentPool core

### Requirement: MessageQueueTransport provides at-least-once delivery

`MessageQueueTransport` SHALL provide at-least-once delivery semantics for events. Events SHALL NOT be lost in transit — if a consumer disconnects before acknowledging an event, the message broker SHALL redeliver the event when the consumer reconnects. The `ack()` method SHALL be called by the consumer after successfully processing an event to prevent redelivery.

#### Scenario: Event redelivery after consumer crash

- **WHEN** a protocol server receives an event but crashes before calling `ack()`
- **AND** the protocol server restarts and re-subscribes to the event stream
- **THEN** the unacknowledged event SHALL be redelivered by the message broker
- **AND** the protocol server SHALL receive the event again

#### Scenario: Acknowledged events are not redelivered

- **WHEN** a protocol server receives an event and calls `ack(message_id)` successfully
- **THEN** the message broker SHALL NOT redeliver that event to the same consumer
- **AND** the event SHALL be marked as consumed in the broker
