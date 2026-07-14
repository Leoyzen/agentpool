## Context

M2 is complete: the RunLoop lifecycle with six pluggable dimensions is operational. The `EventTransport` dimension (defaulting to `InProcessTransport`) defines the boundary between the RunLoop and external consumers (protocol servers). All five protocol servers (ACP, OpenCode, AG-UI, OpenAI API, MCP) are implemented in Python and run in-process with the RunLoop — they receive Python objects directly from the EventBus.

This coupling means protocol servers cannot be implemented in Rust, Go, or TypeScript for performance. They cannot run in separate processes for fault isolation. They cannot use message brokers (Redis/NATS/Kafka) for distributed architectures. RFC-0050 defines `EventTransport` as the language-agnostic boundary, with `EventEnvelope` as the wire format that enables polyglot deployments.

The `InProcessTransport` from M2 passes Python objects through memory — it is zero-overhead but language-bound. M6 introduces the serialized `EventEnvelope` format and two additional transport implementations (`gRPCTransport`, `MessageQueueTransport`) that enable multi-process and distributed deployments while preserving the same RunLoop invariant.

## Goals / Non-Goals

**Goals:**
- Define `EventEnvelope` as a JSON serialization format with schema versioning — consumable by any language (Rust, Go, TypeScript, etc.)
- Implement `gRPCTransport` for single-machine multi-process isolation via gRPC bidirectional streaming
- Implement `MessageQueueTransport` for distributed architecture with pluggable backends (Redis Streams, NATS JetStream, Kafka)
- Provide 3-step evolution: InProcess (default, zero config) → gRPC (opt-in, single-machine multi-process) → MQ (opt-in, distributed)
- Ship a reference protocol server implementation in Rust or Go that consumes EventEnvelopes from gRPC or MQ transport, proving the language-agnostic boundary

**Non-Goals:**
- Distributed RunLoop — the single-process invariant (one RunLoop = one process = one session) is preserved. Distribution happens AROUND the RunLoop (protocol servers scale out), not within it
- Specific MQ broker selection — Redis Streams, NATS JetStream, and Kafka are all supported as pluggable backends; no single broker is mandated
- Protocol server reimplementation in other languages — only a reference implementation is provided to prove the boundary; production protocol servers remain in Python
- EventEnvelope binary format — JSON is chosen for universality; Protocol Buffers are used internally by gRPC transport but EventEnvelope itself is JSON

## Decisions

### Decision 1: EventEnvelope is JSON with schema versioning

**Choice**: `EventEnvelope` is a JSON object with a `schema_version` field, `event_type` discriminator, `session_id`, `turn_id`, `timestamp`, and a `payload` field containing the serialized event data.

**Rationale**: JSON is universally parseable by every language. Schema versioning enables backward-compatible evolution — consumers can check `schema_version` and handle unknown fields gracefully. The envelope is a flat structure (not nested protocol-specific types) so any language can deserialize it without Python-specific type knowledge.

**Alternative considered**: Protocol Buffers as the canonical format — rejected because it requires code generation for each language and adds a compilation step. JSON is zero-friction. The gRPC transport uses protobuf for the transport layer (stream framing, connection management) but the event payload inside is JSON-encoded EventEnvelope.

### Decision 2: 3-step evolution — InProcess → gRPC → MQ

**Choice**: Three transport implementations with progressively broader scope:
1. `InProcessTransport` (default, zero config) — wraps Python events into EventEnvelope on publish, yields EventEnvelope objects on subscribe, no serialization overhead in the hot path
2. `gRPCTransport` (opt-in via `lifecycle.event_transport: grpc`) — single-machine multi-process isolation with strong types and low latency
3. `MessageQueueTransport` (opt-in via `lifecycle.event_transport: mq`) — distributed architecture, horizontal scaling of protocol layer

**Rationale**: Each step adds capability without removing the previous. Users start with InProcess (no config needed), opt into gRPC when they need process isolation, and opt into MQ when they need distributed deployment. The progression is monotonic — MQ requires everything gRPC requires (serialization, envelope format) plus broker infrastructure.

