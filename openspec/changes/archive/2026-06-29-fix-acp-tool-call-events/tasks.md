## 1. EventMapper: Emit in_progress when args change

- [x] 1.1 Add `ToolCallProgressEvent` import to `src/agentpool/orchestrator/event_mapper.py`
- [x] 1.2 Modify `_emit_tool_call_start()` return type to `ToolCallStartEvent | ToolCallProgressEvent | None`
- [x] 1.3 When `tool_call_id` already in `_pending_tool_calls`: compute new `raw_input`. If differs, return `ToolCallProgressEvent(in_progress, tool_input, tool_name)`. If identical, return `None`.
- [x] 1.4 Unit test: emits `ToolCallProgressEvent` with `tool_input` when args differ
- [x] 1.5 Unit test: returns `None` when args identical (dedup works)

## 2. ACPEventConverter: Fix PartDeltaEvent handler

- [x] 2.1 Remove `delta.as_part()` call in `src/agentpool_server/acp_server/event_converter.py` (lines 442-497)
- [x] 2.2 Replace handler: look up existing state by `delta.tool_call_id`, yield nothing if not found
- [x] 2.3 Unit test: `PartDeltaEvent` with `tool_call_id=None` yields no notifications
- [x] 2.4 Unit test: `PartDeltaEvent` with known `tool_call_id` doesn't create new state or emit `ToolCallStart`

## 3. ACPEventConverter: Fix ToolCallProgressEvent handler

- [x] 3.1 Add `tool_input` and `tool_name` to match pattern (event_converter.py:581-589)
- [x] 3.2 When `tool_input` is not `None`: update `state.raw_input` and `state.title`
- [x] 3.3 When `tool_name` is not `None` and state has `"unknown"`: update `state.tool_name`
- [x] 3.4 Add `raw_input=state.raw_input` to emitted `ToolCallProgress` (line 642)
- [x] 3.5 Unit test: `ToolCallProgressEvent` with `tool_input` updates state and emits `raw_input`
- [x] 3.6 Unit test: `ToolCallProgressEvent` with `tool_input=None` preserves existing state

## 4. ACPEventConverter: Remove dead FunctionToolCallEvent handler

- [x] 4.1 Delete `case FunctionToolCallEvent(part=part):` handler (lines 500-523)
- [x] 4.2 Remove `FunctionToolCallEvent` from imports if no longer used
- [x] 4.3 Verify no tests depend on removed handler

## 5. Integration tests

- [x] 5.1 End-to-end: `PartStartEvent` → `PartDeltaEvent` ×N → `FunctionToolCallEvent` → `FunctionToolResultEvent` produces up to 3 ACP notifications with same `tool_call_id`
- [x] 5.2 Verify `raw_input` empty in `pending`, complete in `in_progress`
- [x] 5.3 Tool call with no args produces exactly 2 notifications (pending + completed, no in_progress) — dedup when identical `raw_input`
- [x] 5.4 Run existing ACP tests: `uv run pytest tests/servers/acp_server/ -v`

## 6. Cross-protocol regression tests

- [x] 6.1 Run OpenCode server tests: `uv run pytest tests/servers/opencode_server/ -v` (OpenCode extracts `tool_input` from `ToolCallProgressEvent` — should benefit)
- [x] 6.2 Run AG-UI server tests: `uv run pytest tests/servers/test_agui_server.py tests/server/agui/ -v` (AG-UI ignores `ToolCallProgressEvent` — no impact expected)
- [x] 6.3 Run OpenAI API server tests: `uv run pytest tests/servers/test_openai_api_server.py -v` (doesn't use EventBus for main flow — no impact expected)

## 7. Code quality

- [x] 7.1 `uv run ruff check src/` — no new lint errors
- [x] 7.2 `uv run ruff format --check src/` — formatting clean
- [x] 7.3 `uv run mypy src/` — no new type errors
- [x] 7.4 `uv run pytest -m unit` — all unit tests pass
