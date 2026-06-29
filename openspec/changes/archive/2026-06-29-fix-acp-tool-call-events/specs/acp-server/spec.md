## ADDED Requirements

### Requirement: PartDeltaEvent handler does not generate new tool call IDs

The ACPEventConverter PartDeltaEvent handler SHALL NOT call `delta.as_part()`. When a `PartDeltaEvent` arrives with a `tool_call_id`, the handler SHALL look up the existing `_ToolState` by that ID. If no state exists, the handler SHALL yield no ACP session updates. The handler SHALL NOT create a new `_ToolState` from a `PartDeltaEvent`.

#### Scenario: PartDeltaEvent with known tool_call_id
- **WHEN** a `PartDeltaEvent` arrives with a `tool_call_id` that matches an existing `_ToolState`
- **THEN** the handler SHALL look up the existing state
- **AND** SHALL yield no ACP session updates (streaming deltas are not forwarded)
- **AND** SHALL NOT call `delta.as_part()`
- **AND** SHALL NOT create a new `_ToolState`
- **AND** SHALL NOT emit a `ToolCallStart` notification

#### Scenario: PartDeltaEvent with unknown tool_call_id
- **WHEN** a `PartDeltaEvent` arrives with a `tool_call_id` that does not match any existing `_ToolState`
- **THEN** the handler SHALL yield no ACP session updates
- **AND** SHALL NOT create a new `_ToolState`

### Requirement: ToolCallProgressEvent handler extracts tool_input and tool_name

The ACPEventConverter ToolCallProgressEvent handler SHALL extract `tool_input` and `tool_name` from the event. When `tool_input` is not `None`, the handler SHALL update `_ToolState.raw_input` and `_ToolState.title`. When `tool_name` is not `None` and the state has `"unknown"` as tool name, the handler SHALL update `_ToolState.tool_name`. The handler SHALL include `raw_input` in the emitted ACP `tool_call_update` notification.

#### Scenario: ToolCallProgressEvent with tool_input
- **WHEN** a `ToolCallProgressEvent` arrives with `tool_input` containing complete arguments
- **THEN** the handler SHALL update `_ToolState.raw_input` with the `tool_input` value
- **AND** SHALL update `_ToolState.title` if applicable
- **AND** SHALL emit a `tool_call_update` notification with `status="in_progress"` and `raw_input` containing the complete arguments

#### Scenario: ToolCallProgressEvent with tool_input=None
- **WHEN** a `ToolCallProgressEvent` arrives with `tool_input=None`
- **THEN** the handler SHALL preserve existing `_ToolState.raw_input` unchanged
- **AND** SHALL emit a `tool_call_update` notification with `status="in_progress"` and existing `raw_input`

### Requirement: ACP tool call lifecycle emits up to three notifications

The ACP tool call lifecycle SHALL emit up to three `session/update` notifications per tool call: `pending` (ToolCallStart), `in_progress` (ToolCallProgress), and `completed` (ToolCallComplete). The `in_progress` notification SHALL be skipped when the tool call arguments are identical between `PartStartEvent` and `FunctionToolCallEvent` (dedup). All notifications for a single tool call SHALL share the same `toolCallId`.

#### Scenario: Streaming tool call with argument changes
- **WHEN** a tool call has streaming arguments that differ between `PartStartEvent` and `FunctionToolCallEvent`
- **THEN** the system SHALL emit up to three `session/update` notifications
- **AND** the first notification SHALL have `status="pending"` with empty `raw_input`
- **AND** the second notification SHALL have `status="in_progress"` with complete `raw_input`
- **AND** the third notification SHALL have `status="completed"` with `raw_input` preserved

#### Scenario: No-args tool call with identical raw_input
- **WHEN** a tool call has no arguments (`raw_input={}`) and `PartStartEvent` and `FunctionToolCallEvent` carry identical `raw_input`
- **THEN** the system SHALL emit exactly two `session/update` notifications
- **AND** the first notification SHALL have `status="pending"` with empty `raw_input`
- **AND** the second notification SHALL have `status="completed"` with `raw_input` preserved
- **AND** the `in_progress` notification SHALL be skipped (dedup)

### Requirement: Dead FunctionToolCallEvent handler removed

The ACPEventConverter SHALL NOT contain a `FunctionToolCallEvent` handler. The `FunctionToolCallEvent` is intercepted by EventMapper before reaching the converter, making any handler dead code.

#### Scenario: FunctionToolCallEvent never reaches ACPEventConverter
- **WHEN** a `FunctionToolCallEvent` is emitted during agent execution
- **THEN** EventMapper SHALL intercept it and emit `ToolCallStartEvent` or `ToolCallProgressEvent`
- **AND** ACPEventConverter SHALL NOT have a matching `case FunctionToolCallEvent` branch
