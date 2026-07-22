## Context

The OpenCode server's durable execution recovery has a design split between event persistence and event delivery. The M2 lifecycle infrastructure (Journal, SnapshotStore, ProtocolChannel) was designed for crash-safe event persistence, but `UserMessageInsertedEvent` bypasses this entirely — it's published directly to `EventBus.publish()` instead of through `ProtocolChannel.publish()`. This means user messages are never journaled and not replayed during crash recovery.

Additionally, the recovery path in `_before_consumer_loop()` that would restore `EventProcessorContext` from persisted data is dead code: `set_session_context_data()` is never called in production. The `EventProcessorContext.serialize()/deserialize()` methods are fully implemented but unused.

Cross-framework analysis (8 frameworks) confirmed AgentPool is the only framework with dual event publish paths. All others (opencode v2, deer-flow, zed, pydantic-ai-harness, pi-agent, oh-my-openagent, claw-code, hermes) have a unified event entry point.

**Key files**:
- `src/agentpool_server/opencode_server/opencode_event_bridge.py` — `_handle_event()`, `_finalize_assistant_time()`, `_before_consumer_loop()`, `set_session_context_data()`
- `src/agentpool_server/opencode_server/event_processor.py` — `_process_user_message_inserted()`
- `src/agentpool_server/opencode_server/event_processor_context.py` — `EventProcessorContext.serialize()`, `EventProcessorContext.deserialize()`
- `src/agentpool/orchestrator/session_controller_runs.py` — `_emit_user_message_inserted()`, `_route_message()`
- `src/agentpool/lifecycle/comm_channel.py` — `ProtocolChannel.publish()`, `_derive_upsert_key()`
- `src/agentpool/orchestrator/session_controller_close.py` — checkpoint save flow

## Goals / Non-goals

**Goals:**
- User messages are journaled and replayed during crash recovery.
- Protocol-sourced user messages emit `PartUpdatedEvent` so the TUI can render content after SSE reconnection.
- `_message_registered` resets on turn completion, eliminating false "finalize incomplete turn" warnings.
- `EventProcessorContext` is serialized and restored across elicitation resume boundaries using existing infrastructure. Cross-process crash recovery is tracked as follow-up.
- All new test cases pass across 4 layers.

**Non-goals:**
- Redesigning the M2 lifecycle architecture (6 dimensions remain unchanged).
- Unifying the dual publish paths into a single entry point (too large a refactor for this change).
- Adding event IDs / SSE Last-Event-ID support (tracked separately).
- Changing the `start()` generator's internal turn loop.
- Modifying the OpenCode TUI's `sync.session.sync()` or `replayedParts` deduplication logic.

## Decisions

### Decision 1 (P4): Reset `_message_registered` on turn completion

**Choice**: In `opencode_event_bridge.py`'s `_handle_event()`, set `_message_registered[session_id] = False` in both the `StreamCompleteEvent` handler (line ~471-482) and the `RunFailedEvent` handler.

**Rationale**: `_message_registered` tracks whether an assistant message has been registered for the current turn. After `StreamCompleteEvent`, the turn is done — the flag should reset so the next turn starts fresh. Without this reset, the next `RunStartedEvent` finds `_message_registered=True` and triggers `_finalize_assistant_time(warn=True)`, producing the "Finalizing incomplete turn" warning seen in the log.

**Alternative considered**: Reset in `_finalize_assistant_time()` instead. Rejected because `_finalize_assistant_time()` is also called from the `RunStartedEvent` path (D1 block), where it should NOT reset the flag (the flag was just set to `False` before entering the D1 block, and the new assistant registration happens after).

**Risk**: Zero. The flag is only checked in the assistant registration block (lines 649-663), which runs when `_message_registered=False`. Resetting it to `False` after turn completion means the next turn's `RunStartedEvent` won't enter the D1 block, and the next non-spawn event will register a fresh assistant message. This is the correct behavior.

### Decision 2 (P1): Unconditionally emit `PartUpdatedEvent` for protocol-sourced user messages

**Choice**: In `event_processor.py`'s `_process_user_message_inserted()`, remove the `source != "protocol"` guard (line ~1041) and always yield `PartUpdatedEvent` for each part.

