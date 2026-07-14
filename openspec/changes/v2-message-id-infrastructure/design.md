## Context

AgentPool's internal architecture currently has four independent, non-communicating message ID domains:

1. **NativeTurn** (`turn.py:98`): Generates `_message_id = uuid4().hex`, passes to `EventMapper`, stamps onto `ToolCallCompleteEvent.message_id` and the final `ChatMessage.message_id`.
2. **ACPEventConverter** (`event_converter.py:208`): Generates `_current_message_id = uuid.uuid4()` independently. Attaches to all `AgentMessageChunk`/`AgentThoughtChunk`. Never reads `ChatMessage.message_id` from `StreamCompleteEvent`.
3. **ACPMessageAccumulator** (`acp_converters.py:512`): Generates `str(uuid4())` at `_finalize_current_message()`, discarding the incoming `message_id` from external ACP agents' session updates.
4. **OpenCode server** (`event_processor.py`): Generates `assistant_msg_id` independently for its own event processing.

The ACP v2 protocol (RFD #1261 `session/inject`, message-id RFD, v2 prompt lifecycle) requires a single agent-owned `messageId` that flows end-to-end: from accept → through the event pipeline → to delivery → to revoke. The current fragmented architecture makes this impossible without deep refactoring.

Additionally, the `Feedback` dataclass (`lifecycle/types.py:61-72`) has only `content: str` and `is_steer: bool` — no `message_id`, no revoke capability, no structured content. The `ProtocolChannel` feedback queue is a plain `asyncio.Queue[Feedback]` with no ID tracking.

## Goals / Non-Goals

**Goals:**
- Unify message ID generation into a single source of truth per message, flowing through events, CommChannel, and protocol conversion.
- Extend `Feedback` with `message_id`, `content_blocks`, and `mode` to align with v2 `session/inject` semantics.
- Add `revoke(message_id)` and `replace(message_id, content)` to `ProtocolChannel` for pending feedback management.
- Extend `RunHandle.steer()`/`followup()` to accept and return `message_id`.
- Fix `ACPMessageAccumulator` to preserve incoming `message_id` from external ACP agents.
- Wire `ACPEventConverter` to read `message_id` from events instead of generating independently.
- Make the v2 protocol adapter layer a thin routing layer — all semantics handled internally.

**Non-Goals:**
- Implementing the v2 protocol wire format itself (v2 JSON-RPC methods, `user_message` notification, `state_change` notification) — this is a future change.
- Implementing `session/inject` / `session/revoke_inject` / `session/replace_inject` as ACP methods — this change only prepares the internal architecture.
- Modifying the ACP schema types (`BaseChunk.message_id`, `PromptRequest.message_id`, `PromptResponse.user_message_id`) — these already exist and are marked UNSTABLE.
- Changing the `ChatMessage.message_id` field — it already exists with UUID default.
- Steer-in-stream capability declaration (`["interrupt"]` / `["finish"]`) — future v2 protocol work.
- Non-blocking `session/prompt` lifecycle — future v2 protocol work.

## Decisions

### D1: `message_id` as `str` (not `str | None`) on events

**Decision**: `PartStartEvent.message_id` and `PartDeltaEvent.message_id` use `str` with default `""`, not `str | None`.

**Rationale**: v2 requires `message_id` on all message chunks. Using `str` with empty-string default avoids `None` checks throughout the pipeline. Empty string means "not set" — protocol converters can treat `""` as absent for v1 optional semantics. This matches the existing `session_id: str = ""` pattern on events.

**Alternative**: `str | None = None` — rejected because it forces `if event.message_id:` checks at every consumption site and conflicts with v2's required semantics.

**Note**: In JSON serialization, `""` produces `"message_id": ""` (present but empty), which differs from field omission. For v1 backward compatibility, protocol converters SHALL treat `""` as absent. For v2 where `message_id` is required, `""` arriving at a v2 wire encoder is a bug indicator — the future v2 protocol adapter layer SHOULD add a debug-level assertion that `message_id != ""` before sending v2 wire format.

### D2: `Feedback.message_id` auto-generated in `__post_init__`

**Decision**: `Feedback.message_id` defaults to `str(uuid.uuid4())` via `field(default_factory=...)`, not `None`. Callers can override with an explicit value.

**Rationale**: RFD #1261 specifies `messageId` is agent-owned and returned synchronously from `session/inject`. Auto-generation ensures every `Feedback` has a valid ID even when callers don't provide one. This matches `ChatMessage.message_id`'s existing pattern.

**Alternative**: `str | None = None` with auto-generation in `ProtocolChannel.deliver_feedback()` — rejected because it splits the generation logic across two files and makes `Feedback` objects constructed outside `ProtocolChannel` lack IDs.

### D3: `ProtocolChannel` feedback queue restructured to `deque` with ID tracking

**Decision**: Replace `asyncio.Queue[Feedback]` with `collections.deque[Feedback]` plus `dict[str, Feedback] _pending` and `set[str] _revoked`, `set[str] _delivered`.

**Rationale**: `asyncio.Queue` doesn't support removal by value — `revoke()` needs to remove a specific feedback by `message_id` from the middle of the queue. `deque` supports `remove()`. The `_pending` dict provides O(1) lookup for revoke/replace. `_revoked` prevents re-delivery. `_delivered` prevents revoking already-delivered messages.

The `recv()` method changes from `asyncio.Queue.get_nowait()` to `deque.popleft()` with a length check. This is safe because `recv()` is only called from the RunLoop's synchronous drain loops (not from async contexts that need `Queue.get()`'s blocking semantics). The `close()` method SHALL use `self._feedback_queue.clear()` instead of the current `while not empty(): get_nowait()` drain pattern.

