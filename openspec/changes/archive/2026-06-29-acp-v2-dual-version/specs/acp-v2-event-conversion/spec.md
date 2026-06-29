## ADDED Requirements

### Requirement: v2 event converter SHALL convert RichAgentStreamEvent to v2 SessionUpdate

`ACPEventConverterV2` SHALL consume `RichAgentStreamEvent` objects and yield v2 `SessionUpdate` objects. The converter SHALL be stateful (tracking message IDs, tool call IDs, plan IDs) but perform no I/O.

#### Scenario: Text streaming produces agent_message_chunk

- **WHEN** converter receives `PartDeltaEvent` with `TextPartDelta(delta="Hello")`
- **THEN** converter SHALL yield `agent_message_chunk` with `messageId` and `content={type:"text", text:"Hello"}`

#### Scenario: Turn start produces state_update running

- **WHEN** converter receives `RunStartedEvent` or first `PartStartEvent`
- **THEN** converter SHALL yield `state_update` with `state="running"`

#### Scenario: Turn end produces state_update idle

- **WHEN** converter receives `StreamCompleteEvent`
- **THEN** converter SHALL yield `state_update` with `state="idle"` and `stopReason` from the event

### Requirement: v2 event converter SHALL emit user_message on prompt acceptance

When the v2 handler accepts a `session/prompt`, the event converter (or handler) SHALL emit a `user_message` session update with the prompt content and an agent-assigned `messageId`.

#### Scenario: Prompt content echoed as user_message

- **WHEN** v2 handler receives `session/prompt` with `prompt=[{type:"text", text:"Analyze code"}]`
- **THEN** a `user_message` session update SHALL be emitted with `messageId` (agent-assigned) and `content=[{type:"text", text:"Analyze code"}]`

### Requirement: v2 event converter SHALL use unified tool_call_update

`ACPEventConverterV2` SHALL emit only `tool_call_update` for both creating and updating tool calls (no `tool_call`). First sighting of a `toolCallId` creates the tool call; subsequent updates patch it.

#### Scenario: Tool call start emits tool_call_update with full fields

- **WHEN** converter receives `ToolCallStartEvent` with `tool_call_id=tc1`, `title="Reading file"`, `kind="read"`
- **THEN** converter SHALL yield `tool_call_update` with `toolCallId=tc1`, `title="Reading file"`, `kind="read"`, `status="pending"`

#### Scenario: Tool call progress emits tool_call_update with patch fields

- **WHEN** converter receives `ToolCallProgressEvent` with `tool_call_id=tc1`, `status="in_progress"`
- **THEN** converter SHALL yield `tool_call_update` with `toolCallId=tc1` and `status="in_progress"` only (other fields omitted = unchanged)

#### Scenario: Tool call completion emits tool_call_update with results

- **WHEN** converter receives `ToolCallCompleteEvent` with `tool_call_id=tc1`, `status="completed"`, `content=[{type:"content", content:{type:"text", text:"result"}}]`
- **THEN** converter SHALL yield `tool_call_update` with `toolCallId=tc1`, `status="completed"`, and `content` replacing previous content

### Requirement: v2 event converter SHALL support tool_call_content_chunk for streaming

`ACPEventConverterV2` SHALL emit `tool_call_content_chunk` when streaming individual content items to a tool call, appending to existing content.

#### Scenario: Streaming tool output as content chunks

- **WHEN** agent produces incremental output for tool call `tc1` (e.g., terminal output lines)
- **THEN** converter SHALL yield `tool_call_content_chunk` with `toolCallId=tc1` and single `content` item per chunk

### Requirement: v2 event converter SHALL emit plan_update with tagged content

`ACPEventConverterV2` SHALL emit `plan_update` (not v1 `plan`) with `plan={type:"items", id:"main", entries=[...]}`. The plan ID `"main"` SHALL be used as the synthetic ID for v1-compatible single-plan scenarios.

#### Scenario: Plan update emitted with item-based content

- **WHEN** converter receives `PlanUpdateEvent` with entries `[{content:"Step 1", priority:"high", status:"pending"}]`
- **THEN** converter SHALL yield `plan_update` with `plan={type:"items", id:"main", entries=[...]}`

### Requirement: v2 event converter SHALL NOT inherit from v1 converter

`ACPEventConverterV2` SHALL be an independent class, NOT a subclass of `ACPEventConverter`. Both classes share the same input type (`RichAgentStreamEvent`) but produce different output types (v1 vs v2 `SessionUpdate`).

#### Scenario: Independent class hierarchy

- **WHEN** inspecting `ACPEventConverterV2` class definition
- **THEN** it SHALL NOT extend `ACPEventConverter` (no inheritance relationship)

### Requirement: v2 event converter SHALL emit state_update on subagent lifecycle

`ACPEventConverterV2` SHALL handle `SpawnSessionStart` events and emit appropriate `tool_call_update` or `state_update` notifications for subagent lifecycle, supporting out-of-turn updates when subagents complete after the main turn.

#### Scenario: Subagent completion after main turn

- **WHEN** a `SpawnSessionStart` child session completes after the main turn's `state_update: idle`
- **THEN** converter SHALL emit `tool_call_update` with the subagent's results as an out-of-turn update
