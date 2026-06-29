## ADDED Requirements

### Requirement: EventMapper emits ToolCallProgressEvent when tool call args differ

The EventMapper `_emit_tool_call_start()` SHALL return `ToolCallStartEvent` when a `tool_call_id` is not in `_pending_tool_calls`. When the `tool_call_id` is already present and the new `raw_input` differs from the stored value, the method SHALL return `ToolCallProgressEvent(in_progress, tool_input, tool_name)` instead of `None`. When the `tool_call_id` is already present and `raw_input` is identical, the method SHALL return `None` (dedup).

#### Scenario: New tool call emits ToolCallStartEvent
- **WHEN** a `FunctionToolCallEvent` arrives with a `tool_call_id` not in `_pending_tool_calls`
- **THEN** `_emit_tool_call_start()` SHALL return a `ToolCallStartEvent` with the tool name and empty `raw_input`
- **AND** SHALL add the `tool_call_id` to `_pending_tool_calls`

#### Scenario: Changed args emit ToolCallProgressEvent
- **WHEN** a `FunctionToolCallEvent` arrives with a `tool_call_id` already in `_pending_tool_calls`
- **AND** the new `raw_input` differs from the stored value
- **THEN** `_emit_tool_call_start()` SHALL return a `ToolCallProgressEvent` with `status="in_progress"`, `tool_input`, and `tool_name`
- **AND** SHALL NOT return `None`

#### Scenario: Identical args return None (dedup)
- **WHEN** a `FunctionToolCallEvent` arrives with a `tool_call_id` already in `_pending_tool_calls`
- **AND** the new `raw_input` is identical to the stored value
- **THEN** `_emit_tool_call_start()` SHALL return `None`
- **AND** SHALL NOT emit any event
