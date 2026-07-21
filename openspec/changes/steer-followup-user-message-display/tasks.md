## 1. Core Event Type

- [ ] 1.1 Add `UserMessageInsertedEvent` frozen dataclass to `src/agentpool/agents/events/events.py` with fields: `session_id` (str), `message_id` (str), `content` (`str | list[Any]` — supports multi-modal prompts), `delivery` (Literal["initial", "steer", "followup"]), `source` (Literal["protocol", "background_task", "internal"]), `timestamp` (float, default_factory=time.time)
- [ ] 1.2 Add `UserMessageInsertedEvent` to the `RichAgentStreamEvent` PEP 695 `type` statement
- [ ] 1.3 Export `UserMessageInsertedEvent` from `src/agentpool/agents/events/__init__.py`
- [ ] 1.4 Write unit tests for `UserMessageInsertedEvent` construction and field defaults, including multi-modal `content` as `list[Any]` (`tests/agents/events/test_user_message_inserted_event.py`)

## 2. SessionController Publication

- [ ] 2.1 Add `message_id` parameter to `SessionController._route_message()` — optional `str | None`, defaults to `None`; when `None`, generated as `str(uuid.uuid4())`. Also add `delivery` parameter (optional, inferred from session state and priority if `None`).
- [ ] 2.2 Publish `UserMessageInsertedEvent` from `SessionController._route_message()` before routing action, if EventBus is available — determine `delivery` from session state and priority (`"initial"` for idle, `"steer"` for asap, `"followup"` for when_idle); set `source="protocol"`
- [ ] 2.3 Modify `SessionController.steer_from_background_task()` (SYNC method — do NOT make it `async def`) to publish `UserMessageInsertedEvent` with `delivery="steer"`, `source="background_task"`, if EventBus is available. Use `asyncio.create_task()` with `try/except RuntimeError` for no-running-loop scenarios.
- [ ] 2.4 Publish `UserMessageInsertedEvent` from `SessionController._consume_run()` for followup-from-queue messages (picked up from `prompt_queue` by `_create_per_prompt_handle()` without calling `_route_message()`), if EventBus is available — set `delivery="followup"`, `source="internal"`
- [ ] 2.5 Pass `message_id` through from `send_message()` to `_route_message()` for dedup with protocol handlers
- [ ] 2.6 Write unit tests for `_route_message()` event publication (`tests/orchestrator/test_route_message_event.py`) — test all three delivery paths (initial, steer, followup) and EventBus=None guard
- [ ] 2.7 Write unit tests for `steer_from_background_task()` event publication (`tests/orchestrator/test_steer_background_event.py`) — verify SYNC method, `asyncio.create_task()` usage, `RuntimeError` handling
- [ ] 2.8 Write unit tests for `_consume_run()` followup-from-queue event publication (`tests/orchestrator/test_consume_run_event.py`)

## 3. RunHandle steer()/followup() Emission

- [ ] 3.1 Add `emit_user_message: bool = True` parameter to `RunHandle.steer()` in `src/agentpool/orchestrator/run.py`. Signature: `def steer(self, content: str | list[Any], emit_user_message: bool = True) -> None:`
- [ ] 3.2 Add `emit_user_message: bool = False` parameter to `RunHandle.followup()` in `src/agentpool/orchestrator/run.py`. Signature: `def followup(self, content: str | list[Any], emit_user_message: bool = False) -> None:`
- [ ] 3.3 Implement emission helper `_emit_user_message_inserted()` on BOTH `RunHandle` (for `steer()`/`followup()` direct calls) and `SessionController` (for `steer_from_background_task()` and `_consume_run()`) — wraps emission in `logfire.span("event.user_message_inserted.emit")` to prevent orphan traces; catches all exceptions with `try/except Exception` and logs warning; checks `if self._event_bus` before publishing. Both classes have `self._event_bus` access. Implementation is identical on both (not shared via mixin).
- [ ] 3.4 Use `asyncio.get_running_loop().create_task()` fire-and-forget pattern at all `steer()`/`followup()`/`steer_from_background_task()` call sites with `try/except RuntimeError` for no-running-loop scenarios
- [ ] 3.5 Write unit tests for `steer(emit_user_message=True/False)` and `followup(emit_user_message=True/False)` (`tests/orchestrator/test_steer_user_message.py`) — verify default values, emission suppression, no-running-loop handling

