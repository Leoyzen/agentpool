## Why

All AgentPool protocol servers (ACP, OpenCode, AG-UI, OpenAI API, MCP) are implemented in Python and run in-process with the RunLoop. This coupling prevents polyglot deployments — protocol servers cannot be implemented in Rust/Go/TypeScript for performance, cannot run in separate processes for isolation, and cannot use message brokers (Redis/NATS/Kafka) for distributed architectures. RFC-0050 defines EventTransport as the language-agnostic boundary, with EventEnvelope as the wire format. This milestone implements the transport layer evolution: InProcess (already default) → gRPC → MessageQueue.

## What Changes

- **`EventEnvelope`**: JSON serialization format with schema versioning for all events flowing through EventTransport. Language-agnostic — consumable by Rust, Go, TypeScript.
- **`gRPCTransport`**: Single-machine multi-process isolation. ProtocolServer runs in separate process, communicates with RunLoop via gRPC bidirectional streaming. Strong types, low latency.
- **`MessageQueueTransport`**: Distributed architecture. Uses Redis Streams / NATS JetStream / Kafka as message broker. ProtocolServer can run on different machine. Enables horizontal scaling of protocol layer.
- **`EventTransport` config**: New YAML `lifecycle.event_transport` field — `inprocess` (default), `grpc`, or `mq`. Each is opt-in.
- **Reference implementation**: A Rust or Go ACP protocol server that consumes EventEnvelopes from gRPC or MQ transport, proving the language-agnostic boundary.

## Capabilities

### New Capabilities

- `event-envelope`: JSON + schema-versioned serialization format for all events. Language-agnostic. Defines the wire protocol between RunLoop and external consumers.
- `grpc-transport`: gRPC-based EventTransport implementation. Single-machine multi-process isolation with strong types and low latency.
- `mq-transport`: Message-queue-based EventTransport implementation. Supports Redis Streams, NATS JetStream, and Kafka backends. Enables distributed protocol layer.

### Modified Capabilities

- `event-transport`: EventTransport Protocol gains `publish(envelope)` and `subscribe() -> envelope` methods that operate on EventEnvelope (serialized) rather than Python objects. InProcessTransport wraps on publish and yields EventEnvelope objects on subscribe.
