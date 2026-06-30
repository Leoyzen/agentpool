## 1. Refactor: Side-effect-free collectors

- [x] 1.1 Add `_collect_request_updates(self, request: ModelRequest) -> list[SessionUpdate]` — extract conversion logic from `_replay_request`, return list instead of calling `send_update()`
- [x] 1.2 Add `_collect_response_updates(self, response: ModelResponse) -> list[SessionUpdate]` — extract from `_replay_response`, populate `_tool_call_inputs` cache during collection
- [x] 1.3 Keep `_replay_request`/`_replay_response` as thin wrappers calling collectors then `send_update()` per update (preserves existing behavior for non-batch callers)
- [x] 1.4 Verify existing tests in `tests/acp/test_notifications_replay.py` still pass unchanged

## 2. Batch delivery: send_batch_update

- [x] 2.1 Add `notification_batch_size: int = 20` and `notification_flush_interval: float = 0.0` to `ACPNotifications.__init__`
- [x] 2.2 Add `_batch_supported: bool = False` field, set via `set_batch_support(supported: bool)` method
- [x] 2.3 Implement `send_batch_update(self, updates: list[SessionUpdate]) -> None`:
  - If `_batch_supported`: call `self.client.ext_notification("_batch_session_updates", {"session_id": self.id, "updates": [u.model_dump(by_alias=True, exclude_none=True) for u in updates]})`
  - If not supported: loop `await self.send_update(u)` for each update (fallback)
- [x] 2.4 Add `notification_flush_interval` sleep between batches (only when `> 0`)

## 3. Rewrite replay() with batch collection

- [x] 3.1 Rewrite `replay()` to collect all updates via `_collect_request_updates`/`_collect_response_updates` into a single `list[SessionUpdate]`
- [x] 3.2 Loop over updates in chunks of `notification_batch_size`, call `await self.send_batch_update(batch)` per chunk
- [x] 3.3 Preserve error handling: wrap per-message collection in try/except, log failures, continue (same as current `replay()` behavior)
- [x] 3.4 Verify tool call ordering: `ToolCallStart` from `ToolCallPart` precedes `ToolCallProgress` from `ToolReturnPart` in the collected list

## 4. Client capability detection

- [x] 4.1 Add `_batch_session_updates` to client capabilities negotiation — define how clients advertise support (e.g., via `client_capabilities.field_meta` or a dedicated field in `InitializeRequest`)
- [x] 4.2 In `ACPSession.__post_init__` or `initialize` flow, detect batch support from client capabilities and call `notifications.set_batch_support(True/False)`
- [x] 4.3 Default to `False` (sequential fallback) when client doesn't advertise support

## 5. Tests

- [x] 5.1 Test: `replay()` with batch-capable client sends `_batch_session_updates` ext_notifications, not individual `session/update`
- [x] 5.2 Test: `replay()` with non-capable client falls back to sequential `session/update` (same count as before)
- [x] 5.3 Test: batch preserves ordering — `ToolCallStart` before `ToolCallProgress` within same batch
- [x] 5.4 Test: custom `notification_batch_size=5` produces correct chunk count
- [x] 5.5 Test: `_collect_request_updates` returns correct `SessionUpdate` list without calling any client method
- [x] 5.6 Test: empty messages list produces zero notifications
- [x] 5.7 Update existing `tests/acp/test_notifications_replay.py` to cover both batch and fallback paths
- [x] 5.8 Update `tests/servers/acp_server/test_acp_load.py` to verify batch delivery during `session/load`

## 6. Integration and benchmarks

- [x] 6.1 Run `uv run pytest tests/acp/test_notifications_replay.py tests/servers/acp_server/test_acp_load.py -v` — all pass
- [x] 6.2 Run `uv run ruff check src/acp/agent/notifications.py` — no lint errors
- [x] 6.3 Run `uv run --no-group docs mypy src/acp/agent/notifications.py` — no type errors
- [x] 6.4 Write a benchmark script: time `replay()` with 100 messages, compare batch vs sequential (target: 80%+ reduction)