## 4. ACP Schema: UserMessage Model

- [ ] 4.1 Add `UserMessage` Pydantic model to `src/acp/schema/session_updates.py` — fields: `message_id: str`, `content: list[ContentBlock] | None = None`, `meta: dict[str, Any] | None = None`, discriminator `session_update: Literal["user_message"] = "user_message"`
- [ ] 4.2 Add `UserMessage` to the `SessionUpdate` union in `session_updates.py`
- [ ] 4.3 Update `send_user_message()` in `src/acp/agent/notifications.py` to optionally emit `UserMessage` (v2) instead of `UserMessageChunk` (v1) based on protocol version
- [ ] 4.4 Write unit tests for `UserMessage` serialization and union dispatch (`tests/acp/schema/test_session_updates_user_message.py`)

## 5. ACP Server: _meta.delivery Extraction at acp_agent.py:prompt()

- [ ] 5.1 In `src/agentpool_server/acp_server/acp_agent.py:prompt()` (line ~698), extract `delivery` from `_meta` — `_meta` is already extracted for trace context at this point but NOT forwarded to `handle_prompt()`. Map `"steer"` → priority `"asap"`, `"followup"` → priority `"when_idle"`, absent → `"when_idle"` (default).
- [ ] 5.2 Pass `delivery` as a parameter through `handle_prompt()` → `send_message()` → `_route_message()` call chain
- [ ] 5.3 Generate `message_id` in `handle_prompt()` (or `_emit_user_message_chunks()`) and pass it to `send_message(message_id=mid)` for dedup
- [ ] 5.4 Write unit tests for `_meta.delivery` extraction at `acp_agent.py:prompt()` (`tests/servers/acp_server/test_handle_prompt_delivery.py`)

## 6. ACP Server: ACPEventConverter

- [ ] 6.1 Modify `ACPEventConverter.__init__()` in `src/agentpool_server/acp_server/event_converter.py` to accept `protocol_version: int = 1` parameter, passed from the ACP agent (which stores it at `acp_agent.py:380`)
- [ ] 6.2 Add `case UserMessageInsertedEvent` to `ACPEventConverter.convert()` — for v1 (`protocol_version < 2`): emit `UserMessageChunk` with `TextContentBlock`; for v2 (`protocol_version >= 2`): emit `UserMessage` with `content=[TextContentBlock(...)]`
- [ ] 6.3 Convert `content` (`str | list[Any]`) to appropriate `ContentBlock` instances — `str` → `TextContentBlock`, dict with image → `ImageContentBlock`, etc.
- [ ] 6.4 Add shared dedup `set[str]` per session — lives as `dict[str, set[str]]` on `SessionController` (keyed by `session_id`), passed to `ACPEventConverter` and `EventProcessor` constructors as `displayed_message_ids: set[str]` — skip if `message_id` already in set; add after emission
- [ ] 6.5 Add dedup set cleanup on session close
- [ ] 6.6 Write unit tests for `ACPEventConverter` handling `UserMessageInsertedEvent` — v1 path, v2 path, dedup skip, multi-modal content (`tests/servers/acp_server/test_event_converter_user_message.py`)

## 7. ACP Server: _emit_user_message_chunks() Dedup Wiring

- [ ] 7.1 Modify `_emit_user_message_chunks()` in `src/agentpool_server/acp_server/handler.py` to generate `message_id` FIRST (before emitting chunks)
- [ ] 7.2 Register `message_id` in the shared dedup set before emitting to client
- [ ] 7.3 Pass `message_id` through `send_message(message_id=mid)` → `_route_message(message_id=mid)` so the EventBus event carries the same ID
- [ ] 7.4 Write unit test verifying no double display when both `_emit_user_message_chunks()` and EventBus event fire (`tests/servers/acp_server/test_emit_chunks_dedup.py`)

## 8. OpenCode Server: EventProcessor

