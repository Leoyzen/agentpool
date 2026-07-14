## 1. Feedback Type Extension

- [ ] 1.1 Add `message_id`, `content_blocks`, and `mode` fields to `Feedback` dataclass in `lifecycle/types.py` with `__post_init__` for `mode` auto-derivation from `is_steer`
- [ ] 1.2 Add `uuid` import to `lifecycle/types.py` for `message_id` default factory
- [ ] 1.3 Verify existing `Feedback` construction sites (`run.py:925`, `run.py:964`) still work without changes (new fields have defaults)
- [ ] 1.4 Add unit tests for `Feedback` auto-generated `message_id`, explicit `message_id` override, `mode` derivation, and `content_blocks` passthrough

## 2. Event Message ID Propagation

- [ ] 2.1 Add `message_id: str = ""` field to `PartStartEvent` in `agents/events/events.py`
- [ ] 2.2 Add `message_id: str = ""` field to `PartDeltaEvent` in `agents/events/events.py`
- [ ] 2.3 Update `NativeTurn` (`agents/native_agent/turn.py`) to set `message_id=self._message_id` on `PartStartEvent` and propagate to `PartDeltaEvent`
- [ ] 2.4 Update `ACPTurn` (`agents/acp_agent/turn.py`) to set `message_id` from incoming ACP session update's `message_id` field, or generate UUID if absent
- [ ] 2.5 Add unit tests verifying `PartStartEvent` and `PartDeltaEvent` carry the same `message_id` for a single message

## 3. CommChannel Revoke/Replace

- [ ] 3.1 Add `revoke(message_id: str) -> bool` and `replace(message_id: str, new_content: str) -> bool` method signatures to `CommChannel` Protocol in `lifecycle/protocols.py`
- [ ] 3.2 Implement `DirectChannel.revoke()` returning `False` and `DirectChannel.replace()` returning `False` in `lifecycle/comm_channel.py`
- [ ] 3.3 Replace `ProtocolChannel._feedback_queue` from `asyncio.Queue[Feedback]` to `collections.deque[Feedback]` in `lifecycle/comm_channel.py`
- [ ] 3.4 Add `_pending: dict[str, Feedback]`, `_revoked: set[str]`, `_delivered: set[str]`, `_enqueued: dict[str, list]` to `ProtocolChannel.__init__` (`_enqueued` stores `PendingMessage` references for PydanticAI-layer revoke)
- [ ] 3.5 Implement `ProtocolChannel.deliver_feedback()` with `_revoked` check and `_pending` tracking
- [ ] 3.6 Implement `ProtocolChannel.recv()` with `_pending` → `_delivered` transition on dequeue
- [ ] 3.7 Implement `ProtocolChannel.revoke()` with two-layer logic: (1) check `_pending` — remove from queue, add to `_revoked`, return `True`; (2) check `_enqueued` — remove each `PendingMessage` from `agent_run.pending_messages` via `list.remove(pm)`, catch `ValueError` (already drained), return `True`; (3) check `_delivered` — return `False`; (4) otherwise return `True` (idempotent unknown)
- [ ] 3.8 Implement `ProtocolChannel.replace()` with in-place content update preserving queue position. Return `False` if `message_id` is in `_enqueued` (already past CommChannel layer) or `_delivered`
- [ ] 3.9 Implement `ProtocolChannel._track_enqueued(message_id: str, items: list) -> None` — stores `PendingMessage` references in `_enqueued[message_id]`. Called by `RunHandle.steer()` after `agent_run.enqueue()`
- [ ] 3.10 Update `ProtocolChannel.close()`: replace `while not self._feedback_queue.empty(): self._feedback_queue.get_nowait()` with `self._feedback_queue.clear()` and clear `_pending`, `_revoked`, `_delivered`, `_enqueued`
- [ ] 3.11 Add unit tests for: revoke before delivery (CommChannel layer), revoke after enqueue (PydanticAI layer), revoke after drain (ValueError caught), revoke after delivery (`False`), revoke unknown (`True`), revoke already-revoked (`True`), replace pending, replace enqueued (`False`), replace delivered (`False`), deliver after revoke rejection, recv marks delivered, _track_enqueued stores references

## 4. RunHandle Steer/Followup/Revoke

