# Spec: subagent-qwen-display

## Requirements

### 1. qwen display mode

The ACP event converter SHALL support a `"qwen"` value for `subagent_display_mode` (alongside existing `"legacy"` and `"zed"`).

When `subagent_display_mode == "qwen"`:
- `SpawnSessionStart` events SHALL produce `ToolCallStart` with `kind="other"` (not `"subagent"`)
- `ToolCallStart` SHALL NOT include `SubagentRunInfo`
- `ToolCallStart` SHALL NOT include `field_meta` with subagent-specific fields

### 2. SubagentContext

The converter SHALL accept an optional `SubagentContext` containing:
- `parent_tool_call_id: str` — the tool call ID that spawned this subagent
- `subagent_type: str` — the agent name (from `SpawnSessionStart.source_name`)

When `subagent_context is None` (root session), the converter SHALL NOT stamp any subagent `_meta`.

### 3. subagent_meta property

The converter SHALL expose a `subagent_meta` property that returns:
- `None` when `subagent_context is None`
- `{"parentToolCallId": ..., "subagentType": ..., "provenance": "subagent"}` when `subagent_context` is set

### 4. _meta stamping on notifications

The handler SHALL stamp `field_meta=converter.subagent_meta` on every `SessionNotification` constructed in `_handle_event`.

This SHALL apply to ALL event types from the child session — `AgentMessageChunk`, `AgentThoughtChunk`, `ToolCallStart`, `ToolCallProgress`, `AgentPlanUpdate`, etc.

### 5. Handler creates child converter

When `_on_spawn_session_start` receives a `SpawnSessionStart` event, the handler SHALL:
- Extract `tool_call_id` and `source_name` from the event
- Create an `ACPEventConverter` with `subagent_context=SubagentContext(...)`
- Store it in `self._converters[child_session_id]` BEFORE calling `start_event_consumer`

### 6. _before_consumer_loop early return

`_before_consumer_loop` SHALL check if a converter already exists for the session. If it does (created by `_on_spawn_session_start`), it SHALL return early without creating a new one.

### 7. Nested subagents

Each nesting level SHALL get its own converter with its own `SubagentContext`. The `parentToolCallId` SHALL point to the immediate parent's tool call, not the root.

### 8. Backward compatibility

- `"legacy"` mode behavior SHALL remain unchanged
- `"zed"` mode behavior SHALL remain unchanged (except the hotfix reverting `kind` to `"other"` stays)
- Root session converters SHALL have `subagent_context=None` and `subagent_meta=None`
