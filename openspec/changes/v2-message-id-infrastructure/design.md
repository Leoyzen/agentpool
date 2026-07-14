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

### D8: `SessionController.receive_request()` gains `message_id` parameter, return type simplifies to `str | None`

**Decision**: `receive_request()` gains `message_id: str | None = None` keyword parameter. The return type changes from `RunHandle | None` to `str | None` — `str` (the `message_id`) for success (both new runs and steer/followup), `None` for failure or rejection. The `RunHandle` case is eliminated because initial prompts now route through `followup()` (see D17), which returns `str`.

**Rationale**: Protocol handlers that have a client-provided message ID (from v2 `session/inject` or `session/prompt` request) can pass it through. Internal callers (auto-resume, background tasks) don't need to provide it. The return type simplifies because every code path (new run, steer, followup) now goes through `Feedback` → `message_id` → `str` return. The protocol handler subscribes to the `EventBus` and filters by `session_id` — it does not need the `RunHandle` directly.

**Backward compatibility**: Existing callers that check `if receive_request(...)` still work — truthy `str` is truthy, `None` is falsy. However, **2 existing callers access `RunHandle`-specific attributes** on the return value and MUST be migrated:

1. `session_routes.py:1935-1941`: `run_handle.complete_event.wait()` — waits for run completion
2. `acp_server/handler.py:604-607`: `run_handle._turn_complete_event.wait()` — waits for turn completion

**Fix**: Add `wait_for_completion(session_id: str, timeout: float | None = None) -> bool` method to `SessionController` and `SessionPool`. This method looks up the active run via `session.current_run_id` → `self._runs[run_id]` and awaits `run_handle.complete_event.wait()` with the given timeout. Both callers are migrated from `run_handle.complete_event.wait()` to `session_pool.wait_for_completion(session_id, timeout=30)`. This decouples callers from the `RunHandle` type entirely.

### D9: `RunHandle.replace()` deferred to future change

**Decision**: `RunHandle.replace(message_id, content)` and `SessionController.replace_inject()` are NOT included in this change. The `CommChannel` Protocol declares `replace()` and `ProtocolChannel` implements it, but `RunHandle` does NOT expose a `replace()` method in this change.

**Rationale**: RFD #1261 marks `session/replace_inject` as opt-in (P3 priority). Including the full `replace` chain (CommChannel → RunHandle → SessionController → protocol handler) adds complexity for a feature that may not be exercised until v2 protocol support lands. The `CommChannel.replace()` implementation is included so the infrastructure is ready, but the RunHandle/SessionController exposure is deferred to the v2 protocol adapter change.

### D10: Crash recovery does not persist pending feedback

**Known limitation**: `_pending`, `_delivered`, and `_revoked` sets in `ProtocolChannel` are in-memory. On crash, all pending feedback is lost. This matches current behavior (`asyncio.Queue` is also in-memory), so it is not a regression. v2 `session/inject` semantics may expect durability for pending messages, but that is out of scope for this change. Future work: if durable feedback is needed, the Journal's tool execution log pattern can be extended to track pending feedback by `message_id`.

### D11: `Feedback.content_blocks` activated — pipeline carries structured content

**Decision**: The `content_blocks` field on `Feedback` is **activated** in this change. The internal pipeline (`receive_request()` → `Feedback` → `steer()`/`followup()` → `agent_run.enqueue()`) carries structured content through without stringification. Specifically:

- `receive_request()` stops stringifying `list` content to `str` — preserves `str | list[Any]` as-is.
- `steer()` and `followup()` accept `message: str | list[Any]`. When `list`, it is stored in `Feedback.content_blocks`; when `str`, in `Feedback.content`.
- For native agents: when `content_blocks` is present, `steer()` calls `agent_run.enqueue(*content_blocks)` (PydanticAI's `enqueue` already supports multimodal `UserContent` — `ImageUrl`, `BinaryContent`, `TextContent`). When only `content` (str), calls `enqueue(content)` as before.
- For non-native (ACP) agents: `content_blocks` is passed through to the injection path. ACP-specific conversion is deferred (see Future Work).

**Rationale**: `receive_request()` currently does `content_str = " ".join(str(c) for c in content)`, which destroys multimodal content (images become `<ImageContentBlock ...>` strings). PydanticAI's `enqueue()` already accepts `EnqueueContent = UserContent | ModelRequestPart | ModelMessage` where `UserContent = str | TextContent | MultiModalContent | CachePoint`. The pipeline should be content-agnostic — it carries `list[Any]` transparently without caring what's inside.

**Future Work (NOT in this change)**:
- ACP `ContentBlock` (TextContentBlock, ImageContentBlock, AudioContentBlock, etc.) → PydanticAI `UserContent` (str, ImageUrl, BinaryContent) type mapping. This belongs in the v2 ACP protocol adapter.
- OpenCode `parts` (TextPartInput, FilePartInput) → internal `content_blocks` mapping. This belongs in the OpenCode v2 route handler.
- Protocol converter consumption of `content_blocks` for wire serialization (ACP outbound `ContentBlock[]`). Deferred to v2 protocol adapter.
- `Feedback.content_blocks` typed as `list[Any]` (opaque to internal pipeline) in this change. A stronger typed union (`list[ContentBlock] | list[UserContent]`) is deferred to avoid leaking protocol-specific types into the internal layer.

### D12: Thread safety — single event loop thread only

**Constraint**: All `ProtocolChannel` methods (`deliver_feedback()`, `recv()`, `revoke()`, `replace()`, `close()`) MUST be called from the same event loop thread. Cross-thread access requires external synchronization. This is the existing convention for all AgentPool lifecycle components and is not a new constraint, but it is documented here because `revoke()` and `replace()` introduce new mutation paths.

### D12.1: Revoke operates at two layers — CommChannel queue and PydanticAI pending_messages

**Decision**: `revoke(message_id)` SHALL operate at two layers to cover both `steer()` code paths:

1. **CommChannel layer** (`ProtocolChannel._pending`): For messages still pending in the feedback queue (followup messages, steer on idle agents, steer in turn gaps). `revoke()` removes from `_pending` dict. This covers `followup()` and the CommChannel path of `steer()`.

2. **PydanticAI queue layer** (`agent_run.pending_messages`): For steer messages that went directly to `enqueue()` (the most common steer case — agent RUNNING with active agent_run). After `enqueue()`, `steer()` SHALL record the newly appended `PendingMessage` references by slicing `agent_run.pending_messages[queue_len_before:]`. `revoke()` SHALL remove these references from the live `pending_messages` list via `list.remove(pm)` (identity comparison).

**Revoke window**: The PydanticAI-layer revoke window is from `enqueue()` (t0) to `before_model_request` drain (t1). During model generation (waiting for API response), the message sits in `pending_messages` and can be removed. Once `_drain_by_priority()` consumes it, `list.remove(pm)` raises `ValueError` — caught and treated as "already consumed" (`revoke()` returns `True`, idempotent).

**Evidence**: PydanticAI's `PendingMessage` has no `message_id` field and `enqueue()` returns `None`, but `agent_run.pending_messages` exposes the live `list[PendingMessage]` for inspection. `list.remove(pm)` uses identity (`is`) comparison, which reliably finds the exact `PendingMessage` object appended by `enqueue()`. Both `remove()` and `_drain_by_priority()` run on the same event loop thread — no true concurrency, only interleaving at `await` points. The drain only fires at `before_model_request` and `after_node_run` hooks, not during model API calls.

**Tracking mechanism**: `ProtocolChannel` SHALL maintain an `_enqueued: dict[str, list[PendingMessage]]` mapping. `steer()` calls `comm_channel._track_enqueued(message_id, new_items)` after `enqueue()`. `revoke()` checks `_pending` first, then `_enqueued`. On successful revoke from `_enqueued`, the `PendingMessage` references are removed from `agent_run.pending_messages` via `list.remove(pm)`.

**Race safety**: If `_drain_by_priority()` runs between `enqueue()` and `revoke()`, the `PendingMessage` is already removed from the list. `list.remove(pm)` raises `ValueError`, which is caught — `revoke()` returns `True` (idempotent: the message is no longer in the queue, same end state as revoke). If `revoke()` runs before drain, the `PendingMessage` is removed from the list, and the subsequent drain's `_drain_by_priority()` simply won't find it (it iterates the list and builds a `remaining` list). Both paths are safe.

**Limitation**: Once the drain has consumed the `PendingMessage` and appended its `ModelMessage`s to `ctx.messages` (the conversation history), the message is irreversible. `revoke()` returns `True` (idempotent) but cannot undo the injection. This matches ACP v2 semantics — revoke is best-effort, not guaranteed.

**Cleanup**: `_enqueued` entries SHALL be cleaned up after each turn's drain cycle. When `_drain_by_priority()` consumes `PendingMessage`s from `agent_run.pending_messages`, the corresponding `_enqueued` entries SHALL be removed. This prevents unbounded memory growth in long-running sessions. Implementation: after `_idle_loop()` or `_drain_events()` completes, iterate `_enqueued` and remove entries whose `PendingMessage` references are no longer in `agent_run.pending_messages` (identity check). Alternatively, clean up in `revoke()` after the drain check (catch `ValueError` → remove from `_enqueued`).

### D13: Map OpenCode `delivery` to `receive_request` priority

**Decision**: OpenCode's `delivery: "steer" | "queue"` maps directly to AgentPool's priority system. `receive_request()` SHALL accept `delivery` as an alias for `priority`: `"steer"` → `"asap"`, `"queue"` → `"when_idle"`. OpenCode route handlers SHALL pass `delivery` from `MessageRequest` to `receive_request()` instead of hardcoding `priority="when_idle"`.

**Rationale**: OpenCode's protocol already has the steer/queue distinction (`SessionDelivery.Delivery = ["steer", "queue"]`), but AgentPool's OpenCode routes currently ignore it. Wiring it through enables mid-turn steer via OpenCode HTTP, matching ACP v2's `session/inject` semantics. This is a protocol completeness fix — the server respects the client's `delivery` field value regardless of whether the frontend currently uses `delivery: "steer"`.

### D14: Resolve dual `assistant_msg_id` in OpenCode server

**Decision**: The OpenCode server currently has multiple independent `assistant_msg_id` generation paths, creating a split-message issue. **All** generation sites SHALL be unified in this change — no technical debt left behind. The fix: the REST path generates the canonical `assistant_msg_id` using `identifier.ascending("message", request.message_id)`, passes it to `receive_request(message_id=...)`, and ALL consumers read `message_id` from events instead of generating their own.

**Known generation sites** (9 total, to be verified during implementation):
1. `message_routes.py:370` — REST message handler (canonical, generates the ID)
2. `session_pool_integration.py:498` — checkpoint reconstruction
3. `session_pool_integration.py:781` — `subscribe_to_events()` (test/utility)
4. `session_pool_integration.py:932` — `_before_consumer_loop()` (the critical dual-ID issue)
5. `session_routes.py:204` — slash command execution
6. `session_routes.py:432` — skill command execution
7. `session_routes.py:1266` — shell command execution
8. `session_routes.py:1439` — session summarization
9. `session_routes.py:1876` — MCP prompt command execution

Sites 1 and 4 are the standard message flow (split-message problem). Sites 2, 5-9 serve different endpoints (slash commands, shell, summarize, MCP commands) — they SHALL also pass `message_id` to `receive_request()` and let the ID propagate through events. Task 8.6 SHALL verify all 9 sites are updated.

**Rationale**: Content parts (text, tools, reasoning) are currently broadcast linked to the consumer's `assistant_msg_id_B`, while step-start/finish is linked to the REST path's `assistant_msg_id_A`. This creates a split-message issue in the frontend. Reading from events ensures a single coherent message ID.

### D15: OpenCode `message_id` format is opaque to internal pipeline

**Decision**: AgentPool's internal `Feedback.message_id` uses UUID by default, but the OpenCode server uses `identifier.ascending("message")` which produces `msg_*` format IDs. Both are opaque strings — the internal pipeline treats them identically. No format enforcement is applied.

**Rationale**: ACP uses UUID4, OpenCode uses monotonic ascending `msg_*` IDs. Both are valid opaque strings per the message-id RFD. The internal pipeline should not enforce a specific format — protocol converters generate IDs appropriate to their protocol.

### D16: OpenCode abort maps to `RunHandle.cancel()`, not `revoke()`

**Decision**: OpenCode's `POST /abort` is session-level — it cancels the entire run via `RunHandle.cancel()` (existing behavior). ACP v2's `session/revoke_inject` is message-level — it cancels a specific pending inject via `RunHandle.revoke(message_id)`. These are different operations and OpenCode does not need a message-level revoke endpoint in this change.

**Rationale**: OpenCode's protocol has no message-level revoke concept. The `revoke()` infrastructure is built internally for ACP v2 to use, but OpenCode clients continue to use session-level abort.

### D17: Initial prompt reuses `followup()` — unified code path with `message_id`

**Decision**: When `receive_request()` starts a new run (idle session), the initial prompt SHALL be delivered via `run_handle.followup(content, message_id=message_id)` BEFORE `start()` is called. `start()` is called with `initial_prompt=""` — the first `_idle_loop()` iteration drains the `followup()` feedback from `ProtocolChannel._pending` and uses it as the first turn's prompt.

**Current flow** (eliminated):
```
receive_request() → _start_run_handle(content)
  → start(initial_prompt=content)
    → current_prompts = [content]  ← no Feedback, no message_id
```

**New flow**:
```
receive_request() → _start_run_handle(content, message_id)
  → run_handle.followup(content, message_id=msg_id)  ← Feedback in _pending (ProtocolChannel) or _message_queue (DirectChannel fallback)
  → start(initial_prompt="")
    → current_prompts = [] if not initial_prompt else [initial_prompt]  ← CRITICAL: empty string must produce []
      → _idle_loop() → recv() → fb  (ProtocolChannel) or _message_queue already has content (DirectChannel)
        → current_prompts = [fb.content or fb.content_blocks]
```

**Behavioral equivalence**: Both flows result in `_execute_turn(prompts=[content])`. The end state is identical — the first turn processes the same prompt content.

**CRITICAL implementation detail**: The current code at `run.py:405` does `current_prompts = [initial_prompt]` unconditionally. When `initial_prompt=""`, this produces `[""]` — a non-empty list with one empty string element. The `if not current_prompts:` check at line 407 tests list emptiness, not element truthiness, so `[""]` passes through to `_execute_turn()` with an empty prompt. The fix: change line 405 to `current_prompts = [initial_prompt] if initial_prompt else []` — only create the list when `initial_prompt` is truthy (non-empty).

**DirectChannel fallback**: For standalone execution (`DirectChannel`), `deliver_feedback()` returns `False` and `recv()` returns `None`. The `followup()` method falls back to `_message_queue.append(message)`. In the updated `followup()`, a `Feedback` object SHALL be constructed **before** the fallback to preserve `message_id` generation. The `Feedback` content (or `content_blocks`) is appended to `_message_queue`, and `followup()` returns `fb.message_id`. The `_idle_loop()` checks `_message_queue` directly (lines 533, 549, 561) and will find the content without calling `recv()`.

**Advantages**:
- Initial prompt gets a `message_id` automatically — ACP v2 `session/prompt` can return `user_message_id`
- Revoke works on the initial prompt (before `start()` picks it up from `_idle_loop()`)
- No special-casing for "initial prompt" — all prompts go through the same `Feedback` → `ProtocolChannel` → `_idle_loop` path
- `content_blocks` (multimodal) flows through the same `Feedback` path
- `receive_request()` return type simplifies to `str | None` (always `message_id` on success)

**Implementation changes**:
1. `_start_run_handle()`: call `followup(content, message_id=...)` before `asyncio.create_task(start(""))`, return `message_id`
2. `start()`: `initial_prompt: str = ""` (default empty). **CRITICAL**: change `current_prompts = [initial_prompt]` to `current_prompts = [initial_prompt] if initial_prompt else []` — ensures empty string produces `[]` which triggers `_idle_loop()`
3. `followup()`: construct `Feedback` object BEFORE the `deliver_feedback()` call. If `deliver_feedback()` returns `False` (DirectChannel), append `fb.content` or `fb.content_blocks` to `_message_queue` and return `fb.message_id` — preserves `message_id` for standalone execution
4. `_idle_loop()` and `_drain_events()`: read `fb.content` AND `fb.content_blocks` from `Feedback` — `_message_queue` type changes from `list[str]` to `list[str | list[Any]]`. **5 append sites** in `run.py` need updating: lines 533, 549, 561 (in `_idle_loop`), 864, 866 (in `_drain_events`). Also `feedback_steer` type at line 857 changes from `list[str]` to `list[str | list[Any]]`.
5. `_execute_turn()`: `current_prompts` type changes from `list[str]` to `list[str | list[Any]]`. The `"\n".join(current_prompts)` at line 645 MUST handle `list` items — extract text from `content_blocks` for the `ChatMessage.content` field, or use `content_blocks` directly.
6. `NativeTurn.prompts` type annotation: widen from `list[str]` to `list[str | list[Any]]` (or `list[UserContent]`) to match the base class `BaseAgent.create_turn()` which already accepts `list[UserContent]`.

**Race safety**: `followup()` is called synchronously before `asyncio.create_task()`. The `Feedback` is in `ProtocolChannel._pending` before `start()` runs. `_idle_loop()` clears `_idle_event` then immediately calls `recv()` — finds the feedback without blocking. No race.

## Risks / Trade-offs

- **[Queue type change from `asyncio.Queue` to `deque`]** → `recv()` is only called from synchronous drain loops, not from `await queue.get()` contexts. No async semantics are lost. Mitigation: verify all `recv()` call sites are synchronous (they are — 4 sites in `run.py`, all use `while True: fb = recv(); if None: break`).

- **[`steer()` return type change]** → 8 call sites in `session_pool.py` and 1 in `session_controller.py` currently use `run.steer(msg)` in boolean context. Truthy `str` behaves identically to `True`. Mitigation: grep all call sites and verify none depend on `is True` or `is False` checks.

- **[`ACPEventConverter` refactor]** → Removing `_current_message_id` affects 7 yield sites in `event_converter.py`. Each needs to read `event.message_id` instead. Mitigation: all 7 sites are in the same file, changes are mechanical.

- **[External ACP agent message_id preservation]** → Some external agents may not send `message_id` on chunks (it's optional in v1). Mitigation: `_finalize_current_message()` falls back to `str(uuid4())` when incoming `message_id` is `None` or empty.

- **[`Feedback` field additions]** → 2 construction sites (`run.py:925`, `run.py:964`) need to populate new fields. Mitigation: new fields have defaults, so construction without explicit values still works — `message_id` auto-generates, `content_blocks` defaults to `None`, `mode` derives from `is_steer`.

- **[Race between revoke and delivery]** → If `revoke()` is called at the exact moment `recv()` dequeues the feedback, the feedback may already be consumed. Mitigation: `_delivered` set is checked in `revoke()` — if already delivered, return `False` (matching RFD #1261's `already_delivered` error). The race window is single-threaded (all operations are synchronous within the same event loop thread), so it's actually a non-issue in practice.

- **[Multi-message turns with external ACP agents]** → If an external ACP agent sends multiple `AgentMessageChunk` with different `message_id` values without a role switch, the accumulator must detect the `message_id` change and finalize the previous message. Mitigation: `ACPMessageAccumulator.process()` compares incoming `message_id` with `_current_message_id` and triggers `_finalize_current_message()` on change.

- **[`receive_request` return type change]** → Return type changes from `RunHandle | None` to `str | None`. Existing callers using `if receive_request(...):` still work (truthy str). Mitigation: grep all call sites and verify none use `isinstance(result, RunHandle)` or access `RunHandle`-specific attributes on the return value.

- **[Initial prompt via followup]** → `_idle_loop()` and `_drain_events()` currently read only `fb.content` (string). Must also pass `fb.content_blocks` through. `_message_queue` type changes from `list[str]` to `list[str | list[Any]]`. Mitigation: `_execute_turn()` already receives prompts as a list — type widening is backward compatible.

- **[AG-UI and OpenAI API servers]** → These servers also consume `PartStartEvent`/`PartDeltaEvent` and may have independent `message_id` generation. Mitigation: Task 8.7 audits these servers for independent generation and updates if found.
