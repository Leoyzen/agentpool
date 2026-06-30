## 1. SubagentContext + converter changes (event_converter.py)

- [x] 1.1 Add `SubagentContext` dataclass with `parent_tool_call_id: str` and `subagent_type: str`
- [x] 1.2 Add `subagent_context: SubagentContext | None = None` field to `ACPEventConverter`
- [x] 1.3 Add `subagent_meta` property to `ACPEventConverter` — returns `None` or `{"parentToolCallId": ..., "subagentType": ..., "provenance": "subagent"}`
- [x] 1.4 Add `"qwen"` to `subagent_display_mode` Literal type: `Literal["legacy", "zed", "qwen"]`
- [x] 1.5 Add `"qwen"` branch in `SpawnSessionStart` handler — emit `ToolCallStart(kind="other")` without `SubagentRunInfo` or `field_meta`

## 2. Handler changes (handler.py)

- [x] 2.1 In `_on_spawn_session_start`: extract `SubagentContext` from `SpawnSessionStart` event (`tool_call_id`, `source_name`), create `ACPEventConverter` with `subagent_context`, store in `self._converters[child_sid]` BEFORE calling `start_event_consumer`
- [x] 2.2 In `_before_consumer_loop`: add early return if `session_id in self._converters` (converter already created by `_on_spawn_session_start`)
- [x] 2.3 In `_handle_event`: add `field_meta=converter.subagent_meta` to `SessionNotification` constructor

## 3. Tests

- [x] 3.1 Test `subagent_meta` property returns `None` when `subagent_context is None`
- [x] 3.2 Test `subagent_meta` returns correct dict when `subagent_context` is set
- [x] 3.3 Test `"qwen"` mode `SpawnSessionStart` emits `ToolCallStart(kind="other")` without `SubagentRunInfo`
- [x] 3.4 Test `_on_spawn_session_start` creates child converter with `SubagentContext`
- [x] 3.5 Test `_before_consumer_loop` skips creation when converter exists
- [x] 3.6 Test `_handle_event` stamps `field_meta` on `SessionNotification` for child sessions
- [x] 3.7 Test root session notifications have `field_meta=None`
- [x] 3.8 Test nested subagents — each level has its own `subagent_context`
- [x] 3.9 Test legacy and zed modes are unaffected

## 4. Verification

- [x] 4.1 Run `uv run pytest tests/acp/ -x --no-cov` — all ACP tests pass
- [x] 4.2 Run `uv run pytest tests/servers/acp_server/test_subagent_events.py -x --no-cov` — subagent event tests pass
- [x] 4.3 Run `uv run ruff check src/agentpool_server/acp_server/event_converter.py src/agentpool_server/acp_server/handler.py` — no new violations
- [x] 4.4 Manual QA: Start ACP server with `subagent_display_mode: qwen`, spawn a subagent, verify SEED displays agent card
