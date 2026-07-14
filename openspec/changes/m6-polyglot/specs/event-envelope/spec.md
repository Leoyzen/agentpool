## ADDED Requirements

### Requirement: EventEnvelope is a JSON-serializable format with schema versioning

EventEnvelope SHALL be a JSON object containing the following top-level fields: `schema_version` (string, semver), `event_type` (string, discriminator), `session_id` (string), `turn_id` (string or null), `timestamp` (ISO 8601 string), `payload` (JSON object), `seq` (integer or null, optional — set by Journal-backed transports, not set by InProcessTransport), and `metadata` (JSON object, optional — for extensible metadata). The core fields (`schema_version`, `event_type`, `session_id`, `turn_id`, `timestamp`, `payload`) SHALL be present in every envelope. The optional fields (`seq`, `metadata`) MAY be absent or null. The `schema_version` field SHALL follow semantic versioning and SHALL be incremented when breaking changes are made to the envelope structure.

#### Scenario: Serialize a PartDelta event to EventEnvelope

- **WHEN** a `PartDeltaEvent(delta="Hello")` event with `session_id="sess_001"` and `turn_id="turn_001"` is published through an EventTransport
- **THEN** the event SHALL be serialized to a JSON object containing `schema_version`, `event_type="part_delta"`, `session_id="sess_001"`, `turn_id="turn_001"`, `timestamp` (ISO 8601), and `payload` containing `{"delta": "Hello"}`
- **AND** the JSON object SHALL be valid JSON parseable by any JSON parser (Python, Rust, Go, TypeScript)

#### Scenario: EventEnvelope schema version is present and semver-compliant

- **WHEN** any event is serialized to an EventEnvelope
- **THEN** the `schema_version` field SHALL be present and SHALL match the pattern `MAJOR.MINOR.PATCH`
- **AND** the initial schema version SHALL be `1.0.0`

### Requirement: All event types are serializable to EventEnvelope

Every event type defined in the `RichAgentStreamEvent` union SHALL be serializable to and deserializable from an `EventEnvelope`. The `event_type` field SHALL uniquely identify the event variant. Unknown `event_type` values SHALL be deserializable to a generic `UnknownEvent` with the raw payload preserved, enabling forward compatibility.

#### Scenario: Round-trip serialization of ToolCallStartEvent

- **WHEN** a `ToolCallStartEvent(tool_name="bash", tool_args={"command": "ls"})` is serialized to an EventEnvelope and then deserialized
- **THEN** the deserialized event SHALL be a `ToolCallStartEvent` with `tool_name="bash"` and `tool_args={"command": "ls"}`
- **AND** no data SHALL be lost in the round-trip

#### Scenario: Deserialization of unknown event type

- **WHEN** an EventEnvelope with `event_type="future_event_v2"` (not known to the current deserializer) is received
- **THEN** the deserializer SHALL produce an `UnknownEvent` object
- **AND** the `UnknownEvent` SHALL preserve the original `event_type` string and the raw `payload` JSON object
- **AND** no exception SHALL be raised

### Requirement: EventEnvelope is language-agnostic

EventEnvelope SHALL use only JSON-native types (string, number, boolean, null, array, object). No Python-specific types (e.g., `datetime`, `Path`, `Enum`) SHALL appear in the serialized form. Timestamps SHALL be ISO 8601 strings. Binary data SHALL be base64-encoded strings. Enum values SHALL be lowercase string representations.

#### Scenario: Non-Python consumer parses EventEnvelope

- **WHEN** a Rust, Go, or TypeScript JSON parser receives an EventEnvelope
- **THEN** the parser SHALL successfully deserialize the envelope without any Python runtime
- **AND** all field values SHALL be representable in the target language's native JSON types

#### Scenario: Timestamp field is ISO 8601 string

- **WHEN** an EventEnvelope is serialized with a `timestamp` field
- **THEN** the timestamp SHALL be an ISO 8601 formatted string (e.g., `"2026-07-09T12:00:00.000Z"`)
- **AND** the timestamp SHALL NOT be a Unix integer or a Python `datetime` object

### Requirement: EventEnvelope supports backward-compatible schema evolution

When the `schema_version` major version is unchanged, consumers SHALL be able to deserialize envelopes produced by a newer minor/patch version. Unknown fields in the `payload` SHALL be preserved and ignored (not dropped). When the major version changes, consumers MAY reject the envelope with a clear error message.

#### Scenario: Consumer handles newer minor version

- **WHEN** a consumer with schema version `1.0.0` receives an envelope with `schema_version="1.2.0"` containing additional fields in `payload`
- **THEN** the consumer SHALL successfully deserialize the envelope
- **AND** the additional fields SHALL be preserved in the deserialized object's metadata
- **AND** no exception SHALL be raised

#### Scenario: Consumer rejects incompatible major version

- **WHEN** a consumer with schema version `1.0.0` receives an envelope with `schema_version="2.0.0"`
- **THEN** the consumer SHALL raise a `SchemaVersionError` indicating the incompatible major version
- **AND** the error message SHALL include both the expected and received major versions