**Rationale**: Protocol-sourced messages (from REST handler) currently rely on the TUI's initial `sync.session.sync()` to load parts from the DB. However, the TUI calls `sync()` only ONCE per session (`fullSyncedSessions` set prevents re-sync). The TUI has NO optimistic mechanism — `submitInner()` sends REST and clears input without adding message or parts to the store. `message.updated` SSE events only update metadata, not parts. Without `PartUpdatedEvent`, `store.part[messageID]` is empty and `<Show when={text()}>` renders nothing. This is the root cause of user messages not displaying after the initial sync.

**TUI mechanism**: The TUI app has NO `replayedParts` deduplication (that exists only in the CLI's `stream.transport.ts`). Parts are stored in `store.part[messageID]`, populated by `message.part.updated` SSE events or initial `sync()`. For NEW user messages (after `sync()`), SSE is the ONLY parts source — no duplicate risk. For historical messages loaded by `sync()` AND replayed via SSE, part IDs MUST match to avoid duplicates.

**⚠ Part ID mismatch concern**: The existing code comment at `event_processor.py:1036-1040` warns: "DB-reconstructed parts have different part IDs than the original parts in meta, so sending PartUpdatedEvent would cause the TUI to render both sets of parts — duplicated text." This concern applies ONLY to historical messages replayed via SSE (where `sync()` already loaded parts with DB-generated IDs). For NEW user messages, there is no `sync()` involved — SSE is the only parts source, so no duplicate risk. Task P1.0 SHALL verify part ID alignment for the historical replay case. If IDs differ, fix `_deserialize_part()` to preserve original part IDs from `meta`.

**Alternative considered (1C)**: Add an `is_resume` flag to skip `PartUpdatedEvent` during replay. Rejected — for new messages, no deduplication is needed (SSE is the only source). For historical messages, fixing part ID alignment is a better solution than suppressing events.

**Risk**: Low for new messages (no duplicate risk — SSE is the only parts source). Medium for historical messages replayed via SSE (part ID mismatch could cause duplicates if P1.0 is not resolved). P1.0 MUST verify and fix part ID alignment before P1.1 is applied.

### Decision 3 (P3): Activate recovery path with `set_session_context_data()`

**Choice**:
1. After `StreamCompleteEvent` in `opencode_event_bridge.py`, serialize the current `EventProcessorContext` and call `set_session_context_data(session_id, serialized_data)`.
2. In the checkpoint resume flow (`session_controller_close.py` or `session_controller_agent.py`), when resuming a checkpointed session, call `set_session_context_data(session_id, deserialized_data)` before starting the event consumer.
3. In `_before_consumer_loop()`, the existing code at lines 267-286 already handles restoring from `get_session_context_data()` — no changes needed.

**Rationale**: The `EventProcessorContext.serialize()` and `deserialize()` methods are fully implemented. The `set_session_context_data()` method exists. The `_before_consumer_loop()` restoration code exists. The only missing piece is calling `set_session_context_data()` in production code. This fix wires up existing infrastructure.

**Scope limitation**: `_resume_contexts` is an in-memory dict on `OpenCodeEventBridge`. It does NOT survive server restart. This fix only activates the recovery path for **elicitation resume** (same-process checkpoint → resume). Cross-process crash recovery (server restart) requires persisting the serialized `EventProcessorContext` to the session store — tracked as a follow-up change.

**Serialization timing**: Serialize after `StreamCompleteEvent` (not after every event) to minimize overhead. The serialized context includes `assistant_msg_id`, `assistant_msg`, model metadata, and per-turn state. On resume, `_before_consumer_loop()` restores these and sets `_message_registered=True` so the first event doesn't trigger a spurious assistant registration.

**`_steer_received` edge case**: `EventProcessorContext.serialize()` does not include `_steer_received`. Serialization happens after `StreamCompleteEvent` when `_steer_received` is already `False` (reset during the steer split). If a crash/checkpoint happens during the steer split (after `_steer_received = True` but before `PartStartEvent`), the resumed context won't have `_steer_received = True`, and the steer split won't trigger. This is a narrow race window accepted as an edge case — the steer message will sort after the assistant message instead of between two assistant messages.

**Checkpoint integration**: The checkpoint save flow (`session_controller_close.py`) already saves session state. The serialized `EventProcessorContext` is stored in `_resume_contexts` (in-memory, same-process). For cross-process recovery (server restart), the context must be persisted to the session store — tracked as follow-up.

**Other emission paths out of scope**: There are three `UserMessageInsertedEvent` emission paths: (1) `SessionControllerRunsMixin._emit_user_message_inserted()` from `_route_message()` — addressed by P2, (2) `RunHandle._emit_user_message_inserted()` from `RunHandle.steer(emit_user_message=True)` — suppressed (`emit_user_message=False`) when called from `_route_message()`, only used for direct programmatic calls, (3) `session_pool_messaging.py:steer_from_background_task()` — for background task steers. Paths 2 and 3 represent internal/programmatic operations, not user-initiated protocol messages, and are out of scope for this change.

**Alternative considered**: Move all state to the Journal and reconstruct from replay. Rejected because `EventProcessorContext` contains derived state (e.g., `response_text` accumulated from streaming deltas) that would require complex replay logic to reconstruct.

**Risk**: Medium. Serialization edge cases (e.g., non-serializable fields in `EventProcessorContext`) need testing. The `serialize()` method is implemented but never used in production — may have bugs.

### Decision 4 (P2): Route `UserMessageInsertedEvent` through `ProtocolChannel`

**Choice**:
1. In `session_controller_runs.py`'s `_emit_user_message_inserted()`, for steer/followup messages (messages arriving during an active run), publish through `ProtocolChannel.publish()` instead of direct `EventBus.publish()`.
2. For initial REST messages (messages arriving when the session is idle), keep the direct `EventBus.publish()` path — there's no active `ProtocolChannel` yet (the run hasn't started).
3. In `ProtocolChannel.publish()`, add a deduplication guard: if the event is a `UserMessageInsertedEvent` and `_replaying=True`, skip the EventBus publish (the event was already delivered during the original run).

