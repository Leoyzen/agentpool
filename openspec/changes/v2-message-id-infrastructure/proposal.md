## Why

AgentPool's internal architecture has four independent, non-communicating message ID domains (NativeTurn, ACPEventConverter, ACPMessageAccumulator, OpenCode server). The upcoming ACP v2 protocol (RFD #1261 `session/inject`, message-id RFD, v2 prompt lifecycle) requires a single, agent-owned `messageId` that flows end-to-end: from accept → through the event pipeline → to delivery → to revoke. Without unifying these domains now, v2 protocol support will require deep, risky refactors across the entire event and feedback stack.

## What Changes

- **Unify message ID generation**: Single source of truth for `message_id` across native turns, ACP event conversion, and ACP message accumulation. Eliminate independent UUID generation in `ACPEventConverter` and `ACPMessageAccumulator`.
- **Extend `Feedback` type**: Add `message_id` (auto-generated UUID, agent-owned), `content_blocks` (structured content), and `mode` ("steer" | "queue") fields to the `Feedback` dataclass.
- **Add `message_id` to streaming events**: `PartStartEvent` and `PartDeltaEvent` gain a `message_id: str` field so all downstream consumers (ACP, OpenCode, AG-UI) receive the ID without generating their own.
- **Upgrade `ProtocolChannel` feedback queue**: Replace the plain `asyncio.Queue[Feedback]` with an ID-tracked structure supporting `revoke(message_id)` and `replace(message_id, new_content)` operations. Track pending, delivered, and revoked message IDs.
- **Extend `RunHandle.steer()`/`followup()`**: Accept optional `message_id` parameter, return `str | None` (the message_id) instead of `bool`. Add `RunHandle.revoke(message_id)` method.
- **Extend `SessionController.receive_request()`**: Accept optional `message_id` parameter, propagate to `steer()`/`followup()`. Add `revoke_inject(session_id, message_id)` method.
- **Fix `ACPMessageAccumulator` message_id discard**: Preserve incoming `message_id` from external ACP agents instead of generating a fresh UUID at `_finalize_current_message()`.
- **Wire `ACPEventConverter` to event message_id**: Read `message_id` from streaming events instead of maintaining an independent `_current_message_id`.

## Capabilities

### New Capabilities
- `message-id-pipeline`: End-to-end message ID propagation from generation (native turn or ACP inbound) through events, CommChannel feedback, and protocol conversion. Covers ID unification, event-level `message_id` fields, and the `ACPMessageAccumulator` preserve-incoming-ID fix.

### Modified Capabilities
- `steer-followup-api`: `steer()` and `followup()` signatures change to accept optional `message_id` and return `str | None` (the message_id) instead of `bool`. New `revoke(message_id)` method on `RunHandle`.
- `structured-work-channel`: `ProtocolChannel` gains `revoke(message_id)` and `replace(message_id, content)` methods. Feedback queue upgraded from plain FIFO to ID-tracked with pending/delivered/revoked sets.
- `pending-message-queue`: `Feedback` dataclass extended with `message_id`, `content_blocks`, and `mode` fields.

## Impact

- **`lifecycle/types.py`**: `Feedback` dataclass gains 3 fields with defaults (backward compatible).
- **`lifecycle/comm_channel.py`**: `ProtocolChannel` feedback queue restructured; new `revoke()` and `replace()` methods.
- **`lifecycle/protocols.py`**: `CommChannel` Protocol gains `revoke()` and `replace()` method signatures.
- **`orchestrator/run.py`**: `steer()` and `followup()` signature/return-type changes; new `revoke()` method. 2 construction sites + 4 consumption sites of `Feedback` updated.
- **`orchestrator/session_controller.py`**: `receive_request()` gains `message_id` parameter; new `revoke_inject()` method.
- **`orchestrator/session_pool.py`**: `steer()`, `followup()`, `inject_prompt()`, `queue_prompt()` public APIs updated to pass through `message_id`.
- **`agents/events/events.py`**: `PartStartEvent` and `PartDeltaEvent` gain `message_id: str` field.
- **`agents/native_agent/turn.py`**: `NativeTurn._message_id` propagated to `PartStartEvent`/`PartDeltaEvent`.
- **`agents/acp_agent/acp_converters.py`**: `_finalize_current_message()` preserves incoming `message_id`; `process()` reads `update.message_id`.
- **`agentpool_server/acp_server/event_converter.py`**: Reads `message_id` from events instead of independent UUID generation.
- **`agentpool_server/opencode_server/event_processor.py`**: Reads `message_id` from events instead of independent generation.
- **Tests**: New tests for revoke/replace semantics, message_id propagation, and ID-tracked feedback queue.