- [ ] 4.1 Change `RunHandle.steer()` signature to `steer(self, message: str | list[Any], *, message_id: str | None = None) -> str | None` in `orchestrator/run.py`
- [ ] 4.2 Change `RunHandle.followup()` signature to `followup(self, message: str | list[Any], *, message_id: str | None = None) -> str | None` in `orchestrator/run.py`
- [ ] 4.3 Update `steer()` to construct `Feedback` with `message_id` parameter (or auto-generated UUID). When `message` is a `list`, store in `Feedback.content_blocks` and `content=""`; when `str`, store in `Feedback.content` as before. Return `fb.message_id` on success, `None` on failure
- [ ] 4.4 Update `followup()` same as 4.3 but with `is_steer=False`
- [ ] 4.5 In `steer()`, when `content_blocks` is present and agent is native: call `agent_run.enqueue(*content_blocks, priority="asap")` instead of `enqueue(message, priority="asap")`. When only `content` (str): call `enqueue(content, priority="asap")` as before. After `enqueue()`, record `queue_len_before = len(agent_run.pending_messages)` before enqueue, then `new_items = agent_run.pending_messages[queue_len_before:]`, then call `self._comm_channel._track_enqueued(fb.message_id, new_items)` if `_comm_channel` is `ProtocolChannel`
- [ ] 4.6 Same for `followup()`: unpack `content_blocks` for `enqueue(priority="when_idle")` when present. Note: `followup()` goes through CommChannel path (not direct enqueue), so `_track_enqueued` is NOT needed — the Feedback stays in `_pending` until `recv()` picks it up
- [ ] 4.7 Add `RunHandle.revoke(message_id: str) -> bool` method that delegates to `self._comm_channel.revoke(message_id)`. Revoke operates at two layers: (1) CommChannel `_pending` for undelivered feedback, (2) PydanticAI `pending_messages` for already-enqueued steer messages via `_enqueued` tracking + `list.remove(pm)`. If `_comm_channel` is `None` or `DirectChannel`, return `False`
- [ ] 4.8 Update `_steer_callback_wrapper()` to handle the new return type (`str | None` instead of `bool`)
- [ ] 4.9 Verify all 8 `steer()` call sites in `session_pool.py` and 1 in `session_controller.py`: grep for `is True`, `is False`, and bare statement-style calls (`.steer(` without assignment). No caller SHALL depend on `bool` return type
- [ ] 4.10 Add unit tests for: steer with explicit message_id, steer with auto-generated message_id, steer with `list` content (content_blocks), followup with message_id, revoke pending feedback (CommChannel layer), revoke enqueued steer (PydanticAI layer — verify `PendingMessage` removed from `pending_messages`), revoke after drain (ValueError caught, returns `True`), revoke delivered (`False`), revoke unknown (`True`)
- [ ] 4.11 Update `SessionPool.steer()`, `SessionPool.followup()`, `SessionPool.inject_prompt()`, `SessionPool.queue_prompt()` signatures to accept `message_id: str | None = None` and `message: str | list[Any]` and pass through to `RunHandle`

## 5. SessionController Extension

- [ ] 5.1 Add `message_id: str | None = None` keyword parameter to `SessionController.receive_request()` in `orchestrator/session_controller.py`
- [ ] 5.2 Remove the `content_str = " ".join(str(c) for c in content)` stringification in `receive_request()` — preserve `content` as `str | list[Any]` and pass through to `steer()`/`followup()` as-is
- [ ] 5.3 Update `_start_run_handle()` to call `run_handle.followup(content, message_id=message_id)` BEFORE `asyncio.create_task(self._consume_run(run_handle, ""))` — initial prompt routes through followup (D17). Return the `message_id` string from `followup()`.
- [ ] 5.4 Update `receive_request()` return type annotation from `RunHandle | None` to `str | None` — `str` (message_id) for success (both new runs and steer/followup), `None` for failure
- [ ] 5.5 Pass `message_id` to `run.steer()` and `run.followup()` calls in `receive_request()`; return the `message_id` string from `steer()`/`followup()`
- [ ] 5.6 Add `SessionController.revoke_inject(session_id: str, message_id: str) -> bool` method that delegates to active `RunHandle.revoke()`
- [ ] 5.7 Add unit tests for: `receive_request` with `message_id` propagation, `list` content preservation (no stringification), return type `str | None` verification, initial prompt via followup (D17), `revoke_inject` on active/idle sessions

## 5.5. RunHandle Start/Idle Loop Update

- [ ] 5.5.1 Change `RunHandle.start()` signature from `start(self, initial_prompt: str)` to `start(self, initial_prompt: str = "")` in `orchestrator/run.py`
- [ ] 5.5.2 When `initial_prompt` is empty, `start()` SHALL fall through to `_idle_loop()` which drains `ProtocolChannel` feedback (including the followup-delivered initial prompt)
- [ ] 5.5.3 Update `_idle_loop()`: when `fb.content_blocks` is not `None`, append `fb.content_blocks` to `_message_queue`; else append `fb.content`
- [ ] 5.5.4 Update `_drain_events()`: same content_blocks handling as `_idle_loop()` for both steer and non-steer feedback
- [ ] 5.5.5 Change `_message_queue` type from `list[str]` to `list[str | list[Any]]`
- [ ] 5.5.6 Change `_execute_turn()` parameter `current_prompts` type from `list[str]` to `list[str | list[Any]]`
- [ ] 5.5.7 For native agents in `_execute_turn()`: when a prompt is `list[Any]`, pass as structured content to the agent turn (e.g. `enqueue(*prompt)`); when `str`, pass as plain text
- [ ] 5.5.8 Add unit tests for: start with empty initial_prompt (followup path), _idle_loop with content_blocks, _drain_events with content_blocks, _execute_turn with list prompt

