## 1. Feedback Type Extension

- [x] 1.1 Add `message_id`, `content_blocks`, and `mode` fields to `Feedback` dataclass in `lifecycle/types.py` with `__post_init__` for `mode` auto-derivation from `is_steer`
- [x] 1.2 Add `uuid` import to `lifecycle/types.py` for `message_id` default factory
- [x] 1.3 Verify existing `Feedback` construction sites (`run.py:925`, `run.py:964`) still work without changes (new fields have defaults)
- [x] 1.4 Add unit tests for `Feedback` auto-generated `message_id`, explicit `message_id` override, `mode` derivation, and `content_blocks` passthrough

## 2. Event Message ID Propagation

- [x] 2.1 Add `message_id: str = ""` field to `PartStartEvent` in `agents/events/events.py`
- [x] 2.2 Add `message_id: str = ""` field to `PartDeltaEvent` in `agents/events/events.py`
- [x] 2.3 Update `NativeTurn` (`agents/native_agent/turn.py`) to set `message_id=self._message_id` on `PartStartEvent` and propagate to `PartDeltaEvent`
- [x] 2.4 Update `ACPTurn` (`agents/acp_agent/turn.py`) to set `message_id` from incoming ACP session update's `message_id` field, or generate UUID if absent
- [x] 2.5 Add unit tests verifying `PartStartEvent` and `PartDeltaEvent` carry the same `message_id` for a single message

## 3. CommChannel Revoke/Replace

- [x] 3.1 Add `revoke(message_id: str) -> bool` and `replace(message_id: str, new_content: str | list[Any]) -> bool` method signatures to `CommChannel` Protocol in `lifecycle/protocols.py`
- [x] 3.2 Implement `DirectChannel.revoke()` returning `False` and `DirectChannel.replace()` returning `False` in `lifecycle/comm_channel.py`
- [x] 3.3 Replace `ProtocolChannel._feedback_queue` from `asyncio.Queue[Feedback]` to `collections.deque[Feedback]` in `lifecycle/comm_channel.py`
- [x] 3.4 Add `_pending: dict[str, Feedback]`, `_revoked: set[str]`, `_delivered: set[str]`, `_enqueued: dict[str, list]` to `ProtocolChannel.__init__` (`_enqueued` stores `PendingMessage` references for PydanticAI-layer revoke)
- [x] 3.5 Implement `ProtocolChannel.deliver_feedback()` with `_revoked` check and `_pending` tracking
- [x] 3.6 Implement `ProtocolChannel.recv()` with `_pending` → `_delivered` transition on dequeue
- [x] 3.7 Implement `ProtocolChannel.revoke()` with two-layer logic: (1) check `_pending` — remove from queue, add to `_revoked`, return `True`; (2) check `_enqueued` — remove each `PendingMessage` from `agent_run.pending_messages` via `list.remove(pm)`, catch `ValueError` (already drained), return `True`; (3) check `_delivered` — return `False`; (4) otherwise return `True` (idempotent unknown)
- [x] 3.8 Implement `ProtocolChannel.replace()` with in-place content update preserving queue position. Accepts `new_content: str | list[Any]` — when `list[Any]`, updates `Feedback.content_blocks`; when `str`, updates `Feedback.content`. Return `False` if `message_id` is in `_enqueued` (already past CommChannel layer) or `_delivered`
- [x] 3.9 Implement `ProtocolChannel._track_enqueued(message_id: str, items: list) -> None` — stores `PendingMessage` references in `_enqueued[message_id]`. Called by `RunHandle.steer()` after `agent_run.enqueue()`
- [x] 3.10 Update `ProtocolChannel.close()`: replace `while not self._feedback_queue.empty(): self._feedback_queue.get_nowait()` with `self._feedback_queue.clear()` and clear `_pending`, `_revoked`, `_delivered`, `_enqueued`
- [x] 3.11 Add unit tests for: revoke before delivery (CommChannel layer), revoke after enqueue (PydanticAI layer), revoke after drain (ValueError caught), revoke after delivery (`False`), revoke unknown (`True`), revoke already-revoked (`True`), replace pending, replace enqueued (`False`), replace delivered (`False`), deliver after revoke rejection, recv marks delivered, _track_enqueued stores references