**Note**: `deque.remove(feedback)` in `revoke()` is O(n). This is acceptable because feedback queues are tiny (typically 1-3 items). The `_pending` dict provides O(1) lookup to find the Feedback object, but deque removal still scans. If queue sizes grow unexpectedly in the future, consider switching to an `OrderedDict`-based structure.

**Alternative**: Keep `asyncio.Queue` and rebuild it on revoke — rejected as O(n) per revoke and wasteful.

**Alternative**: Use a custom `OrderedDict` as both queue and lookup — rejected as over-engineering; `deque` + `dict` is simpler and standard.

### D4: `steer()`/`followup()` return type changes from `bool` to `str | None`

**Decision**: `RunHandle.steer()` and `RunHandle.followup()` return `str | None` (the `message_id` on success, `None` on failure) instead of `bool`.

**Rationale**: Callers need the `message_id` for subsequent revoke/replace operations. Returning the ID directly avoids a separate lookup. `str | None` is chosen over a tuple `(bool, str | None)` for simplicity.

**Backward compatibility**: Existing callers checking `if run.steer(msg):` still work — truthy `str` is `True` in boolean context, `None` is `False`. No caller in the codebase relies on the exact `bool` return type. Task 4.7 SHALL grep for `is True`, `is False`, AND bare statement-style calls (`.steer(` without assignment) to verify no caller depends on the `bool` type. For new v2 call sites, callers MUST capture the return value to obtain the `message_id` handle.

**Alternative**: Keep `bool` return and add `last_message_id` attribute — rejected as stateful and race-prone with concurrent steer calls.

**Feedback tracking for revoke**: When `steer()` or `followup()` is called via `SessionController.receive_request()` (the protocol-server path), the `Feedback` is delivered through `ProtocolChannel.deliver_feedback()`, which places it in the `_pending` dict. `revoke()` can then remove it before `recv()` delivers it to the RunLoop. Once `recv()` dequeues the `Feedback`, it transitions to `_delivered` and `revoke()` returns `False` — this is correct because the message has already been handed to the agent runtime. For native agents, after `recv()` delivers the `Feedback`, `steer()` calls `pydantic_ai_run.enqueue()` which places the message in PydanticAI's internal queue — at that point the message is beyond revoke scope, which matches the v2 semantic that revoke only works before delivery. Direct `RunHandle.steer()` calls (not through `receive_request()`) do NOT route through `ProtocolChannel.deliver_feedback()` — the `Feedback` is constructed only as a carrier for `message_id` generation, and `revoke()` will return `True` (idempotent unknown) since the `message_id` was never tracked in `_pending`. This is acceptable because v2 protocol handlers always route through `receive_request()`, and internal callers (auto-resume, background tasks) do not need revoke semantics.