- [ ] 8.1 Add `case UserMessageInsertedEvent` to `EventProcessor.process()` in `src/agentpool_server/opencode_server/event_processor.py`
- [ ] 8.2 Create `UserMessage` object with `role="user"`, `id=event.message_id`, `content` converted from `str | list[Any]` to Parts, `created_at=event.timestamp`
- [ ] 8.3 Broadcast `MessageUpdatedEvent` and `PartUpdatedEvent` via SSE (matching `message_routes.py` pattern)
- [ ] 8.4 Add shared dedup `set[str]` per session to `EventProcessor` — skip if `message_id` already in set
- [ ] 8.5 Add dedup set cleanup on session close
- [ ] 8.6 Write unit tests for `EventProcessor` handling `UserMessageInsertedEvent` — normal path, dedup skip, multi-modal content (`tests/servers/opencode_server/test_event_processor_user_message.py`)

## 9. OpenCode Server: UserMessage Creation Site Dedup Wiring

- [ ] 9.1 In `src/agentpool_server/opencode_server/routes/message_routes.py`, add generated `message_id` to the shared dedup set after creating `UserMessage` — covers `message_routes.py:311` and `message_routes.py:884`
- [ ] 9.2 Pass `message_id` to `route_message()` / `send_message()` for EventBus event correlation
- [ ] 9.3 Audit and wire dedup at ALL 6+ `UserMessage` creation sites in OpenCode:
  - `message_routes.py:311,884`
  - `session_routes.py:414,638`
  - `opencode_event_bridge.py:368,638`
- [ ] 9.4 Write unit test verifying no double display when both REST handler and EventBus event fire (`tests/servers/opencode_server/test_message_routes_dedup.py`)

## 10. Exhaustive Match Audit

- [ ] 10.1 Grep all `match event:` sites in `src/` — enumerate ALL 27 match sites across 20 files
- [ ] 10.2 For each site without `case _:` catch-all, add `case UserMessageInsertedEvent: pass` (or appropriate handling)
- [ ] 10.3 Verify `builtin_handlers.py`, `tts_handlers.py`, `openai_api_server/completions/helpers.py` handle the new type
- [ ] 10.4 Run `python tests/check_markers.py` to verify all new test files have layer markers

## 11. Integration Tests

- [ ] 11.1 Write integration test: ACP v1 client receives `UserMessageChunk` for steer message (`tests/servers/acp_server/test_steer_user_message_integration.py`)
- [ ] 11.2 Write integration test: ACP v2 client receives `UserMessage` for steer message
- [ ] 11.3 Write integration test: OpenCode client receives `UserMessage` SSE event for steer message
- [ ] 11.4 Write integration test: Internal `steer_from_background_task()` triggers user message display in both protocols
- [ ] 11.5 Write integration test: Followup from `prompt_queue` (via `_consume_run()`) triggers user message display
- [ ] 11.6 Write integration test: Dedup prevents double display when protocol handler and EventBus both fire
- [ ] 11.7 Write integration test: `EventBus=None` (standalone execution) — no events published, no crash
- [ ] 11.8 Write integration test: `steer(emit_user_message=False)` suppresses event from `steer()` (event may still come from `_route_message()`)

## 12. Documentation

- [ ] 12.1 Update `src/agentpool/AGENTS.md` — add `UserMessageInsertedEvent` to Event Types taxonomy table
- [ ] 12.2 Update `src/agentpool/AGENTS.md` — document `steer(emit_user_message=)` (default `True`) and `followup(emit_user_message=)` (default `False`) parameters
- [ ] 12.3 Update `src/agentpool_server/AGENTS.md` — document ACP `_meta.delivery` extraction at `acp_agent.py:prompt()` and `UserMessage` schema addition
- [ ] 12.4 Update `src/agentpool_server/AGENTS.md` — document `ACPEventConverter(protocol_version=)` constructor parameter
- [ ] 12.5 Document the relationship between `UserMessageInsertedEvent` (this change) and `SystemNotificationEvent` (RFC-0056 / PR #219) — complementary, not replacing. RFC-0056 is not a dependency.
- [ ] 12.6 Document the `source` field mapping table (protocol, background_task, internal)

## 13. Quality Gates

- [ ] 13.1 Run `uv run ruff check src/` — all modified files pass
- [ ] 13.2 Run `uv run --no-group docs mypy src/` — no new type errors on modified files
- [ ] 13.3 Run `uv run pytest -m unit` — all unit tests pass
- [ ] 13.4 Run `uv run pytest -m integration` — all integration tests pass
- [ ] 13.5 Run `python tests/check_markers.py` — all new test files have layer markers
