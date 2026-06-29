# Spec: session-aware-event-routing (modified)

## Delta: Add qwen display mode

### Modified Requirements

#### subagent_display_mode

The `subagent_display_mode` field SHALL accept three values: `"legacy"`, `"zed"`, and `"qwen"`.

The `"qwen"` mode SHALL:
- Emit `ToolCallStart(kind="other")` for `SpawnSessionStart` events (same `kind` as `"other"` tools)
- NOT emit `SubagentRunInfo` on `ToolCallStart`
- NOT emit `field_meta` with `subagent_session_info` on `ToolCallStart` (the `_meta` goes on the notification, not the update)

The handler SHALL stamp `_meta` fields (`parentToolCallId`, `subagentType`, `provenance`) on `SessionNotification.field_meta` for all events from child sessions when the child converter has a `subagent_context`.

#### Unchanged Requirements

- `"legacy"` mode: inline text, no `ToolCallStart` — unchanged
- `"zed"` mode: `ToolCallStart(kind="other")` (hotfixed from `"subagent"`), `SubagentRunInfo` populated — unchanged from current hotfix state
- `build_subagent_completed()` method — unchanged
- `_parent_of` dict and completion notification — unchanged
- Recursive cancellation — unchanged
