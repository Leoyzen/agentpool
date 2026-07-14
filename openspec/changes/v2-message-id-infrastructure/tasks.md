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
- [ ] 3.4 Add `_pending: dict[str, Feedback]`, `_revoked: set[str]`, `_delivered: set[str]` to `ProtocolChannel.__init__`
- [ ] 3.5 Implement `ProtocolChannel.deliver_feedback()` with `_revoked` check and `_pending` tracking
- [ ] 3.6 Implement `ProtocolChannel.recv()` with `_pending` → `_delivered` transition on dequeue
- [ ] 3.7 Implement `ProtocolChannel.revoke()` with pending/delivered/unknown semantics (idempotent for unknown/revoked, `False` for delivered)
- [ ] 3.8 Implement `ProtocolChannel.replace()` with in-place content update preserving queue position
- [ ] 3.9 Update `ProtocolChannel.close()`: replace `while not self._feedback_queue.empty(): self._feedback_queue.get_nowait()` with `self._feedback_queue.clear()` and clear `_pending`, `_revoked`, `_delivered`
- [ ] 3.10 Add unit tests for revoke before delivery, revoke after delivery, revoke unknown, revoke already-revoked, replace pending, replace delivered, deliver after revoke rejection, recv marks delivered

## 4. RunHandle Steer/Followup/Revoke

- [ ] 4.1 Change `RunHandle.steer()` signature to `steer(self, message: str, *, message_id: str | None = None) -> str | None` in `orchestrator/run.py`
- [ ] 4.2 Change `RunHandle.followup()` signature to `followup(self, message: str, *, message_id: str | None = None) -> str | None` in `orchestrator/run.py`
- [ ] 4.3 Update `steer()` to construct `Feedback` with `message_id` parameter (or auto-generated UUID) and return `fb.message_id` on success, `None` on failure
- [ ] 4.4 Update `followup()` to construct `Feedback` with `message_id` parameter (or auto-generated UUID) and return `fb.message_id` on success, `None` on failure
- [ ] 4.5 Add `RunHandle.revoke(message_id: str) -> bool` method that delegates to `self._comm_channel.revoke(message_id)` if `_comm_channel` is not None, else returns `False`
- [ ] 4.6 Update `_steer_callback_wrapper()` to handle the new return type (`str | None` instead of `bool`)
- [ ] 4.7 Verify all 8 `steer()` call sites in `session_pool.py` and 1 in `session_controller.py`: grep for `is True`, `is False`, and bare statement-style calls (`.steer(` without assignment). No caller SHALL depend on `bool` return type
- [ ] 4.8 Add unit tests for steer with explicit message_id, steer with auto-generated message_id, followup with message_id, revoke pending, revoke delivered, revoke unknown
- [ ] 4.9 Update `SessionPool.steer()`, `SessionPool.followup()`, `SessionPool.inject_prompt()`, `SessionPool.queue_prompt()` signatures to accept `message_id: str | None = None` and pass through to `RunHandle`

## 5. SessionController Extension

- [ ] 5.1 Add `message_id: str | None = None` keyword parameter to `SessionController.receive_request()` in `orchestrator/session_controller.py`
- [ ] 5.2 Pass `message_id` to `run.steer()` and `run.followup()` calls in `receive_request()`; return the `message_id` string (from steer/followup) or `RunHandle` (for new runs) or `None` (failure)
- [ ] 5.3 Update `receive_request()` return type annotation from `RunHandle | None` to `RunHandle | str | None`
- [ ] 5.4 Add `SessionController.revoke_inject(session_id: str, message_id: str) -> bool` method that delegates to active `RunHandle.revoke()`
- [ ] 5.5 Add unit tests for `receive_request` with `message_id` propagation, return type verification, and `revoke_inject` on active/idle sessions

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
- [ ] 8.6 Verify OpenCode server event flow produces consistent `message_id` with ACP server — single coherent message ID per turn
- [ ] 8.7 Audit `agui_server/` and `openai_api_server/` for independent `message_id` generation; update to read from events if found

## 9. Integration Testing

- [ ] 9.1 End-to-end test: native agent steer → message_id returned → revoke before delivery → no user_message emitted
- [ ] 9.2 End-to-end test: native agent followup → message_id returned → revoke after delivery → returns False
- [ ] 9.3 End-to-end test: external ACP agent sends AgentMessageChunk with message_id → ChatMessage preserves it
- [ ] 9.4 End-to-end test: ACPEventConverter produces AgentMessageChunk with message_id matching the native turn's _message_id
- [ ] 9.5 Regression test: existing steer/followup calls without message_id still work (auto-generated UUID)
- [ ] 9.6 Regression test: existing Feedback construction without new fields still works
- [ ] 9.7 End-to-end test: external ACP agent sends multiple AgentMessageChunk with different message_ids → each preserved as separate ChatMessage
- [ ] 9.8 End-to-end test: receive_request returns message_id string for steer on busy session, RunHandle for idle session, None for failure