## 4. RunHandle Steer/Followup/Revoke

- [x] 4.1 Change `RunHandle.steer()` signature to `steer(self, message: str | list[Any], *, message_id: str | None = None) -> str | None` in `orchestrator/run.py`
- [x] 4.2 Change `RunHandle.followup()` signature to `followup(self, message: str | list[Any], *, message_id: str | None = None) -> str | None` in `orchestrator/run.py`
- [x] 4.3 Update `steer()` to construct `Feedback` with `message_id` parameter (or auto-generated UUID). When `message` is a `list`, store in `Feedback.content_blocks` and `content=""`; when `str`, store in `Feedback.content` as before. Return `fb.message_id` on success, `None` on failure
- [x] 4.4 Update `followup()` same as 4.3 but with `is_steer=False`
- [x] 4.5 In `steer()`, when `content_blocks` is present and agent is native: call `agent_run.enqueue(*content_blocks, priority="asap")` instead of `enqueue(message, priority="asap")`. When only `content` (str): call `enqueue(content, priority="asap")` as before. After `enqueue()`, record `queue_len_before = len(agent_run.pending_messages)` before enqueue, then `new_items = agent_run.pending_messages[queue_len_before:]`, then call `self._comm_channel._track_enqueued(fb.message_id, new_items)` if `_comm_channel` is `ProtocolChannel`
- [x] 4.6 Same for `followup()`: unpack `content_blocks` for `enqueue(priority="when_idle")` when present. Note: `followup()` goes through CommChannel path (not direct enqueue), so `_track_enqueued` is NOT needed — the Feedback stays in `_pending` until `recv()` picks it up
- [x] 4.7 Add `RunHandle.revoke(message_id: str) -> bool` method that delegates to `self._comm_channel.revoke(message_id)`. Revoke operates at two layers: (1) CommChannel `_pending` for undelivered feedback, (2) PydanticAI `pending_messages` for already-enqueued steer messages via `_enqueued` tracking + `list.remove(pm)`. If `_comm_channel` is `None` or `DirectChannel`, return `False`
- [x] 4.8 Update `_steer_callback_wrapper()` to handle the new return type (`str | None` instead of `bool`)
- [x] 4.9 Verify all 8 `steer()` call sites in `session_pool.py` and 1 in `session_controller.py`: grep for `is True`, `is False`, and bare statement-style calls (`.steer(` without assignment). No caller SHALL depend on `bool` return type
- [x] 4.10 Add unit tests for: steer with explicit message_id, steer with auto-generated message_id, steer with `list` content (content_blocks), followup with message_id, revoke pending feedback (CommChannel layer), revoke enqueued steer (PydanticAI layer — verify `PendingMessage` removed from `pending_messages`), revoke after drain (ValueError caught, returns `True`), revoke delivered (`False`), revoke unknown (`True`)
- [x] 4.11 Update `SessionPool.steer()`, `SessionPool.followup()`, `SessionPool.inject_prompt()`, `SessionPool.queue_prompt()` signatures to accept `message_id: str | None = None` and `message: str | list[Any]` and pass through to `RunHandle`

## 5. SessionController Extension