### D5: `ACPEventConverter` reads `message_id` from events, stops independent generation

**Decision**: Remove `_current_message_id` from `ACPEventConverter`. Instead, read `event.message_id` from `PartStartEvent`/`PartDeltaEvent`. For events without `message_id` (e.g., error events, compaction), generate a one-off UUID inline.

**Rationale**: The converter's `_current_message_id` is never synced with `ChatMessage.message_id` from `StreamCompleteEvent`, creating a mismatch between what the agent sees and what the client sees. Reading from events ensures the same ID flows from `NativeTurn._message_id` → `PartStartEvent.message_id` → `AgentMessageChunk.message_id`.

**Alternative**: Sync `_current_message_id` from `StreamCompleteEvent.message.message_id` — rejected because `StreamCompleteEvent` arrives after all chunks, too late to affect chunk IDs.

**Multi-message turns**: For native agents, `NativeTurn._message_id` is per-turn — all messages in a turn share the same `message_id`. This is correct for ACP v2 semantics where `messageId` identifies the agent's response, not individual text segments. For external ACP agents, `ACPMessageAccumulator` reads `update.message_id` from the latest chunk. If an external agent sends multiple `AgentMessageChunk` notifications with **different** `message_id` values, a `message_id` change SHALL trigger an implicit `_finalize_current_message()` for the previous message, preserving each message's ID separately. The accumulator SHALL detect `message_id` changes by comparing the incoming `update.message_id` with `self._current_message_id` — if they differ and both are non-empty, finalize the previous message before starting the new one.

### D6: `ACPMessageAccumulator` preserves incoming `message_id`

**Decision**: `_finalize_current_message()` uses `self._current_message_id` (set from the latest incoming `AgentMessageChunk.message_id` / `UserMessageChunk.message_id` / `AgentThoughtChunk.message_id`) instead of always generating `str(uuid4())`. Falls back to `str(uuid4())` only when the incoming `message_id` is `None` or empty.

**Rationale**: External ACP agents (e.g., Goose) assign their own `message_id` values to chunks. Discarding them breaks message identity continuity and prevents v2 features like revoke from working with external agents.

**Alternative**: Always generate new UUID — rejected as it breaks the end-to-end ID contract.

### D7: `CommChannel` Protocol gains `revoke()` and `replace()` methods

**Decision**: The `CommChannel` Protocol in `lifecycle/protocols.py` gains `revoke(message_id: str) -> bool` and `replace(message_id: str, new_content: str) -> bool` method signatures. `DirectChannel` implements both as no-ops returning `False` (no feedback queue). `ProtocolChannel` implements them with real logic.

**Rationale**: Making these part of the Protocol ensures any future `CommChannel` implementation must consider revoke/replace semantics. `DirectChannel` returning `False` is consistent with its existing `deliver_feedback() -> False` pattern.

### D8: `SessionController.receive_request()` gains `message_id` parameter and updated return type

**Decision**: `receive_request()` gains `message_id: str | None = None` keyword parameter. When provided, it's passed to `steer()`/`followup()`. When `None`, `steer()`/`followup()` auto-generates. The return type changes from `RunHandle | None` to `RunHandle | str | None` — `RunHandle` for new runs (idle session), `str` for steer/followup success (the `message_id`), `None` for failure or rejection.

