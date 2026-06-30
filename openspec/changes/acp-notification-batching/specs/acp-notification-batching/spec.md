## ADDED Requirements

### Requirement: Replay SHALL batch SessionUpdate delivery

The `ACPNotifications.replay()` method SHALL collect all `SessionUpdate` objects from message conversion before sending, then deliver them in batches via `_batch_session_updates` ext_notification. Batch size SHALL be configurable via `notification_batch_size` (default: 20).

#### Scenario: Batched replay with capable client

- **WHEN** `replay()` is called with 100 messages producing 400 `SessionUpdate` objects
- **AND** the client advertised `_batch_session_updates` support during initialize
- **THEN** the agent SHALL send `ceil(400 / 20) = 20` `_batch_session_updates` ext_notifications
- **AND** each ext_notification SHALL contain at most 20 `SessionUpdate` objects in the `updates` array
- **AND** the total `session/update` notifications sent SHALL be 0

#### Scenario: Fallback replay with non-capable client

- **WHEN** `replay()` is called with 100 messages producing 400 `SessionUpdate` objects
- **AND** the client did NOT advertise `_batch_session_updates` support
- **THEN** the agent SHALL send 400 individual `session/update` notifications
- **AND** each `session/update` notification SHALL contain exactly one `SessionUpdate`

### Requirement: Batch SHALL preserve update ordering

The `_batch_session_updates` ext_notification SHALL deliver `SessionUpdate` objects in the same order they would appear in sequential `session/update` delivery. Updates within a batch SHALL maintain the sequence produced by `_collect_request_updates` and `_collect_response_updates`.

#### Scenario: Tool call ordering within batch

- **WHEN** a `ModelResponse` contains a `ToolCallPart` followed by a `TextPart`
- **AND** the corresponding `ModelRequest` has a `ToolReturnPart`
- **THEN** the `ToolCallStart` update SHALL appear before the `AgentMessageChunk` update in the batch
- **AND** the `ToolCallProgress` update (from `ToolReturnPart`) SHALL appear in a subsequent batch or the same batch at a later index

### Requirement: Batch protocol SHALL use ext_notification

The batch delivery mechanism SHALL use ACP's `ext_notification` with method name `_batch_session_updates`. The notification params SHALL contain `session_id` (str) and `updates` (list of `SessionUpdate` dicts). No new schema type SHALL be added to `SessionNotification`.

#### Scenario: ext_notification format

- **WHEN** a batch of 20 `SessionUpdate` objects is ready for delivery
- **THEN** the agent SHALL call `client.ext_notification("_batch_session_updates", {"session_id": "<id>", "updates": [<update1>, ...]})`
- **AND** the method name SHALL be prefixed with underscore (ACP extension convention)

### Requirement: Replay conversion SHALL be side-effect free

The `_collect_request_updates` and `_collect_response_updates` methods SHALL return `list[SessionUpdate]` without performing any I/O. The `_tool_call_inputs` cache SHALL be populated during collection for use in sequential fallback, but SHALL NOT be consumed during batch delivery.

#### Scenario: Collection without client connection

- **WHEN** `_collect_request_updates` is called with a `ModelRequest`
- **THEN** it SHALL return a `list[SessionUpdate]` without calling any `client.*` method
- **AND** `_tool_call_inputs` SHALL be populated for `ToolCallPart` entries encountered

### Requirement: Batch size SHALL be configurable

`ACPNotifications` SHALL accept `notification_batch_size` (int, default 20) and `notification_flush_interval` (float, default 0.0) as constructor parameters. These SHALL control the maximum number of updates per batch and any inter-batch delay respectively.

#### Scenario: Custom batch size

- **WHEN** `ACPNotifications` is constructed with `notification_batch_size=50`
- **AND** `replay()` produces 200 `SessionUpdate` objects
- **THEN** the agent SHALL send `ceil(200 / 50) = 4` batch notifications