- [x] 5.1 Add `message_id: str | None = None` keyword parameter to `SessionController.receive_request()` in `orchestrator/session_controller.py`
- [x] 5.2 Remove the `content_str = " ".join(str(c) for c in content)` stringification in `receive_request()` — preserve `content` as `str | list[Any]` and pass through to `steer()`/`followup()` as-is
- [x] 5.3 Update `_start_run_handle()` to call `run_handle.followup(content, message_id=message_id)` BEFORE `asyncio.create_task(self._consume_run(run_handle, ""))` — initial prompt routes through followup (D17). Return the `message_id` string from `followup()`.
- [x] 5.4 Update `receive_request()` return type annotation from `RunHandle | None` to `str | None` — `str` (message_id) for success (both new runs and steer/followup), `None` for failure
- [x] 5.5 Pass `message_id` to `run.steer()` and `run.followup()` calls in `receive_request()`; return the `message_id` string from `steer()`/`followup()`
- [x] 5.6 Add `SessionController.revoke_inject(session_id: str, message_id: str) -> bool` method that delegates to active `RunHandle.revoke()`
- [x] 5.7 Add `SessionController.wait_for_completion(session_id: str, timeout: float | None = None) -> bool` method — looks up active run via `session.current_run_id` → `self._runs[run_id]` and awaits `run_handle.complete_event.wait()` with timeout. Returns `True` if completed, `False` on timeout or no active run. Also add pass-through on `SessionPool`.
- [x] 5.8 Migrate 2 callers that access `RunHandle` attributes on `receive_request()` return value: (1) `session_routes.py:1935-1941` — replace `run_handle.complete_event.wait()` with `session_pool.wait_for_completion(session_id, timeout=30)`; (2) `acp_server/handler.py:604-607` — replace `run_handle._turn_complete_event.wait()` with `session_pool.wait_for_completion(session_id)` or equivalent
- [x] 5.9 Add unit tests for: `receive_request` with `message_id` propagation, `list` content preservation (no stringification), return type `str | None` verification, initial prompt via followup (D17), `revoke_inject` on active/idle sessions, `wait_for_completion` on active/idle/timed-out sessions

## 5.5. RunHandle Start/Idle Loop Update

- [x] 5.5.1 Change `RunHandle.start()` signature from `start(self, initial_prompt: str)` to `start(self, initial_prompt: str = "")` in `orchestrator/run.py`
- [x] 5.5.2 **CRITICAL**: Change `current_prompts = [initial_prompt]` (line 405) to `current_prompts = [initial_prompt] if initial_prompt else []` — empty string MUST produce `[]` to trigger `_idle_loop()`. Without this, `[""]` is a non-empty list and bypasses `_idle_loop()`, executing a spurious empty-prompt turn.
- [x] 5.5.3 Update `followup()`: construct `Feedback` object BEFORE calling `deliver_feedback()`. If `deliver_feedback()` returns `False` (DirectChannel), append `fb.content` or `fb.content_blocks` to `_message_queue` and return `fb.message_id` — preserves `message_id` for standalone execution (BLOCKER 2 fix)
- [x] 5.5.4 Update all 5 `fb.content` append sites in `_idle_loop()` (lines 533, 549, 561) and `_drain_events()` (lines 864, 866): when `fb.content_blocks` is not `None`, append `fb.content_blocks`; else append `fb.content`. Also update `feedback_steer` type at line 857 from `list[str]` to `list[str | list[Any]]`
- [x] 5.5.5 Change `_message_queue` type from `list[str]` to `list[str | list[Any]]`
- [x] 5.5.6 Change `_execute_turn()` parameter `current_prompts` type from `list[str]` to `list[str | list[Any]]`. Handle `"\n".join(current_prompts)` at line 645: when prompts contain `list` items, extract text from `content_blocks` for `ChatMessage.content` or use `content_blocks` directly
- [x] 5.5.7 Widen `NativeTurn.prompts` type annotation from `list[str]` to `list[str | list[Any]]` (or `list[UserContent]`) to match the base class `BaseAgent.create_turn()` which already accepts `list[UserContent]`
- [x] 5.5.8 For native agents in `_execute_turn()`: when a prompt is `list[Any]`, pass as structured content to the agent turn (e.g. `enqueue(*prompt)`); when `str`, pass as plain text
- [x] 5.5.9 Update `AgentRunContext.queued_steer_messages` type from `list[str]` to `list[str | list[Any]]` (used at `run.py:767, 838` — steer messages on RUNNING agents without active_agent_run or CommChannel)
- [x] 5.5.10 Add `_enqueued` cleanup: after each turn's drain cycle, remove `_enqueued` entries whose `PendingMessage` references are no longer in `agent_run.pending_messages` (identity check). Prevents unbounded memory growth in long-running sessions.
- [x] 5.5.11 Add unit tests for: start with empty initial_prompt (followup path), start with empty string producing `[]` not `[""]`, followup DirectChannel fallback preserving message_id, _idle_loop with content_blocks (all 3 sites), _drain_events with content_blocks (both sites), _execute_turn with list prompt, _enqueued cleanup after drain