**Alternative considered**: Single pluggable transport with configuration — rejected because the operational characteristics differ fundamentally (in-process function calls vs. TCP streams vs. message broker pub/sub). Separate implementations with a shared Protocol interface is cleaner than one implementation with mode flags.

### Decision 3: gRPC uses bidirectional streaming for event flow

**Choice**: `gRPCTransport` uses a single gRPC bidirectional streaming RPC (`StreamEvents`) where the RunLoop publishes EventEnvelopes as stream messages and the protocol server sends steer/followup messages back through the same stream.

**Rationale**: Bidirectional streaming matches the RunLoop's bidirectional nature — events flow out (RunLoop → consumer) and control messages flow in (consumer → RunLoop, e.g., steer requests). A single long-lived stream avoids connection setup per event and provides natural backpressure via gRPC flow control.

**Alternative considered**: Separate unary RPCs for publish and subscribe — rejected because it requires polling or long-poll for the subscribe direction, adding latency and complexity. WebSocket was considered but gRPC provides stronger typing and built-in flow control.

### Decision 4: MQ supports Redis Streams / NATS JetStream / Kafka — pluggable backend

**Choice**: `MessageQueueTransport` defines a `MQBackend` Protocol with `publish()`, `subscribe()`, and `ack()` methods. Three concrete implementations: `RedisStreamsBackend`, `NATSJetStreamBackend`, `KafkaBackend`. Backend selection via `lifecycle.mq.backend` YAML field.

**Rationale**: Different deployments have different infrastructure. Redis Streams is simplest for single-broker setups. NATS JetStream provides built-in consumer groups. Kafka is the choice for high-throughput distributed systems. The `MQBackend` Protocol allows users to implement custom backends (e.g., RabbitMQ, Pulsar) without modifying AgentPool core.

**Alternative considered**: Single MQ backend (Redis Streams only) — rejected because it forces infrastructure decisions on users. The Protocol abstraction is lightweight (3 methods) and the backend implementations are thin wrappers over client libraries.

### Decision 5: One RunLoop = one process = one session invariant preserved

**Choice**: Distribution happens AROUND the RunLoop, not within it. The RunLoop always executes in a single process. Protocol servers (consumers) can be distributed — multiple protocol server processes subscribe to the same RunLoop's events via MQ transport, but the RunLoop itself is single-process.

**Rationale**: The RunLoop's state machine (idle/running/done), journal, and snapshot store are designed for single-process consistency. Making the RunLoop distributed would require distributed consensus (Raft/Paxos), which is a fundamentally different problem (M7+ territory, if ever). By distributing the consumer side (protocol servers), we get horizontal scaling of the I/O-heavy layer without touching the execution consistency model.

**Alternative considered**: Distributed RunLoop with leader election — explicitly rejected as out of scope. The event-transport boundary enables consumer-side distribution, which addresses the polyglot and scaling needs without RunLoop-level distribution.

### Decision 6: Reference implementation in Rust or Go proving language-agnostic boundary

**Choice**: A reference protocol server (ACP-compatible) implemented in Rust or Go that connects to a RunLoop via gRPC or MQ transport, receives EventEnvelopes, and translates them to ACP protocol messages.

**Rationale**: The language-agnostic claim must be proven, not asserted. A reference implementation validates that EventEnvelope is truly consumable by a non-Python language, that the gRPC/MQ transport works cross-language, and that the protocol translation layer is implementable outside Python. Rust or Go are chosen for performance characteristics and strong type systems.

**Alternative considered**: TypeScript reference implementation — considered but Rust/Go better demonstrate the performance isolation use case (compiled, no GC pauses for protocol handling). TypeScript reference can be added later as a separate contribution.

## Migration Notes

### EventEnvelope: dataclass (M2) → Pydantic v2 model (M6)

`EventEnvelope` was initially defined in M2 as a Python dataclass with fields `seq`, `session_id`, `tenant_id`, `turn_id`, `event_type`, `event_data`, `schema_version`, `timestamp`, and `metadata`. M6 upgrades `EventEnvelope` from a dataclass to a Pydantic v2 model to gain JSON serialization, validation, and schema versioning support.