**Rationale**: The dual publish path exists because `_emit_user_message_inserted()` is called before `_start_run_handle()` for idle sessions — at that point, the `ProtocolChannel` hasn't been created yet. For steer/followup messages, the `ProtocolChannel` already exists (the run is active), so routing through it is straightforward.

**Deduplication**: During crash recovery, `journal.resume()` replays journaled events through `ProtocolChannel.publish()` with `_replaying=True`. If `UserMessageInsertedEvent` was journaled (via this fix), it would be replayed AND fresh-published by `_route_message()`, causing duplicates. The deduplication guard prevents this.

**⚠ Crash-before-delivery edge case**: The deduplication guard assumes the event "was already delivered during the original run." However, if the crash happens **between journaling and EventBus delivery** (journal write succeeds, EventBus publish hasn't run yet), the event is journaled but was never delivered to the TUI. During replay, the guard skips delivery, and the TUI **never sees the user message** — which is exactly the bug being fixed. This is an accepted edge case — the window is microseconds (between `self._journal.append(event)` and `await self._event_bus.publish(...)`) and the alternative (TUI-side deduplication by `message_id`) is out of scope. A future improvement could narrow the guard to only skip when the event was actually delivered (e.g., by checking the EventBus replay buffer).

**`_derive_upsert_key()`**: `UserMessageInsertedEvent` currently hits the `case _: return None` branch (append semantics). This is correct — each user message is a distinct event, not an upsert.

**ProtocolChannel access mechanism**: `_emit_user_message_inserted()` is on `SessionControllerRunsMixin`, which has `_event_bus` but no direct `ProtocolChannel` reference. The `ProtocolChannel` lives on `RunHandle._comm_channel`. Access path: check `session = self.get_session(session_id)`; if `session.current_run_id` is not `None` and the run handle's `_comm_channel` is a `ProtocolChannel`, publish through `comm_channel.publish()`. Otherwise, fall back to direct `EventBus.publish()`.

**Alternative considered (2A)**: Route ALL `UserMessageInsertedEvent` through `ProtocolChannel`. Rejected because for idle sessions, the `ProtocolChannel` doesn't exist yet. Creating a `ProtocolChannel` before the run starts would require rethinking the lifecycle.

**Risk**: Medium. The deduplication guard adds complexity and has the crash-before-delivery edge case (see above). The split path (initial via EventBus, steer via ProtocolChannel) means the journaling behavior differs based on when the message arrives. This is acceptable — initial messages are sent before the run starts, so they don't need journaling (the run hasn't begun). Steer/followup messages arrive during the run and must be journaled for crash recovery.

## Risks / Trade-offs