## 6. ACPMessageAccumulator Fix

- [x] 6.1 Add `self._current_message_id: str | None = None` to `ACPMessageAccumulator.__init__` in `agents/acp_agent/acp_converters.py`
- [x] 6.2 Update `ACPMessageAccumulator.process()` to read `update.message_id` from `AgentMessageChunk`, `UserMessageChunk`, `AgentThoughtChunk` and store in `self._current_message_id`
- [x] 6.3 Add `message_id` change detection in `process()`: if incoming `update.message_id` differs from `self._current_message_id` and both are non-empty, trigger `_finalize_current_message()` for the previous message before starting the new one. Edge case: when first chunk has `message_id=None`, `_current_message_id` stays `None`; subsequent chunk with `message_id="msg_001"` does NOT trigger finalize (None is empty) — content merges forward into the named message. This is correct behavior: unnamed chunks are absorbed into the next named message.
- [x] 6.4 Update `_finalize_current_message()` to use `self._current_message_id` if non-empty, else fall back to `str(uuid4())`
- [x] 6.5 Reset `self._current_message_id = None` after `_finalize_current_message()` to avoid stale IDs across messages
- [x] 6.6 Add unit tests for preserving incoming `message_id`, falling back to UUID when `None`, `message_id` change triggers finalize, and resetting between messages

## 7. ACPEventConverter Refactor

- [x] 7.1 Remove `_current_message_id` field from `ACPEventConverter` in `agentpool_server/acp_server/event_converter.py`
- [x] 7.2 Remove `_current_message_id` reset in `reset()` method
- [x] 7.3 Update all 7 `AgentMessageChunk.text(...)` / `AgentThoughtChunk.text(...)` yield sites to read `message_id` from the event being converted (or generate one-off UUID for events without `message_id`)
- [x] 7.4 For `StreamCompleteEvent` branch, verify `message.message_id` is used for any final chunk (if applicable)
- [x] 7.5 Add integration tests verifying the `message_id` from `PartStartEvent` appears on the resulting `AgentMessageChunk` notification

## 8. OpenCode Server Alignment