**M2 (revision 2) already uses M6-compatible field names and types**, so no field migration is needed. M2's `EventEnvelope` revision 2 uses `payload` (not `event_data`), `timestamp: str` (ISO 8601, not `float`), `seq: int | None = None` (optional), and `metadata: dict[str, Any]` (optional) — matching the M6 schema exactly. Both M2 and M6 define the same 8 fields: `schema_version`, `event_type`, `session_id`, `turn_id`, `timestamp`, `payload`, `seq`, `metadata`. The only change M6 makes is upgrading from `@dataclass` to `Pydantic v2 BaseModel` with `model_config = ConfigDict(frozen=True)`.

M6 adds the following methods to `EventEnvelope`:

- `from_event(event: RichAgentStreamEvent, session_id: str, turn_id: str | None) -> EventEnvelope` — classmethod that maps each event variant to its `event_type` discriminator and serializes fields to JSON-native types.
- `to_event() -> RichAgentStreamEvent` — instance method that reconstructs the original event object from `event_type` and `payload`.
- `to_json() -> str` — serializes the envelope to a JSON string using only JSON-native types.
- `from_json(data: str) -> EventEnvelope` — classmethod that deserializes from a JSON string and validates `schema_version` semver pattern.

`InProcessTransport` is updated to transparently wrap on publish — existing M2 code that passes raw events continues to work. The `publish()` method accepts both raw Python events (wrapped via `EventEnvelope.from_event()`) and `EventEnvelope` objects (passed through). The `subscribe()` method yields `EventEnvelope` objects (not unwrapped raw events), consistent with the updated `EventTransport` Protocol. Since `subscribe()` accepts optional `topic` and `from_seq` parameters for backward compatibility with M2, no separate `subscribe_unwrapped()` helper is needed. Existing M2 consumers are updated in task 17.3 to handle `EventEnvelope` objects directly.

### EventTransport.subscribe() backward compatibility

M2 defines `subscribe(topic: str, from_seq: int) -> AsyncIterator[EventEnvelope]`. M6 preserves this signature but makes both parameters optional: `subscribe(topic: str | None = None, from_seq: int | None = None) -> AsyncIterator[EventEnvelope]`. When called without arguments, returns all events. When called with `topic` and/or `from_seq`, returns filtered events. This ensures backward compatibility with M2 consumers that call `subscribe(topic=..., from_seq=...)`.

## Risks / Trade-offs

- **[Risk] Serialization overhead for EventEnvelope** — JSON serialization on every event adds CPU and latency compared to in-process Python object passing. Mitigated by: (1) InProcessTransport wraps on publish but does not serialize to JSON (EventEnvelope objects are passed in-process), (2) JSON is fast for small payloads (typical events are <1KB), (3) gRPC transport uses protobuf framing which is compact. MEDIUM risk for high-throughput scenarios.
- **[Risk] gRPC adds dependency** — `grpcio` and `grpcio-tools` are non-trivial dependencies. Mitigated by: gRPC is an optional extra (`uv sync --extra grpc`), not a core dependency. InProcess transport has zero external deps. LOW risk.
- **[Risk] MQ adds operational complexity** — Running Redis/NATS/Kafka is infrastructure burden. Mitigated by: MQ is opt-in, default is InProcess. Users who need distributed deployment already have broker infrastructure. The `MQBackend` Protocol allows starting with Redis (simplest) and migrating to Kafka without code changes. MEDIUM risk.
- **[Risk] Latency increase for non-InProcess transports** — gRPC adds TCP round-trip latency (~0.1ms localhost). MQ adds broker hop latency (~1-5ms depending on backend). Mitigated by: these transports are opt-in for users who need isolation or distribution — the latency trade-off is explicit. InProcess remains the default. LOW risk for the intended use cases.
- **[Trade-off] JSON over binary format** — JSON is larger on the wire than protobuf/flatbuffers. Chosen for universality and debuggability. Event payloads are small enough that the overhead is negligible compared to LLM token generation latency (100ms+).
- **[Trade-off] Separate transport implementations vs. single configurable transport** — Three implementations (InProcess, gRPC, MQ) share the `EventTransport` Protocol but have fundamentally different connection models. Code duplication is minimal (the Protocol is 2 methods); the benefit is clarity and testability.
