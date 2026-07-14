## 1. EventEnvelope — JSON Serialization Format

- [ ] 1.1 Create `src/agentpool/lifecycle/envelope.py` defining `EventEnvelope` as a Pydantic v2 model (not dataclass) with fields: `schema_version: str = "1.0.0"`, `event_type: str`, `session_id: str`, `turn_id: str | None = None`, `timestamp: str` (ISO 8601), `payload: dict[str, Any]`, `seq: int | None = None` (optional, set by Journal-backed transports, not set by InProcessTransport), `metadata: dict[str, Any] = {}` (optional, for extensible metadata). These fields match M2's dataclass definition exactly — M6 upgrades from dataclass to Pydantic v2 with the same field names and types. Add `model_config = ConfigDict(frozen=True)`.
- [ ] 1.2 Implement `EventEnvelope.from_event(event: RichAgentStreamEvent, session_id: str, turn_id: str | None) -> EventEnvelope` classmethod — maps each event variant to its `event_type` discriminator string and serializes fields to JSON-native types (datetime → ISO 8601, Path → str, Enum → lowercase str, bytes → base64 str).
- [ ] 1.3 Implement `EventEnvelope.to_event() -> RichAgentStreamEvent` method — reconstructs the original event object from `event_type` and `payload`. Unknown `event_type` values return `UnknownEvent(event_type=..., payload=...)`.
- [ ] 1.4 Add `UnknownEvent` class to `src/agentpool/agents/events/events.py` with fields `event_type: str` and `payload: dict[str, Any]` — add to the `RichAgentStreamEvent` union type.
- [ ] 1.5 Add `UnknownEvent` to `src/agentpool/agents/events/__init__.py` public exports.
- [ ] 1.6 Define `SCHEMA_VERSION = "1.0.0"` constant and `EventEnvelope.SCHEMA_VERSION` class attribute referencing it.
- [ ] 1.7 Implement `EventEnvelope.to_json() -> str` — serializes to a JSON string using only JSON-native types (no Python-specific types leak through).
- [ ] 1.8 Implement `EventEnvelope.from_json(data: str) -> EventEnvelope` — deserializes from JSON string, validates `schema_version` semver pattern `MAJOR.MINOR.PATCH`.
- [ ] 1.9 Write unit tests for `EventEnvelope` construction: all fields present, `schema_version` defaults to `"1.0.0"`, `timestamp` is ISO 8601 string, `turn_id` nullable, `frozen=True` raises `ValidationError` on mutation.
- [ ] 1.10 Write unit tests for `from_event()` / `to_event()` round-trip: `PartDeltaEvent`, `ToolCallStartEvent`, `ToolCallCompleteEvent`, `StreamCompleteEvent`, `RunStartedEvent` — verify no data loss.
- [ ] 1.11 Write unit tests for unknown event type deserialization: envelope with `event_type="future_event_v2"` produces `UnknownEvent` with preserved `event_type` and raw `payload`, no exception raised.
- [ ] 1.12 Write unit tests for `to_json()` / `from_json()` round-trip: JSON is valid, parseable by `json.loads`, all fields preserved, no Python-specific types in output.
- [ ] 1.13 Write unit tests for schema version compatibility: consumer with `1.0.0` accepts envelope with `1.2.0` (additional payload fields preserved), consumer with `1.0.0` rejects `2.0.0` with `SchemaVersionError`.
- [ ] 1.14 Add `SchemaVersionError` exception to `src/agentpool/lifecycle/envelope.py` — raised when major version mismatch detected during `from_json()`.

## 2. Event Type Registry & Discriminator Mapping

- [ ] 2.1 Create `src/agentpool/lifecycle/event_type_registry.py` defining `EVENT_TYPE_REGISTRY: dict[str, type]` mapping discriminator strings to event classes (e.g., `"part_delta" -> PartDeltaEvent`, `"tool_call_start" -> ToolCallStartEvent`).
- [ ] 2.2 Implement `register_event_type(event_type: str, event_class: type) -> None` function for extending the registry at runtime (for custom event types).
- [ ] 2.3 Implement `serialize_event(event: RichAgentStreamEvent) -> tuple[str, dict[str, Any]]` — returns `(event_type, payload)` using the registry's class-to-discriminator reverse mapping.
- [ ] 2.4 Implement `deserialize_event(event_type: str, payload: dict[str, Any]) -> RichAgentStreamEvent` — returns the correct event instance or `UnknownEvent` for unknown types.
- [ ] 2.5 Write unit tests for the registry: all `RichAgentStreamEvent` variants have unique discriminator strings, round-trip serialization for every variant, `register_event_type()` adds new types, unknown types produce `UnknownEvent`.