- [x] 8.1 Update `opencode_server/event_processor.py` to read `message_id` from `PartStartEvent`/`PartDeltaEvent` instead of generating `assistant_msg_id` independently
- [x] 8.2 Update `opencode_server/session_pool_integration.py` `_before_consumer_loop()` to read `message_id` from events instead of generating `assistant_msg_id` via `identifier.ascending("message")` — resolves the dual `assistant_msg_id` problem (D14)
- [x] 8.3 Update `opencode_server/routes/message_routes.py` to pass `delivery` from `MessageRequest` to `receive_request(priority=delivery)` instead of hardcoding `priority="when_idle"` (D13)
- [x] 8.4 Update `opencode_server/routes/message_routes.py` to pass `message_id` from `MessageRequest` to `receive_request(message_id=...)` for client-provided ID propagation
- [x] 8.5 Update `opencode_server/routes/session_routes.py` to pass `delivery` and `message_id` for command, fork, and compact routes
- [x] 8.6 Audit and update ALL 9 `assistant_msg_id` generation sites in OpenCode server: (1) `message_routes.py:370` — canonical, (2) `session_pool_integration.py:498` — checkpoint, (3) `session_pool_integration.py:781` — subscribe_to_events, (4) `session_pool_integration.py:932` — _before_consumer_loop, (5) `session_routes.py:204` — slash command, (6) `session_routes.py:432` — skill command, (7) `session_routes.py:1266` — shell command, (8) `session_routes.py:1439` — summarization, (9) `session_routes.py:1876` — MCP prompt. All SHALL read `message_id` from events instead of generating independently (D14 full unification, no technical debt)
- [x] 8.7 Verify OpenCode server event flow produces consistent `message_id` with ACP server — single coherent message ID per turn across ALL files
- [x] 8.8 Audit `agui_server/` and `openai_api_server/` for independent `message_id` generation; update to read from events if found

## 9. Integration Testing

- [x] 9.1 End-to-end test: native agent steer → message_id returned → revoke before delivery → no user_message emitted
- [x] 9.2 End-to-end test: native agent steer → message_id returned → revoke after enqueue but before drain → PendingMessage removed from pending_messages → True
- [x] 9.3 End-to-end test: native agent followup → message_id returned → revoke after delivery → returns False
- [x] 9.4 End-to-end test: external ACP agent sends AgentMessageChunk with message_id → ChatMessage preserves it
- [x] 9.5 End-to-end test: ACPEventConverter produces AgentMessageChunk with message_id matching the native turn's _message_id
- [x] 9.6 Regression test: existing steer/followup calls without message_id still work (auto-generated UUID)
- [x] 9.7 Regression test: existing Feedback construction without new fields still works
- [x] 9.8 End-to-end test: external ACP agent sends multiple AgentMessageChunk with different message_ids → each preserved as separate ChatMessage
- [x] 9.9 End-to-end test: receive_request returns message_id string for both new runs (idle session via followup D17) and steer/followup (busy session), None for failure
- [x] 9.10 End-to-end test: receive_request with list content (multimodal) → content_blocks preserved through pipeline → agent_run.enqueue(*content_blocks) for native agents
- [x] 9.11 End-to-end test: OpenCode server with delivery="steer" → mid-turn injection via enqueue("asap")
- [x] 9.12 End-to-end test: OpenCode server single assistant_msg_id per turn across all event types (text, tools, reasoning, step-start/finish)
## 10. DeliveryMode Enum (Phase 4)

- [x] 10.1 Add `DeliveryMode(enum.Enum)` to `lifecycle/types.py` with values `STEER = "steer"` and `QUEUE = "queue"`. Include docstring mapping to ACP v2, OpenCode, and pydantic-ai internal names.
- [x] 10.2 Verify `Feedback.mode` field (from Task 1.1) uses the same string values (`"steer"` / `"queue"`) so `DeliveryMode` values can be used directly without conversion.
- [x] 10.3 Add unit tests for `DeliveryMode` enum: value equality with `"steer"`/`"queue"`, `Feedback(mode=DeliveryMode.STEER)` construction, `Feedback(mode=DeliveryMode.QUEUE)` construction.

## 11. SessionPool Public API (Phase 4)

