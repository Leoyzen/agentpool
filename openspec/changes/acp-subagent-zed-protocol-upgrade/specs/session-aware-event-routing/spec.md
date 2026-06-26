## MODIFIED Requirements

### Requirement: Protocol layers route events by session_id
Protocol layer event consumers SHALL use the `session_id` field on each event to determine which session context should process the event. Events with a `session_id` different from the consumer's primary session SHALL be routed to the corresponding child session context.

When `subagent_display_mode="zed"`, subagent `ToolCallStart` events SHALL use `kind="subagent"` (not `"other"`). All `ToolCallProgress` events for subagent tool calls SHALL carry `field_meta` containing `subagent_session_info` and `tool_name`.

#### Scenario: ACP server receives child session event
- **WHEN** the ACP event converter receives a ToolCallStartEvent with session_id="child-456"
- **THEN** it routes the event to the converter state for session "child-456"

#### Scenario: Subagent ToolCallStart uses kind="subagent"
- **WHEN** `subagent_display_mode="zed"` and a `SpawnSessionStart` event is converted to `ToolCallStart`
- **THEN** the `ToolCallStart.kind` SHALL be `"subagent"` (not `"other"`)

#### Scenario: ToolCallProgress carries _meta
- **WHEN** a `ToolCallProgress` is emitted for a subagent tool call in zed mode
- **THEN** the `field_meta` SHALL contain `subagent_session_info` with `session_id` matching the child session
- **AND** the `field_meta` SHALL contain `tool_name="task"`

#### Scenario: tool_call_id consistency
- **WHEN** `SpawnSessionStart` carries `tool_call_id="tc-123"`
- **THEN** the `ToolCallStart` emitted by the converter SHALL use `tool_call_id="tc-123"` (not a new `uuid4()`)