## 3. EventTransport Protocol Update — Envelope-Based Interface

- [ ] 3.1 Update `EventTransport` Protocol in `src/agentpool/lifecycle/protocols.py` — change `publish()` signature to `publish(envelope: EventEnvelope) -> None` and `subscribe()` to `subscribe(topic: str | None = None, from_seq: int | None = None) -> AsyncIterator[EventEnvelope]` (operating on serialized envelopes, not raw Python objects). Both parameters are optional — calling `subscribe()` with no arguments returns all events; calling with `topic` and/or `from_seq` returns filtered events (backward compat with M2's `subscribe(topic, from_seq)` signature).
- [ ] 3.2 Update `InProcessTransport` in `src/agentpool/lifecycle/transport.py` — `publish()` wraps incoming Python events into `EventEnvelope.from_event()` transparently, `subscribe()` yields `EventEnvelope` objects (not unwrapped raw events), consistent with the updated `EventTransport` Protocol (task 17.2 overrides this behavior from the initial M2 design). Zero network serialization.
- [ ] 3.3 Write unit tests for `InProcessTransport` transparent wrapping: publish `PartDeltaEvent`, subscriber receives `EventEnvelope` object (not unwrapped), envelope contains correct `event_type` and `payload`, `isinstance` check against `EventTransport` Protocol passes.
- [ ] 3.4 Write unit tests for event ordering preservation: publish events A, B, C through `InProcessTransport`, subscriber receives in same order.
- [ ] 3.5 Write unit tests for `InProcessTransport` replay buffer: late subscriber receives buffered events, then new events in order.

## 4. gRPC Proto Definition & Generated Stubs

- [ ] 4.1 Create `proto/agentpool_event.proto` defining `EventEnvelopeMessage` with `string envelope_json = 1` field (JSON-encoded EventEnvelope string), `ControlMessage` with `string message_type = 1` (steer/followup/close) and `string content = 2`, and `StreamEvents` bidirectional streaming RPC (`stream ControlMessage` in, `stream EventEnvelopeMessage` out).
- [ ] 4.2 Create `proto/agentpool_event_service.proto` or extend the above with `service EventService { rpc StreamEvents(stream ControlMessage) returns (stream EventEnvelopeMessage); }`.
- [ ] 4.3 Create `tools/generate_grpc_stubs.py` script that runs `grpcio-tools.protoc` to generate Python stubs from the `.proto` files into `src/agentpool/lifecycle/grpc/generated/`.
- [ ] 4.4 Add generated stubs to `src/agentpool/lifecycle/grpc/generated/` with `__init__.py` exporting `EventServiceStub`, `EventServiceServicer`, `EventEnvelopeMessage`, `ControlMessage`.
- [ ] 4.5 Add `grpc` optional extra to `pyproject.toml` — includes `grpcio` and `grpcio-tools` dependencies. Verify `uv sync --extra grpc` installs correctly.
- [ ] 4.6 Write a test that imports the generated stubs and verifies `EventServiceStub`, `EventServiceServicer`, `EventEnvelopeMessage`, `ControlMessage` are importable (skipped when grpc extra not installed).

## 5. gRPCTransport — Server Side (RunLoop)

- [ ] 5.1 Create `src/agentpool/lifecycle/grpc/transport.py` defining `gRPCTransport` class implementing `EventTransport` Protocol — constructor takes `address: str = "localhost:50051"`, `buffer_size: int = 1000`.
- [ ] 5.2 Implement `gRPCTransport` gRPC server lifecycle: `start()` creates `grpc.aio.Server`, registers `EventServiceServicer`, starts listening on configured address. `close()` stops server gracefully, closes all active streams with `status=OK`.
- [ ] 5.3 Implement `gRPCTransport.publish(envelope: EventEnvelope) -> None` — serializes envelope to JSON, wraps in `EventEnvelopeMessage(envelope_json=...)`, pushes to connected client streams. If no client connected, buffers up to `buffer_size` envelopes.
- [ ] 5.4 Implement `gRPCTransport.subscribe() -> AsyncIterator[EventEnvelope]` — not used on server side (server publishes, doesn't subscribe). Raises `NotImplementedError` or returns empty iterator.
- [ ] 5.5 Implement `EventServiceServicer` subclass (`_EventServicer`) — `StreamEvents()` method: registers the request stream for control message ingestion, yields buffered + new `EventEnvelopeMessage` responses as they arrive via an internal `asyncio.Queue`.
- [ ] 5.6 Implement control message handling in `_EventServicer.StreamEvents()` — parse incoming `ControlMessage`, route `steer` to `RunLoop.steer()`, `followup` to `RunLoop.followup()`, `close` to stream termination.
- [ ] 5.7 Implement client disconnect/reconnect: when a client stream breaks, mark stream as disconnected, continue buffering events. On reconnect, deliver buffered events in order.
- [ ] 5.8 Implement `gRPCTransport.ack(seq: int) -> None` — no-op for gRPC (streaming transport, no ack semantics needed).
- [ ] 5.9 Write unit tests for `gRPCTransport` server: `start()` begins listening, `publish()` with no client buffers events, client connects and receives buffered events, `close()` stops server cleanly.
- [ ] 5.10 Write unit tests for control message routing: steer message calls `RunLoop.steer()`, followup calls `RunLoop.followup()`, close terminates stream.
- [ ] 5.11 Write unit tests for client disconnect/reconnect: events buffered during disconnect, delivered on reconnect in order, buffer limit enforced.
- [ ] 5.12 Write unit tests for clean shutdown: `close()` stops gRPC server, all active streams closed with `status=OK`, no socket/thread leaks.

## 6. gRPCTransport — Client Side (Protocol Server)

- [ ] 6.1 Create `src/agentpool/lifecycle/grpc/client.py` defining `gRPCEventClient` class — constructor takes `address: str = "localhost:50051"`.
- [ ] 6.2 Implement `gRPCEventClient.connect() -> None` — creates `grpc.aio.Channel`, instantiates `EventServiceStub`, opens `StreamEvents` bidirectional stream.
- [ ] 6.3 Implement `gRPCEventClient.subscribe() -> AsyncIterator[EventEnvelope]` — yields `EventEnvelope` objects parsed from `EventEnvelopeMessage.envelope_json` received on the stream.
- [ ] 6.4 Implement `gRPCEventClient.send_control(message_type: str, content: str) -> None` — sends `ControlMessage` through the bidirectional stream request side.
- [ ] 6.5 Implement `gRPCEventClient.close() -> None` — closes the stream and channel cleanly.
- [ ] 6.6 Write unit tests for `gRPCEventClient`: connect to a test gRPC server, receive published events, send steer control message, close cleanly.
- [ ] 6.7 Write integration test: `gRPCTransport` server + `gRPCEventClient` client in separate asyncio tasks — publish 100 events, verify all received in order, verify latency < 1ms per event on localhost.

## 7. MQBackend Protocol & Base Abstraction

- [ ] 7.1 Create `src/agentpool/lifecycle/mq/backend.py` defining `MQBackend` as `@runtime_checkable` Protocol with `publish(topic: str, envelope: EventEnvelope) -> None`, `subscribe(topic: str) -> AsyncIterator[EventEnvelope]`, `ack(message_id: str) -> None`, and `close() -> None`.
- [ ] 7.2 Create `src/agentpool/lifecycle/mq/base.py` defining `BaseMQBackend` abstract base class with common functionality: topic derivation (`_topic_for_session(session_id) -> str` returns `f"agentpool:events:{session_id}"`), connection state tracking, `_ensure_connected()` guard.
- [ ] 7.3 Write unit tests for `MQBackend` Protocol: dummy implementation passes `isinstance` check, incomplete class fails `isinstance` check, `BaseMQBackend._topic_for_session()` produces correct topic format.

## 8. RedisStreamsBackend

- [ ] 8.1 Create `src/agentpool/lifecycle/mq/redis_backend.py` defining `RedisStreamsBackend(BaseMQBackend)` — constructor takes `url: str = "redis://localhost:6379"`, `consumer_group: str = "agentpool"`, `consumer_name: str = "consumer-1"`.
- [ ] 8.2 Implement `RedisStreamsBackend.connect() -> None` — creates `redis.asyncio.Redis` client from URL, verifies connection with PING.
- [ ] 8.3 Implement `RedisStreamsBackend.publish(topic, envelope) -> None` — serializes envelope to JSON, calls `XADD` on stream `topic` with field `envelope` containing JSON string.
- [ ] 8.4 Implement `RedisStreamsBackend.subscribe(topic) -> AsyncIterator[EventEnvelope]` — creates consumer group if not exists, uses `XREADGROUP` to consume messages, yields `EventEnvelope` parsed from message data, preserves message ID for ack.
- [ ] 8.5 Implement `RedisStreamsBackend.ack(message_id: str) -> None` — calls `XACK` on the stream with the consumer group and message ID.
- [ ] 8.6 Implement `RedisStreamsBackend.close() -> None` — closes Redis connection.
- [ ] 8.7 Write unit tests for `RedisStreamsBackend`: publish then subscribe receives envelope, ack prevents redelivery, unacked messages redelivered on reconnect, topic format matches `agentpool:events:{session_id}`. Use `fakeredis` or mark as `@pytest.mark.integration` with real Redis.
- [ ] 8.8 Write unit tests for connection lifecycle: `connect()` establishes connection, `close()` cleans up, publishing before `connect()` raises error.

## 9. NATSJetStreamBackend

- [ ] 9.1 Create `src/agentpool/lifecycle/mq/nats_backend.py` defining `NATSJetStreamBackend(BaseMQBackend)` — constructor takes `url: str = "nats://localhost:4222"`, `stream_name: str = "agentpool-events"`.
- [ ] 9.2 Implement `NATSJetStreamBackend.connect() -> None` — connects to NATS server, accesses JetStream context, creates stream if not exists.
- [ ] 9.3 Implement `NATSJetStreamBackend.publish(topic, envelope) -> None` — serializes envelope to JSON, publishes to subject `topic` via JetStream `publish()`.
- [ ] 9.4 Implement `NATSJetStreamBackend.subscribe(topic) -> AsyncIterator[EventEnvelope]` — creates JetStream durable consumer for subject, uses `fetch()` or `messages()` iterator, yields `EventEnvelope` parsed from message data.
- [ ] 9.5 Implement `NATSJetStreamBackend.ack(message_id: str) -> None` — calls `ack()` on the NATS message (message_id maps to NATS sequence or ack token).
- [ ] 9.6 Implement `NATSJetStreamBackend.close() -> None` — drains and closes NATS connection.
- [ ] 9.7 Write unit tests for `NATSJetStreamBackend`: publish then subscribe receives envelope, ack prevents redelivery, unacked redelivered, subject format matches `agentpool.events.{session_id}`. Mark as `@pytest.mark.integration` with real NATS.
- [ ] 9.8 Write unit tests for connection lifecycle: `connect()`, `close()`, error on publish before connect.

## 10. KafkaBackend

- [ ] 10.1 Create `src/agentpool/lifecycle/mq/kafka_backend.py` defining `KafkaBackend(BaseMQBackend)` — constructor takes `bootstrap_servers: str = "localhost:9092"`, `consumer_group: str = "agentpool"`, `topic_prefix: str = "agentpool-events"`.
- [ ] 10.2 Implement `KafkaBackend.connect() -> None` — creates `aiokafka.AIOKafkaProducer` and `aiokafka.AIOKafkaConsumer`, starts both.
- [ ] 10.3 Implement `KafkaBackend.publish(topic, envelope) -> None` — serializes envelope to JSON, sends to Kafka topic derived from session ID (one partition per session for ordering).
- [ ] 10.4 Implement `KafkaBackend.subscribe(topic) -> AsyncIterator[EventEnvelope]` — uses `AIOKafkaConsumer` for the topic, yields `EventEnvelope` parsed from message value, preserves offset for ack.
- [ ] 10.5 Implement `KafkaBackend.ack(message_id: str) -> None` — commits the consumer offset (message_id encodes partition + offset).
- [ ] 10.6 Implement `KafkaBackend.close() -> None` — stops producer and consumer, flushes pending messages.
- [ ] 10.7 Write unit tests for `KafkaBackend`: publish then subscribe receives envelope, ack commits offset, unacked redelivered on reconnect, single partition per session guarantees ordering. Mark as `@pytest.mark.integration` with real Kafka.
- [ ] 10.8 Write unit tests for connection lifecycle: `connect()`, `close()`, error on publish before connect.

## 11. MessageQueueTransport

- [ ] 11.1 Create `src/agentpool/lifecycle/mq/transport.py` defining `MessageQueueTransport` class implementing `EventTransport` Protocol — constructor takes `backend: MQBackend`.
- [ ] 11.2 Implement `MessageQueueTransport.publish(envelope: EventEnvelope) -> None` — derives topic from `envelope.session_id`, calls `backend.publish(topic, envelope)`.
- [ ] 11.3 Implement `MessageQueueTransport.subscribe() -> AsyncIterator[EventEnvelope]` — not used on server side (server publishes). Returns empty iterator or raises `NotImplementedError`. Protocol servers use `backend.subscribe(topic)` directly.
- [ ] 11.4 Implement `MessageQueueTransport.ack(message_id: str) -> None` — delegates to `backend.ack(message_id)`.
- [ ] 11.5 Implement `MessageQueueTransport.close() -> None` — calls `backend.close()`.
- [ ] 11.6 Implement `MessageQueueTransport.create_backend(backend_type: str, url: str, **kwargs) -> MQBackend` factory method — maps `"redis"` → `RedisStreamsBackend`, `"nats"` → `NATSJetStreamBackend`, `"kafka"` → `KafkaBackend`, `"custom"` → import from `custom_class` path.
- [ ] 11.7 Write unit tests for `MessageQueueTransport`: publish routes to correct topic, subscribe delegation, ack delegation, close cleans up backend, `create_backend()` produces correct backend type for each string.
- [ ] 11.8 Write unit tests for custom backend: `create_backend("custom", custom_class="mymodule:MyBackend")` instantiates custom class implementing `MQBackend`.

## 12. YAML Configuration Integration

- [ ] 12.1 Add `event_transport: Literal["inprocess", "grpc", "mq"] = "inprocess"` field to `LifecycleConfig` in `src/agentpool_config/lifecycle.py`.
- [ ] 12.2 Add `grpc: GRPCTransportConfig | None = None` field to `LifecycleConfig` — define `GRPCTransportConfig` Pydantic model with `address: str = "localhost:50051"`, `buffer_size: int = 1000`.
- [ ] 12.3 Add `mq: MQTransportConfig | None = None` field to `LifecycleConfig` — define `MQTransportConfig` Pydantic model with `backend: Literal["redis", "nats", "kafka", "custom"]`, `url: str = ""`, `custom_class: str | None = None`, and backend-specific options as `extra="allow"`.
- [ ] 12.4 Write unit tests for config models: defaults are `event_transport="inprocess"`, `grpc=None`, `mq=None`. `grpc` config requires `address`. `mq` config requires `backend` and `url`.
- [ ] 12.5 Write unit tests for config validation: `event_transport: grpc` without `grpc:` section uses defaults, `event_transport: mq` without `mq:` section raises `ValidationError`, `event_transport: mq` with `backend: custom` requires `custom_class`.

## 13. Lifecycle Factory — Transport Selection

- [ ] 13.1 Update `create_dimensions()` in `src/agentpool/lifecycle/factory.py` to accept `lifecycle_config` and produce an `event_transport` key in the returned dict.
- [ ] 13.2 Implement transport selection logic in `create_dimensions()`: `event_transport="inprocess"` (or None) → `InProcessTransport`, `"grpc"` → `gRPCTransport(address=config.grpc.address)`, `"mq"` → `MessageQueueTransport(backend=MessageQueueTransport.create_backend(...))`.
- [ ] 13.3 Wire `gRPCTransport.start()` call into RunLoop startup when gRPC transport is selected — gRPC server must start before events are published.
- [ ] 13.4 Wire `gRPCTransport.close()` / `MessageQueueTransport.close()` into `RunLoop.close()` — transport cleanup happens during RunLoop shutdown.
- [ ] 13.5 Write unit tests for `create_dimensions()`: `None` config returns `InProcessTransport`, `"grpc"` config returns `gRPCTransport` with correct address, `"mq"` config returns `MessageQueueTransport` with correct backend.
- [ ] 13.6 Write integration test: RunLoop with `gRPCTransport` — `start()` starts gRPC server, events published through transport, `close()` stops server cleanly.

## 14. gRPC as Optional Extra — Dependency Isolation

- [ ] 14.1 Add `[project.optional-dependencies] grpc = ["grpcio>=1.60", "grpcio-tools>=1.60"]` to `pyproject.toml`.
- [ ] 14.2 Add conditional imports in `src/agentpool/lifecycle/grpc/transport.py` — `try: import grpc.aio` with `ImportError` message guiding user to `uv sync --extra grpc`.
- [ ] 14.3 Add conditional imports in `src/agentpool/lifecycle/grpc/client.py` — same `ImportError` guard.
- [ ] 14.4 Mark all gRPC transport tests with `@pytest.mark.skipif` when `grpc` extra not installed — use `importorskip("grpc")` pattern.
- [ ] 14.5 Verify `uv sync` (without `--extra grpc`) does not install `grpcio` and all non-gRPC tests pass.
- [ ] 14.6 Verify `uv sync --extra grpc` installs `grpcio` and `grpcio-tools` and gRPC tests run.

## 15. MQ Backend Dependencies — Optional Extras

- [ ] 15.1 Add `[project.optional-dependencies] mq-redis = ["redis[hiredis]>=5.0"]` to `pyproject.toml`.
- [ ] 15.2 Add `[project.optional-dependencies] mq-nats = ["nats-py>=2.6"]` to `pyproject.toml`.
- [ ] 15.3 Add `[project.optional-dependencies] mq-kafka = ["aiokafka>=0.10"]` to `pyproject.toml`.
- [ ] 15.4 Add conditional imports in each backend module — `ImportError` with guidance to install the correct extra.
- [ ] 15.5 Mark MQ backend tests with `importorskip` for their respective libraries — `pytest.importorskip("redis")`, `pytest.importorskip("nats")`, `pytest.importorskip("aiokafka")`.
- [ ] 15.6 Verify `uv sync` (without MQ extras) does not install any MQ client libraries and all non-MQ tests pass.

## 16. Reference Protocol Server (Rust or Go)

- [ ] 16.1 Create `reference-servers/` directory at repository root with `README.md` explaining the reference implementation purpose.
- [ ] 16.2 Create `reference-servers/grpc-acp-server/` with a Rust project (using `tonic` for gRPC) or Go project (using `grpc-go`) — copies the `.proto` file, generates language-specific stubs.
- [ ] 16.3 Implement the reference server's main loop: connect to AgentPool gRPC server, open `StreamEvents` bidirectional stream, receive `EventEnvelopeMessage` responses.
- [ ] 16.4 Implement EventEnvelope JSON parsing in the reference server — deserialize the `envelope_json` field into a language-native struct matching the EventEnvelope schema (`schema_version`, `event_type`, `session_id`, `turn_id`, `timestamp`, `payload`).
- [ ] 16.5 Implement ACP protocol translation in the reference server — map `EventEnvelope` event types to ACP protocol messages (e.g., `part_delta` → ACP `task_update` with text delta, `tool_call_start` → ACP `task_update` with tool invocation, `stream_complete` → ACP `task_complete`).
- [ ] 16.6 Implement steer control message in the reference server — send `ControlMessage(message_type="steer", content="...")` through the gRPC stream back to the RunLoop.
- [ ] 16.7 Write a test script (`reference-servers/test_integration.sh`) that starts AgentPool with `lifecycle.event_transport: grpc`, starts the reference server, sends a prompt, and verifies events are received by the reference server.
- [ ] 16.8 Document the reference server build and run instructions in `reference-servers/grpc-acp-server/README.md`.

## 17. InProcessTransport — Envelope Transparency Update

- [ ] 17.1 Update `InProcessTransport.publish()` in `src/agentpool/lifecycle/transport.py` — if input is a raw Python event (not `EventEnvelope`), wrap via `EventEnvelope.from_event()`. If already an `EventEnvelope`, pass through.
- [ ] 17.2 Update `InProcessTransport.subscribe()` — yield `EventEnvelope` objects (not unwrapped events) to match the updated Protocol. The `topic` and `from_seq` parameters are accepted for backward compat with M2 consumers but are used for filtering only. The separate `subscribe_unwrapped()` helper is no longer needed since `subscribe()` with optional parameters handles both use cases.
- [ ] 17.3 Update existing `InProcessTransport` consumers (EventBus, ProtocolEventConsumerMixin) to handle `EventEnvelope` directly — ensure no behavior change for existing protocol servers. Existing M2 consumers calling `subscribe(topic=..., from_seq=...)` continue to work without modification.
- [ ] 17.4 Write regression tests: all existing tests using `InProcessTransport` pass without modification — event delivery, ordering, replay buffer behavior unchanged. Verify `subscribe(topic="s1", from_seq=0)` (M2 style) and `subscribe()` (M6 style) both work correctly.
- [ ] 17.5 Write unit tests for `subscribe()` with optional parameters: calling with no arguments yields all events, calling with `topic` filters by topic, calling with `from_seq` enables replay — behavior identical to pre-M6 for M2-style calls.

## 18. End-to-End Transport Integration Tests

- [ ] 18.1 Write integration test: InProcessTransport with EventEnvelope — publish events, subscribe, verify envelope format on the wire, verify round-trip deserialization.
- [ ] 18.2 Write integration test: gRPCTransport end-to-end — RunLoop publishes events via gRPC, `gRPCEventClient` receives `EventEnvelope` objects, events match what was published, ordering preserved.
- [ ] 18.3 Write integration test: gRPC steer round-trip — client sends steer control message, RunLoop injects into active Turn, Turn output reflects the steer.
- [ ] 18.4 Write integration test: gRPC client disconnect/reconnect — events buffered during disconnect, delivered in order on reconnect.
- [ ] 18.5 Write integration test: MessageQueueTransport with RedisStreamsBackend — publish events, subscribe from another process (simulated via separate backend instance), events delivered, ack works.
- [ ] 18.6 Write integration test: MessageQueueTransport multi-consumer — two subscribers on same session stream both receive all events (fan-out, not competing consumers).
- [ ] 18.7 Write integration test: transport selection via YAML config — `lifecycle.event_transport: grpc` produces `gRPCTransport`, `lifecycle.event_transport: mq` produces `MessageQueueTransport`, no config produces `InProcessTransport`.
- [ ] 18.8 Write integration test: at-least-once delivery for MQ — consumer crashes mid-processing, unacked event redelivered on reconnect.

## 19. Performance & Latency Verification

- [ ] 19.1 Write benchmark test: `InProcessTransport` vs `gRPCTransport` latency — publish 1000 events, measure average per-event latency, assert gRPC localhost latency < 1ms per event.
- [ ] 19.2 Write benchmark test: EventEnvelope serialization overhead — serialize/deserialize 1000 events, measure total time, assert JSON serialization < 0.1ms per event for typical payloads (<1KB).
- [ ] 19.3 Write benchmark test: gRPC event ordering — publish 1000 events, verify all received in order, no drops, no duplicates.
- [ ] 19.4 Write benchmark test: MQ throughput — publish 1000 events through Redis Streams, measure events/second, assert no event loss.

## 20. Integration Verification

- [ ] 20.1 Run full test suite: `uv run pytest` — all tests must pass without modification (gRPC and MQ tests skipped when extras not installed).
- [ ] 20.2 Run mypy: `uv run --no-group docs mypy src/agentpool/lifecycle/` — no type errors.
- [ ] 20.3 Run ruff: `uv run ruff check src/agentpool/lifecycle/` — no lint errors.
- [ ] 20.4 Run mypy on config models: `uv run --no-group docs mypy src/agentpool_config/lifecycle.py` — no type errors.
- [ ] 20.5 Run ruff on config models: `uv run ruff check src/agentpool_config/lifecycle.py` — no lint errors.
- [ ] 20.6 Verify default behavior unchanged: `agentpool run assistant "Hello"` with no `lifecycle.event_transport` config uses `InProcessTransport`, output identical to pre-M6.
- [ ] 20.7 Verify gRPC transport: `agentpool run assistant "Hello"` with `lifecycle.event_transport: grpc` config starts gRPC server, events flow to connected client.
- [ ] 20.8 Verify ACP server with InProcess: `agentpool serve-acp config.yml` (no lifecycle config) — ACP server works identically to pre-M6.
- [ ] 20.9 Verify gRPC extra isolation: `uv sync` (no extras) → all non-gRPC tests pass, gRPC tests skipped. `uv sync --extra grpc` → gRPC tests run and pass.
- [ ] 20.10 Verify MQ extra isolation: `uv sync` (no extras) → all non-MQ tests pass, MQ tests skipped. `uv sync --extra mq-redis` → Redis backend tests run.
- [ ] 20.11 Verify reference server: `reference-servers/test_integration.sh` passes — Rust/Go server receives EventEnvelopes from gRPC transport and translates to ACP messages.