- [x] 11.1 Add `SessionController._route_message(session_id, content, *, mode, message_id) -> str | None` internal method. Extracts the dispatch logic from `receive_request()` (idle vs busy session, create run vs steer/followup). Returns `message_id` on success, `None` on failure.
- [x] 11.2 Add `SessionPool.send_message(session_id, content, *, mode=DeliveryMode.QUEUE, message_id=None) -> str | None` to `orchestrator/session_pool.py`. Delegates to `SessionController._route_message()`. When `content` is `list[Any]`, passes as `content_blocks` to `Feedback`; when `str`, passes as `content`.
- [x] 11.3 Add `SessionPool.wait_for_completion(session_id, timeout=None) -> str` to `orchestrator/session_pool.py`. Subscribes to EventBus, waits for `StreamCompleteEvent` or `RunErrorEvent`, extracts text from final message. Raises `asyncio.TimeoutError` on timeout, `SessionNotFoundError` if session missing, `RunError` on agent error.
- [x] 11.4 Add `SessionPool.run_agent(agent: str, prompt: str, parent_session_id: str | None = None, **metadata) -> str` to `orchestrator/session_pool.py`. Creates session via `create_session()`, sends prompt via `send_message(mode=QUEUE)`, waits via `wait_for_completion()`, closes session in `finally`. Logs warning if nesting depth > 3 (tracked via `_run_agent_depth` ContextVar).
- [x] 11.5 Add `SessionPool.revoke_message(session_id, message_id) -> bool` to `orchestrator/session_pool.py`. Wraps `SessionController.revoke_inject()` (from Task 5.4). Returns `True` if revoked, `False` if already delivered.
- [x] 11.6 Add unit tests for: `send_message` with `DeliveryMode.STEER` (mid-turn injection), `send_message` with `DeliveryMode.QUEUE` (next-turn queue), `send_message` with `list` content (content_blocks), `send_message` with explicit `message_id`, `send_message` with auto-generated `message_id`, `run_agent` success path (create → send → wait → close), `run_agent` error path (ensure session cleanup), `wait_for_completion` timeout, `revoke_message` pending (returns `True`), `revoke_message` delivered (returns `False`).

## 12. Deprecation + Migration (Phase 4)

- [x] 12.1 Add `DeprecationWarning` to `SessionPool.receive_request()`. Delegate to `send_message()` with `priority` mapped: `"asap"` → `DeliveryMode.STEER`, `"when_idle"` → `DeliveryMode.QUEUE`. Unknown priority values emit additional `DeprecationWarning` and default to `QUEUE`. Return type narrows from `RunHandle | None` to `str | None`.
- [x] 12.2 Add `DeprecationWarning` to `DelegationService.spawn_subagent()` in `capabilities/delegation.py`. Delegate to `ctx.host.session_pool.run_agent(name, prompt, parent_session_id)`.
- [x] 12.3 Add `DeprecationWarning` to `DelegationService.get_available_agents()`. Delegate to `list(ctx.agent_registry.agent_configs.keys())`.
- [x] 12.4 Add `DeprecationWarning` to `RunLoopDelegationService.spawn_subagent()` in `capabilities/runloop_delegation.py`. Delegate to `self._host.session_pool.run_agent(name, prompt, self._session_id)`.
- [x] 12.5 Migrate `SubagentCapability` (in `capabilities/subagent_capability.py`) from `ctx.delegation.spawn_subagent()` to `ctx.host.session_pool.run_agent()`. Internal change — tool's external behavior unchanged. Keep `ctx.delegation` field on `AgentContext` for backward compat but mark deprecated in docstring.
- [x] 12.6 Grep all `receive_request()` call sites in protocol servers (`acp_server/handler.py`, `opencode_server/session_pool_integration.py`, `agui_server/`, `openai_api_server/`). Verify none use `isinstance(result, RunHandle)` or access `RunHandle`-specific attributes on the return value. All should use truthy check (`if result:`) or `if result is not None:`.
- [x] 12.7 Add regression tests: `receive_request()` with `DeprecationWarning` still returns truthy `str` on success, `None` on failure. `DelegationService.spawn_subagent()` with `DeprecationWarning` still returns result string. `SubagentCapability` `task` tool still works after migration to `run_agent()`.
- [x] 12.8 Add `wait_for_completion()` integration test: send message → wait → verify result text matches agent output. Test with timeout → verify `asyncio.TimeoutError` raised. Test with non-existent session → verify `SessionNotFoundError`.