**Rationale**: Protocol handlers that have a client-provided message ID (from v2 `session/inject` request) can pass it through. Internal callers (auto-resume, background tasks) don't need to provide it. The return type change is necessary because v2 `session/inject` requires the protocol handler to return the `message_id` to the client — without it, the handler cannot fulfill the v2 response contract. `RunHandle | str | None` is chosen over a `RequestResult` dataclass for simplicity; if more fields are needed later, a dataclass can be introduced without breaking the `str` case.

**Backward compatibility**: Existing callers that check `if receive_request(...)` still work — `RunHandle` and truthy `str` are both truthy, `None` is falsy.

### D9: `RunHandle.replace()` deferred to future change

**Decision**: `RunHandle.replace(message_id, content)` and `SessionController.replace_inject()` are NOT included in this change. The `CommChannel` Protocol declares `replace()` and `ProtocolChannel` implements it, but `RunHandle` does NOT expose a `replace()` method in this change.

**Rationale**: RFD #1261 marks `session/replace_inject` as opt-in (P3 priority). Including the full `replace` chain (CommChannel → RunHandle → SessionController → protocol handler) adds complexity for a feature that may not be exercised until v2 protocol support lands. The `CommChannel.replace()` implementation is included so the infrastructure is ready, but the RunHandle/SessionController exposure is deferred to the v2 protocol adapter change.

### D10: Crash recovery does not persist pending feedback

**Known limitation**: `_pending`, `_delivered`, and `_revoked` sets in `ProtocolChannel` are in-memory. On crash, all pending feedback is lost. This matches current behavior (`asyncio.Queue` is also in-memory), so it is not a regression. v2 `session/inject` semantics may expect durability for pending messages, but that is out of scope for this change. Future work: if durable feedback is needed, the Journal's tool execution log pattern can be extended to track pending feedback by `message_id`.

### D11: `Feedback.content_blocks` consumption deferred

**Decision**: The `content_blocks` field is added to `Feedback` in this change, but no consumer reads it yet. Protocol converters continue to use `Feedback.content` (plain text). Consumption of `content_blocks` as ACP `ContentBlock[]` wire format is deferred to the v2 protocol adapter change.

**Rationale**: Adding the field now ensures the type is ready for v2 without requiring a second `Feedback` extension. The field defaults to `None`, so existing code is unaffected.

### D12: Thread safety — single event loop thread only

**Constraint**: All `ProtocolChannel` methods (`deliver_feedback()`, `recv()`, `revoke()`, `replace()`, `close()`) MUST be called from the same event loop thread. Cross-thread access requires external synchronization. This is the existing convention for all AgentPool lifecycle components and is not a new constraint, but it is documented here because `revoke()` and `replace()` introduce new mutation paths.

### D13: Map OpenCode `delivery` to `receive_request` priority

**Decision**: OpenCode's `delivery: "steer" | "queue"` maps directly to AgentPool's priority system. `receive_request()` SHALL accept `delivery` as an alias for `priority`: `"steer"` → `"asap"`, `"queue"` → `"when_idle"`. OpenCode route handlers SHALL pass `delivery` from `MessageRequest` to `receive_request()` instead of hardcoding `priority="when_idle"`.

**Rationale**: OpenCode's protocol already has the steer/queue distinction (`SessionDelivery.Delivery = ["steer", "queue"]`), but AgentPool's OpenCode routes currently ignore it. Wiring it through enables mid-turn steer via OpenCode HTTP, matching ACP v2's `session/inject` semantics.

### D14: Resolve dual `assistant_msg_id` in OpenCode server

**Decision**: The OpenCode server currently has TWO independent `assistant_msg_id` generation paths (REST path in `message_routes.py:370` and EventBus consumer in `session_pool_integration.py:932`), creating a split-message issue. The fix: the REST path generates the canonical `assistant_msg_id` using `identifier.ascending("message", request.message_id)`, passes it to `receive_request(message_id=...)`, and the EventBus consumer reads `message_id` from `PartStartEvent` instead of generating its own.

