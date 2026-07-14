## ADDED Requirements

### Requirement: gRPCTransport implements EventTransport via bidirectional streaming

`gRPCTransport` SHALL implement the `EventTransport` Protocol using a single gRPC bidirectional streaming RPC. The RunLoop (server side) SHALL publish `EventEnvelope` messages as stream responses. The protocol server (client side) SHALL send control messages (steer, followup, close) as stream requests through the same bidirectional stream. The gRPC service definition SHALL be provided as a `.proto` file in the AgentPool package.

#### Scenario: Protocol server connects via gRPC

- **WHEN** `lifecycle.event_transport: grpc` is configured in YAML
- **AND** the RunLoop starts with a `gRPCTransport`
- **THEN** the RunLoop SHALL listen on a gRPC server at the configured address (default: `localhost:50051`)
- **AND** a protocol server process SHALL connect to the gRPC server using the `StreamEvents` bidirectional streaming RPC
- **AND** events published by the RunLoop SHALL be delivered to the protocol server as stream response messages

#### Scenario: Steer message flows from protocol server to RunLoop

- **WHEN** a protocol server sends a steer control message through the gRPC bidirectional stream
- **THEN** the `gRPCTransport` SHALL forward the steer message to the RunLoop's `steer()` method
- **AND** the steer content SHALL be injected into the active Turn

### Requirement: gRPCTransport serializes EventEnvelope as JSON inside protobuf messages

The gRPC service SHALL use protobuf messages for transport framing, but the event payload SHALL be a JSON-encoded `EventEnvelope` string field within the protobuf message. This ensures the EventEnvelope format is identical across all transports (gRPC, MQ, InProcess) and consumable by any language without protobuf code generation for event types.

#### Scenario: gRPC message contains JSON EventEnvelope

- **WHEN** the RunLoop publishes a `PartDeltaEvent` through `gRPCTransport`
- **THEN** the gRPC response message SHALL contain an `envelope_json` field
- **AND** the `envelope_json` field SHALL be a JSON string matching the `EventEnvelope` format defined in the `event-envelope` spec
- **AND** a non-protobuf consumer SHALL be able to parse the JSON string independently

### Requirement: gRPCTransport is opt-in via lifecycle config

`gRPCTransport` SHALL NOT be the default transport. It SHALL be activated only when `lifecycle.event_transport: grpc` is specified in YAML configuration. When not specified, the default `InProcessTransport` SHALL be used. The gRPC server address SHALL be configurable via `lifecycle.grpc.address` (default: `localhost:50051`).

#### Scenario: Default transport remains InProcess

- **WHEN** no `lifecycle.event_transport` field is present in YAML configuration
- **THEN** the RunLoop SHALL use `InProcessTransport` as the EventTransport
- **AND** no gRPC server SHALL be started

#### Scenario: gRPC transport with custom address

- **WHEN** YAML configuration contains `lifecycle.event_transport: grpc` and `lifecycle.grpc.address: "0.0.0.0:9090"`
- **THEN** the `gRPCTransport` SHALL start a gRPC server listening on `0.0.0.0:9090`
- **AND** the RunLoop SHALL publish events through the gRPC server instead of in-process delivery

### Requirement: gRPCTransport provides low-latency single-machine communication

`gRPCTransport` SHALL be optimized for single-machine multi-process scenarios. The gRPC server SHALL use Unix domain sockets when configured for local communication (default). The transport SHALL not introduce more than 1ms of latency per event on localhost compared to `InProcessTransport`.

#### Scenario: Localhost latency within threshold

- **WHEN** a `gRPCTransport` is configured with a Unix domain socket address
- **AND** 1000 events are published from RunLoop to a connected protocol server
- **THEN** the average per-event latency SHALL be less than 1ms
- **AND** no events SHALL be dropped or delivered out of order

### Requirement: gRPCTransport handles connection lifecycle

`gRPCTransport` SHALL handle client connection and disconnection gracefully. When a protocol server disconnects, the RunLoop SHALL continue operating — events SHALL be buffered (up to a configurable limit) and delivered when a client reconnects. When the RunLoop shuts down, the gRPC server SHALL be stopped cleanly, closing all active streams.

#### Scenario: Client disconnects and reconnects

- **WHEN** a protocol server disconnects from the gRPC stream while the RunLoop is running
- **THEN** the RunLoop SHALL continue operating without error
- **AND** events published during disconnection SHALL be buffered up to the configured buffer limit
- **WHEN** a protocol server reconnects
- **THEN** buffered events SHALL be delivered to the reconnected client in order

#### Scenario: RunLoop shutdown closes gRPC server

- **WHEN** the RunLoop calls `close()` while a gRPC server is active
- **THEN** the gRPC server SHALL be gracefully stopped
- **AND** all active bidirectional streams SHALL be closed with a `status=OK` gRPC status
- **AND** no resources (sockets, threads) SHALL be leaked
