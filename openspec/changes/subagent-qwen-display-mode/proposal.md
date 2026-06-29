## Why

ACP clients (SEED, qwen-code SDK) display subagent "agent cards" by reading `_meta` fields (`parentToolCallId`, `subagentType`, `provenance`) from `session/update` notifications. AgentPool's current `zed` mode uses draft ACP PR #855 fields (`kind="subagent"`, `SubagentRunInfo`) which SEED doesn't support, causing agent cards to silently disappear. A hotfix reverted `kind` to `"other"`, but without the `_meta` fields, SEED cannot correlate child events to their parent tool call and cannot render subagent cards. This change adds a `"qwen"` display mode that stamps the correct `_meta` fields, matching qwen-code's proven approach.

## What Changes

- Add `"qwen"` to `subagent_display_mode` Literal type (alongside existing `"legacy"` and `"zed"`)
- Add `SubagentContext` dataclass to `event_converter.py` — carries `parent_tool_call_id` and `subagent_type` from `SpawnSessionStart` event to child converter
- Add `subagent_context: SubagentContext | None` field and `subagent_meta` property to `ACPEventConverter`
- Handler creates child converter directly in `_on_spawn_session_start` with `SubagentContext` extracted from the `SpawnSessionStart` event (Option E from RFC-0040)
- `_before_consumer_loop` skips converter creation if already exists (created by `_on_spawn_session_start`)
- `_handle_event` stamps `field_meta=converter.subagent_meta` on `SessionNotification` — one-line addition that covers ALL event types from child sessions
- Add `"qwen"` branch in converter's `SpawnSessionStart` handler — emits `ToolCallStart(kind="other")` without `SubagentRunInfo`

## Capabilities

### New Capabilities

- `subagent-qwen-display`: Display mode that stamps `_meta` fields (`parentToolCallId`, `subagentType`, `provenance`) on all subagent `session/update` notifications, enabling SEED and qwen-code SDK to render agent cards

### Modified Capabilities

- `session-aware-event-routing`: Add `"qwen"` as a third `subagent_display_mode` option; converter and handler handle the new mode alongside existing `"legacy"` and `"zed"`

## Impact

- **Files changed**: `src/agentpool_server/acp_server/event_converter.py`, `src/agentpool_server/acp_server/handler.py`
- **No framework changes**: `EventBus`, `EventEnvelope`, `RichAgentStreamEvent`, `ProtocolEventConsumerMixin` all unchanged
- **No mixin signature changes**: Other protocols (OpenCode, AG-UI, OpenAI API) unaffected
- **Backward compatible**: Existing `"legacy"` and `"zed"` modes unchanged; `"qwen"` is opt-in
- **RFC**: RFC-0040 documents the full options analysis and design rationale
