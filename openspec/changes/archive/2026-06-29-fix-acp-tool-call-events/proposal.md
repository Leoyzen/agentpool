## Why

ACP `session/update` notifications for tool calls are broken: a single tool invocation produces multiple `tool_call` events with different `toolCallId`s, `rawInput` contains partial JSON or `INVALID_JSON` keys, and the `in_progress` status transition never fires. This makes ACP clients (e.g., Zed) unable to render tool call progress correctly.

## What Changes

- **Fix PartDeltaEvent handler**: Stop calling `delta.as_part()` which generates random `tool_call_id`s on each streaming delta. Use `delta.tool_call_id` directly to look up existing state.
- **Fix EventMapper dedup logic**: When `FunctionToolCallEvent` arrives with a `tool_call_id` already seen, emit `ToolCallProgressEvent(in_progress)` with complete `tool_input` instead of silently returning `None`.
- **Fix ToolCallProgressEvent handler**: Extract `tool_input` and `tool_name` from the event (currently ignored), update state, and include `raw_input` in the emitted ACP notification.
- **Remove dead code**: Delete unreachable `FunctionToolCallEvent` handler in ACPEventConverter (EventMapper always intercepts before it reaches the converter).
- No config flag needed. No new dependencies.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `acp-server`: Tool call event lifecycle — PartDeltaEvent handler, ToolCallProgressEvent handler, 3-notification lifecycle, dead code removal
- `unified-event-routing`: EventMapper emits `ToolCallProgressEvent(in_progress)` when args differ instead of suppressing

## Impact

- **`src/agentpool_server/acp_server/event_converter.py`** — 3 handler changes (PartDeltaEvent, ToolCallProgressEvent, remove FunctionToolCallEvent handler)
- **`src/agentpool/orchestrator/event_mapper.py`** — Modify `_emit_tool_call_start()` to emit `ToolCallProgressEvent` on dedup
- **Side effects**: OpenCode server benefits (already extracts `tool_input`); AG-UI and OpenAI API servers unaffected (ignore `ToolCallProgressEvent` / don't use EventBus for main flow)
- **No breaking changes** — ACP notification shape unchanged, only the number and content of notifications is corrected