## 6. ACPMessageAccumulator Fix

- [ ] 6.1 Add `self._current_message_id: str | None = None` to `ACPMessageAccumulator.__init__` in `agents/acp_agent/acp_converters.py`
- [ ] 6.2 Update `ACPMessageAccumulator.process()` to read `update.message_id` from `AgentMessageChunk`, `UserMessageChunk`, `AgentThoughtChunk` and store in `self._current_message_id`
- [ ] 6.3 Add `message_id` change detection in `process()`: if incoming `update.message_id` differs from `self._current_message_id` and both are non-empty, trigger `_finalize_current_message()` for the previous message before starting the new one
- [ ] 6.4 Update `_finalize_current_message()` to use `self._current_message_id` if non-empty, else fall back to `str(uuid4())`
- [ ] 6.5 Reset `self._current_message_id = None` after `_finalize_current_message()` to avoid stale IDs across messages
- [ ] 6.6 Add unit tests for preserving incoming `message_id`, falling back to UUID when `None`, `message_id` change triggers finalize, and resetting between messages

## 7. ACPEventConverter Refactor

- [ ] 7.1 Remove `_current_message_id` field from `ACPEventConverter` in `agentpool_server/acp_server/event_converter.py`
- [ ] 7.2 Remove `_current_message_id` reset in `reset()` method
- [ ] 7.3 Update all 7 `AgentMessageChunk.text(...)` / `AgentThoughtChunk.text(...)` yield sites to read `message_id` from the event being converted (or generate one-off UUID for events without `message_id`)
- [ ] 7.4 For `StreamCompleteEvent` branch, verify `message.message_id` is used for any final chunk (if applicable)
- [ ] 7.5 Add integration tests verifying the `message_id` from `PartStartEvent` appears on the resulting `AgentMessageChunk` notification

## 8. OpenCode Server Alignment

- [ ] 8.1 Update `opencode_server/event_processor.py` to read `message_id` from `PartStartEvent`/`PartDeltaEvent` instead of generating `assistant_msg_id` independently
- [ ] 8.2 Update `opencode_server/session_pool_integration.py` `_before_consumer_loop()` to read `message_id` from events instead of generating `assistant_msg_id` via `identifier.ascending("message")` — resolves the dual `assistant_msg_id` problem (D14)
- [ ] 8.3 Update `opencode_server/routes/message_routes.py` to pass `delivery` from `MessageRequest` to `receive_request(priority=delivery)` instead of hardcoding `priority="when_idle"` (D13)
- [ ] 8.4 Update `opencode_server/routes/message_routes.py` to pass `message_id` from `MessageRequest` to `receive_request(message_id=...)` for client-provided ID propagation
- [ ] 8.5 Update `opencode_server/routes/session_routes.py` to pass `delivery` and `message_id` for command, fork, and compact routes
- [ ] 8.6 Audit ALL remaining `assistant_msg_id` generation sites in OpenCode server (`stream_adapter.py`, `session_routes.py`, and any other files) — ALL sites SHALL read `message_id` from events instead of generating independently (D14 full unification, no technical debt)
- [ ] 8.7 Verify OpenCode server event flow produces consistent `message_id` with ACP server — single coherent message ID per turn across ALL files
- [ ] 8.8 Audit `agui_server/` and `openai_api_server/` for independent `message_id` generation; update to read from events if found

## 9. Integration Testing

- [ ] 9.1 End-to-end test: native agent steer → message_id returned → revoke before delivery → no user_message emitted
- [ ] 9.2 End-to-end test: native agent steer → message_id returned → revoke after enqueue but before drain → PendingMessage removed from pending_messages → True
- [ ] 9.3 End-to-end test: native agent followup → message_id returned → revoke after delivery → returns False
- [ ] 9.4 End-to-end test: external ACP agent sends AgentMessageChunk with message_id → ChatMessage preserves it
- [ ] 9.5 End-to-end test: ACPEventConverter produces AgentMessageChunk with message_id matching the native turn's _message_id
- [ ] 9.6 Regression test: existing steer/followup calls without message_id still work (auto-generated UUID)
- [ ] 9.7 Regression test: existing Feedback construction without new fields still works
- [ ] 9.8 End-to-end test: external ACP agent sends multiple AgentMessageChunk with different message_ids → each preserved as separate ChatMessage
- [ ] 9.9 End-to-end test: receive_request returns message_id string for both new runs (idle session via followup D17) and steer/followup (busy session), None for failure
- [ ] 9.10 End-to-end test: receive_request with list content (multimodal) → content_blocks preserved through pipeline → agent_run.enqueue(*content_blocks) for native agents
- [ ] 9.11 End-to-end test: OpenCode server with delivery="steer" → mid-turn injection via enqueue("asap")
- [ ] 9.12 End-to-end test: OpenCode server single assistant_msg_id per turn across all event types (text, tools, reasoning, step-start/finish)
