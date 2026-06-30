## Context

ACP tool call notifications are broken due to a three-layer bug involving `pydantic_ai`'s `ToolCallPartDelta.as_part()` generating random IDs, EventMapper's dedup logic suppressing complete args, and ACPEventConverter's ToolCallProgressEvent handler ignoring `tool_input`.

**Current flow (broken)**:
1. Model streams tool call deltas → `PartDeltaEvent(ToolCallPartDelta)` fires
2. ACPEventConverter calls `delta.as_part()` → pydantic-ai generates random `tool_call_id` (since `delta.tool_call_id` is `None`)
3. Each delta creates a new `_ToolState` → multiple `ToolCallStart` notifications with different IDs
4. `FunctionToolCallEvent` arrives with complete args → EventMapper sees `tool_call_id` already in `_pending_tool_calls` → returns `None`
5. `in_progress` status never fires; ACP client sees partial JSON with `INVALID_JSON` keys

**Key files**:
- `src/agentpool_server/acp_server/event_converter.py` — ACPEventConverter (927 lines)
- `src/agentpool/orchestrator/event_mapper.py` — EventMapper (163 lines)
- `src/agentpool/agents/events/events.py` — `ToolCallProgressEvent` class (line 266)

## Goals / Non-Goals

**Goals:**
- ACP tool call lifecycle produces up to 3 notifications: `pending` → `in_progress` → `completed`
- All notifications for a single tool call share the same `toolCallId`
- `raw_input` is empty in `pending`, complete in `in_progress`, preserved in `completed`
- No config flag — fix is always on

**Non-Goals:**
- Streaming partial tool call arguments to ACP client (deltas are not forwarded)
- Changing pydantic-ai's `as_part()` or `_parts_manager` behavior
- Adding configuration flags for backwards compatibility

## Decisions

### Decision 1: Remove `as_part()` call in PartDeltaEvent handler

**Rationale**: `delta.as_part()` generates a random `tool_call_id` when `delta.tool_call_id` is `None` (pydantic_ai/messages.py:2577). This is the root cause of duplicate IDs. The handler should use `delta.tool_call_id` directly to look up existing `_ToolState` and yield nothing if not found.

**Alternative considered (rejected)**: Add a `stream_tool_call_deltas` config flag to gate PartDeltaEvent streaming. This would require plumbing through 8 files (AgentPool → SessionPool → SessionController → NativeTurn → EventMapper → ACPEventConverter → config models → YAML). Overkill for a bug fix — the flag would always be `false` in practice since no one wants broken notifications.

### Decision 2: Emit `ToolCallProgressEvent(in_progress)` from EventMapper

**Rationale**: EventMapper's `_emit_tool_call_start()` (event_mapper.py:104-105) returns `None` when `tool_call_id` is already in `_pending_tool_calls`. This blocks `FunctionToolCallEvent` (with complete args) from reaching ACPEventConverter. Instead, when `tool_call_id` exists but `raw_input` differs, emit `ToolCallProgressEvent(in_progress, tool_input, tool_name)`.

### Decision 3: Fix ToolCallProgressEvent handler to extract `tool_input`

**Rationale**: The handler (event_converter.py:581-648) matches `ToolCallProgressEvent(part=part)` but doesn't extract `tool_input` or `tool_name` from the event. It creates state with `"unknown"` tool name and empty `raw_input`. Fix: add `tool_input` and `tool_name` to the match pattern, update state, and include `raw_input` in the emitted ACP notification.

### Decision 4: Remove dead `FunctionToolCallEvent` handler

**Rationale**: The `FunctionToolCallEvent` handler (event_converter.py:500-523) is unreachable because EventMapper always intercepts `FunctionToolCallEvent` before it reaches ACPEventConverter. After Decision 2, EventMapper emits `ToolCallProgressEvent` instead, so the handler remains dead. Remove it to avoid confusion.

## Risks / Trade-offs

- **[OpenCode server]** OpenCode already extracts `tool_input` from `ToolCallProgressEvent` — benefits from the fix, no breakage expected. → Verified by running OpenCode server tests.
- **[AG-UI server]** AG-UI ignores `ToolCallProgressEvent` — no impact expected. → Verified by running AG-UI server tests.
- **[OpenAI API server]** Doesn't use EventBus for main flow — no impact expected. → Verified by running OpenAI API server tests.
- **[Dedup edge case]** When `raw_input` is identical between `PartStartEvent` and `FunctionToolCallEvent` (e.g., no-args tool call), `in_progress` is skipped — only 2 notifications. This is correct behavior, not a bug.
