## Context

`ACPNotifications.replay()` (`src/acp/agent/notifications.py:475`) replays conversation history during `session/load`. It iterates `Sequence[ModelRequest | ModelResponse]`, converts each part to a `SessionUpdate`, and calls `await self.send_update(update)` per update. `send_update()` constructs a `SessionNotification`, serializes to dict, and calls `await self.client.session_update(notification)`, which calls `await self._conn.send_notification("session/update", dct)` — a JSON-RPC notification over the wire.

Each `send_notification` involves: Pydantic serialization → JSON encoding → TCP write → await flush. For a 100-message session (300-600 updates), this is 300-600 sequential awaits. The `session/update` JSON-RPC notification is one-way (no response expected), but each `await` still pays the TCP flush cost.

The ACP spec's `SessionNotification` schema (`src/acp/schema/notifications.py:17`) has a single `update: TSessionUpdate_co` field — it cannot carry multiple updates in one `session/update` notification.

## Goals / Non-Goals

**Goals:**
- Reduce `replay()` wire roundtrips by 80%+ for typical sessions (50-100 messages)
- Maintain message ordering guarantees within and across batches
- Preserve backward compatibility: clients without batch support receive sequential `session/update` notifications
- No changes to ACP specification — use `ext_notification` extension mechanism

**Non-Goals:**
- Batching real-time streaming notifications (live `session/prompt` turns) — only `replay()` is targeted
- Introducing a new `SessionNotification` schema with array field (would require protocol spec change)
- Cross-session batching or background flush tasks
- Modifying the EventBus or protocol event consumer paths

## Decisions

### Decision 1: ext_notification batch protocol over schema change

**Chosen**: Use ACP's `ext_notification` to send a `_batch_session_updates` notification containing `{ "session_id": str, "updates": [SessionUpdate, ...] }`.

**Rationale**: `SessionNotification.update` is a single `SessionUpdate`, not an array. Changing the schema would break all existing clients. `ext_notification` is the ACP-sanctioned extension point for non-spec methods — prefixed with `_` and ignored by clients that don't understand it.

**Alternative considered**: New `BatchSessionNotification` schema with `updates: list[SessionUpdate]`. Rejected — requires protocol spec change, breaks all clients, and ACP v2 may address this differently.

### Decision 2: Collect-then-send, not pipe-through-queue

**Chosen**: `replay()` first converts all messages to a `list[SessionUpdate]`, then sends in chunks of `notification_batch_size`.

```python
async def replay(self, messages):
    updates = []
    for message in messages:
        match message:
            case ModelRequest():
                updates.extend(await self._collect_request_updates(message))
            case ModelResponse():
                updates.extend(await self._collect_response_updates(message))
    for i in range(0, len(updates), self.notification_batch_size):
        batch = updates[i:i + self.notification_batch_size]
        await self.send_batch_update(batch)
```

**Rationale**: The conversion from `ModelMessage` parts to `SessionUpdate` objects is pure CPU (no I/O). Collecting all updates first is simpler than a streaming pipe and lets us batch cleanly. Memory cost is bounded — a 100-message session produces ~600 `SessionUpdate` objects, each a few hundred bytes.

**Alternative considered**: anyio `MemoryObjectSendStream` with background consumer. Rejected — adds lifecycle complexity for a problem that's fundamentally "collect then chunk".

### Decision 3: Refactor `_replay_request`/`_replay_response` into pure collectors

**Chosen**: Rename `_replay_request` → `_collect_request_updates`, `_replay_response` → `_collect_response_updates`. Return `list[SessionUpdate]` instead of calling `send_update()` directly.

**Rationale**: Current methods mix conversion and I/O. Separating them enables batching and makes the conversion testable without a client connection. The conversion logic (pattern matching on `UserPromptPart`, `TextPart`, etc.) stays identical — only the output changes from `await send_*()` to `list.append(update)`.

### Decision 4: Batch size and flush interval defaults

**Chosen**: `notification_batch_size = 20`, `notification_flush_interval = 0.0` (no artificial delay).

**Rationale**: 20 updates per batch reduces roundtrips by ~20x. No flush interval needed because we send all batches in a tight loop — the `await` on each `send_batch_update` provides natural flow control. The interval is kept as a config knob for future tuning with slow remote clients.

### Decision 5: Fallback detection via client capability check

**Chosen**: Check if the client advertised `_batch_session_updates` support during `initialize`. If not, `send_batch_update()` falls back to looping `send_update()` per update.

**Rationale**: `ext_notification` is fire-and-forget — the agent cannot know if the client processed it. For clients that don't implement batch handling, the notification would be silently dropped, losing replay data. Capability advertisement is the ACP-native way to negotiate extensions.

**Alternative considered**: Always send sequential, let client opt into batch via a separate `session/set_config_option`. Rejected — adds a round-trip and configuration burden.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| Client silently drops `_batch_session_updates` (doesn't implement it but doesn't crash) | Capability check during initialize; fallback to sequential if unsupported |
| Large batch causes TCP buffer pressure on slow connections | Configurable `notification_batch_size`; default 20 is conservative |
| Memory spike for very large sessions (1000+ messages) | Bounded: ~6000 `SessionUpdate` objects × ~200 bytes = ~1.2MB; acceptable |
| Tool call ordering within batch (ToolCallStart must precede ToolCallProgress) | Batch preserves insertion order; `updates` list is built in message order |
| `_tool_call_inputs` cache state during collection | Cache populated during collection phase, consumed during fallback sequential send; batch path doesn't need it (inputs embedded in collected updates) |