- **[Risk] P1 part ID mismatch** → Existing code comment warns DB-reconstructed parts have different part IDs than `meta` parts. If `replayedParts` deduplicates by part ID, users will see duplicate text. Mitigated by task P1.0 which MUST verify the deduplication key before removing the guard. If mismatch is confirmed, an additional fix to align part IDs is required.

- **[Risk] P2 crash-before-delivery** → If crash happens between journal write and EventBus publish, the event is journaled but never delivered. The deduplication guard skips replay delivery, and the TUI never sees the message. Accepted edge case — microsecond window, alternative (TUI-side dedup) is out of scope.

- **[Risk] P3 same-process only** → `_resume_contexts` is in-memory. Cross-process crash recovery (server restart) is NOT fixed by P3. Tracked as follow-up change.

- **[Risk] P3 `_steer_received` not serialized** → If crash happens during steer split (after `_steer_received = True`, before `PartStartEvent`), the resumed context won't have `_steer_received = True`. Accepted edge case — narrow window, steer message sorts after assistant message instead of between two.

- **[Risk] P3 serialization edge cases** → `EventProcessorContext.serialize()` is implemented but never used in production. May fail on non-serializable fields. Mitigated by thorough unit tests for serialization round-trip.

- **[Risk] P2 double-publish during replay** → If `UserMessageInsertedEvent` is journaled and then replayed, AND `_route_message()` also publishes it, the TUI receives duplicates. Mitigated by the deduplication guard in `ProtocolChannel.publish()` that skips EventBus publish when `_replaying=True`.

- **[Risk] P2 split path confusion** → Initial messages go through EventBus, steer messages go through ProtocolChannel. Developers may not understand why. Mitigated by clear docstrings and comments explaining the lifecycle constraint.

- **[Trade-off] P1 unconditional PartUpdatedEvent increases SSE traffic** → Each user message now sends N+1 SSE events (1 MessageUpdated + N PartUpdated) instead of 1. This is negligible — user messages are infrequent and small.

- **[Trade-off] P3 serializing after every StreamCompleteEvent adds overhead** → Minimal. Serialization happens once per turn, not per event. The `EventProcessorContext` is small (a few fields).

- **[Trade-off] P2 deduplication guard is ProtocolChannel-specific** → The guard only works if replay goes through `ProtocolChannel`. If a future change adds another replay path, the guard would need updating. This is acceptable — `ProtocolChannel` is the only replay path by design.

## Migration Plan

1. **P4 (1 line)**: Reset `_message_registered` in `StreamCompleteEvent` and `RunFailedEvent` handlers. Independently verifiable.
2. **P1 (remove 1 guard)**: Remove `source != "protocol"` condition in `_process_user_message_inserted()`. Independently verifiable.
3. **P3 (wire up existing code)**: Call `set_session_context_data()` after `StreamCompleteEvent` and in checkpoint resume. No correctness dependency on P4 — `_message_registered` is on the bridge, not in `EventProcessorContext.serialize()`. P4 first is recommended for code organization (both modify the `StreamCompleteEvent` handler).
4. **P2 (route + guard)**: Route steer/followup `UserMessageInsertedEvent` through `ProtocolChannel`. Add deduplication guard. P2's journaling works independently of P3. P2's deduplication guard is only meaningful when P3 activates replay — but the guard can be implemented before P3 without issues.
5. Each fix can be committed independently. P4 and P1 are zero/low risk and can ship immediately. P3 and P2 require more testing.
6. No YAML config changes required — this is purely internal behavior.
7. **Rollback**: Revert individual commits. Each fix is independent.

## Open Questions

- **Follow-up: Cross-process crash recovery** — Persisting `EventProcessorContext` to the session store (not just in-memory `_resume_contexts`) so it survives server restart. P3 only fixes same-process elicitation resume.
- **Follow-up: TUI-side deduplication** — Adding `message_id`-based deduplication for `MessageUpdatedEvent` on the TUI side would eliminate the P2 crash-before-delivery edge case and make the deduplication guard unnecessary. Out of scope for this change.
- Should the deduplication guard in P2 be generalized to all event types, not just `UserMessageInsertedEvent`? Currently only user messages have the double-publish risk, but future events may too.
- Should `set_session_context_data()` be called after `RunFailedEvent` as well as `StreamCompleteEvent`? Yes, for consistency — a failed turn should also persist its context for recovery.