**Rationale**: Content parts (text, tools, reasoning) are currently broadcast linked to the consumer's `assistant_msg_id_B`, while step-start/finish is linked to the REST path's `assistant_msg_id_A`. This creates a split-message issue in the frontend. Reading from events ensures a single coherent message ID.

### D15: OpenCode `message_id` format is opaque to internal pipeline

**Decision**: AgentPool's internal `Feedback.message_id` uses UUID by default, but the OpenCode server uses `identifier.ascending("message")` which produces `msg_*` format IDs. Both are opaque strings — the internal pipeline treats them identically. No format enforcement is applied.

**Rationale**: ACP uses UUID4, OpenCode uses monotonic ascending `msg_*` IDs. Both are valid opaque strings per the message-id RFD. The internal pipeline should not enforce a specific format — protocol converters generate IDs appropriate to their protocol.

### D16: OpenCode abort maps to `RunHandle.cancel()`, not `revoke()`

**Decision**: OpenCode's `POST /abort` is session-level — it cancels the entire run via `RunHandle.cancel()` (existing behavior). ACP v2's `session/revoke_inject` is message-level — it cancels a specific pending inject via `RunHandle.revoke(message_id)`. These are different operations and OpenCode does not need a message-level revoke endpoint in this change.

**Rationale**: OpenCode's protocol has no message-level revoke concept. The `revoke()` infrastructure is built internally for ACP v2 to use, but OpenCode clients continue to use session-level abort.

## Risks / Trade-offs

- **[Queue type change from `asyncio.Queue` to `deque`]** → `recv()` is only called from synchronous drain loops, not from `await queue.get()` contexts. No async semantics are lost. Mitigation: verify all `recv()` call sites are synchronous (they are — 4 sites in `run.py`, all use `while True: fb = recv(); if None: break`).

- **[`steer()` return type change]** → 8 call sites in `session_pool.py` and 1 in `session_controller.py` currently use `run.steer(msg)` in boolean context. Truthy `str` behaves identically to `True`. Mitigation: grep all call sites and verify none depend on `is True` or `is False` checks.

- **[`ACPEventConverter` refactor]** → Removing `_current_message_id` affects 7 yield sites in `event_converter.py`. Each needs to read `event.message_id` instead. Mitigation: all 7 sites are in the same file, changes are mechanical.

- **[External ACP agent message_id preservation]** → Some external agents may not send `message_id` on chunks (it's optional in v1). Mitigation: `_finalize_current_message()` falls back to `str(uuid4())` when incoming `message_id` is `None` or empty.

- **[`Feedback` field additions]** → 2 construction sites (`run.py:925`, `run.py:964`) need to populate new fields. Mitigation: new fields have defaults, so construction without explicit values still works — `message_id` auto-generates, `content_blocks` defaults to `None`, `mode` derives from `is_steer`.

- **[Race between revoke and delivery]** → If `revoke()` is called at the exact moment `recv()` dequeues the feedback, the feedback may already be consumed. Mitigation: `_delivered` set is checked in `revoke()` — if already delivered, return `False` (matching RFD #1261's `already_delivered` error). The race window is single-threaded (all operations are synchronous within the same event loop thread), so it's actually a non-issue in practice.

- **[Multi-message turns with external ACP agents]** → If an external ACP agent sends multiple `AgentMessageChunk` with different `message_id` values without a role switch, the accumulator must detect the `message_id` change and finalize the previous message. Mitigation: `ACPMessageAccumulator.process()` compares incoming `message_id` with `_current_message_id` and triggers `_finalize_current_message()` on change.

- **[`receive_request` return type change]** → Return type changes from `RunHandle | None` to `RunHandle | str | None`. Existing callers using `if receive_request(...):` still work (truthy). Mitigation: grep all call sites and verify none use `isinstance(result, RunHandle)` exclusively.

- **[AG-UI and OpenAI API servers]** → These servers also consume `PartStartEvent`/`PartDeltaEvent` and may have independent `message_id` generation. Mitigation: Task 8.4 audits these servers for independent generation and updates if found.
