## Context

AgentPool's steer/followup mechanism allows messages to be injected into an active agent session — `steer()` injects mid-turn (priority `"asap"`, drained at `before_model_request`), while `followup()` queues for the next turn (priority `"when_idle"`, drained at `after_node_run` or picked up by `_consume_run()`).

Currently, user message display for steer/followup is ad-hoc per protocol handler:
- **ACP**: `handle_prompt()` calls `_emit_user_message_chunks()` for every `session/prompt` request, including when the session is busy. This works for protocol-initiated messages but not for internal steer (e.g., `steer_from_background_task()`).
- **OpenCode**: `message_routes.py` creates a `UserMessage` and broadcasts `MessageUpdatedEvent` before routing. Same limitation — internal steer paths bypass the REST handler.

There is no unified EventBus event for user message insertion. Internal steer/followup paths (background tasks, programmatic API) have no mechanism to trigger user message display in the frontend.

ACP v1 supports `user_message_chunk` (streaming single content blocks) but not `user_message` (whole-message upsert, v2-only). The Python schema at `src/acp/schema/session_updates.py` only has `UserMessageChunk`.

`SystemNotificationEvent` (RFC-0056 / PR #219) is complementary future work — it renders as `ToolPart(tool="system")`. This change is distinct: `UserMessageInsertedEvent` renders as `role="user"` messages. RFC-0056 is **not** a dependency of this change.

## Goals / Non-Goals

**Goals:**
- All steer/followup entry points (protocol handlers AND internal paths) trigger user message display in frontends
- Unified mechanism via EventBus — single event type, protocol-specific mapping
- ACP v1 support via `UserMessageChunk`; ACP v2 support via `UserMessage` upsert (schema addition)
- OpenCode support via `UserMessage` + `MessageUpdatedEvent` broadcast
- Deduplication to prevent double display when both protocol handler and EventBus event fire
- No breaking changes to existing `steer()`/`followup()` callers

**Non-Goals:**
- Replacing `SystemNotificationEvent` (RFC-0056 / PR #219) — both coexist for different purposes. RFC-0056 is not a dependency.
- Modifying ACP v1 protocol schema — only the Python schema gets `UserMessage` (v2 type) added
- Implementing `reply_to` / `parent_message_id` correlation — ACP has no such field; correlation is by timing
- OS-level desktop notifications — `ToastInfo` remains the chrome-level channel
- Journal/CommChannel persistence — `UserMessageInsertedEvent` bypasses CommChannel (point-in-time signal, never journaled)
- Modifying `SystemNotificationEvent` or its rendering path
- Team mode tool modifications — `source="background_task"` covers the primary internal use case; team-specific `source="team"` can be added in a follow-up

## Decisions

### Decision 1: New `UserMessageInsertedEvent` dataclass (not reusing `SystemNotificationEvent`)

**Choice**: Add a new `UserMessageInsertedEvent` to `RichAgentStreamEvent`.

**Rationale**: `SystemNotificationEvent` (RFC-0056 / PR #219) renders as `ToolPart(tool="system")` — a system-level notification. User messages need to render as `role="user"` in the conversation transcript. Different rendering target requires different event type. Type-safe `match` dispatch in `EventProcessor` and `ACPEventConverter` needs a distinct type. Additionally, `SystemNotificationEvent` lacks `message_id` and `delivery` fields needed for ACP correlation and dedup.

**Alternatives considered**:
- Reuse `SystemNotificationEvent` with `source="steer"` — rejected because the rendering target is fundamentally different (user message vs system notification), and because RFC-0056 / PR #219 is not yet merged — this change must not depend on it
- Reuse `CustomEvent[UserMessagePayload]` — rejected because it loses type safety in `match` dispatch

### Decision 2: Publish from `_route_message()`, `steer_from_background_task()`, and `_consume_run()`

**Choice**: `SessionController._route_message()` publishes `UserMessageInsertedEvent` for all routing paths (idle, steer, followup), if EventBus is available. `steer_from_background_task()` also publishes (synchronously, using `asyncio.create_task()`). `_consume_run()` publishes for followup-from-queue messages, if EventBus is available. `RunHandle.steer()`/`followup()` emit via `asyncio.create_task()` as a secondary mechanism.

**Rationale**: `_route_message()` is the single choke point for all message routing. Publishing here ensures every protocol-initiated entry point triggers the event. `steer_from_background_task()` is a separate internal path that bypasses `_route_message()`. `_consume_run()` covers followup-from-queue (the gap identified by Oracle review) — when a followup is picked up from `prompt_queue`, `_consume_run()` → `_create_per_prompt_handle()` creates a new `RunHandle` directly WITHOUT calling `_route_message()`.

**Alternatives considered**:
- Publish from `RunHandle.steer()`/`followup()` only — rejected because `_consume_run()` picks up followup messages from `prompt_queue` without going through `steer()`/`followup()`
- Publish from protocol handlers only — rejected because internal paths bypass protocol handlers

### Decision 3: `steer()`/`followup()` emission via `asyncio.create_task()` (fire-and-forget)

**Choice**: `RunHandle.steer()` and `followup()` schedule event emission via `asyncio.get_running_loop().create_task()`. Same pattern as RFC-0056's proposed `SystemNotificationEvent`.

**Rationale**: `steer()`/`followup()` are synchronous (constrained by RFC-0037) but always called from async contexts. `event_bus.publish()` is async. Fire-and-forget `create_task()` bridges the sync/async gap. The emission is best-effort — one event loop tick delay is acceptable for display notifications. All `create_task()` call sites use `try/except RuntimeError` to handle no-running-loop scenarios (emission silently skipped).

**Alternatives considered**:
- Make `steer()`/`followup()` async — rejected as breaking change (RFC-0037 constrains them to sync)
- Sync emission path — rejected because `EventBus.publish()` is inherently async

### Decision 4: ACP v1 uses `UserMessageChunk`; v2 uses `UserMessage` upsert (with `protocol_version` in converter)

**Choice**: `ACPEventConverter` emits `UserMessageChunk` for v1 clients and `UserMessage` (whole-message upsert) for v2 clients. Add `UserMessage` Pydantic model to `session_updates.py`. `ACPEventConverter.__init__()` is modified to accept `protocol_version: int = 1`, passed from the ACP agent (which stores it at `acp_agent.py:380`).

**Rationale**: v1 only has `user_message_chunk` (streaming, single `ContentBlock`). v2 adds `user_message` (full upsert with patch semantics). For steer/followup, the message content is known in full, so `UserMessage` (v2) is the natural fit. For v1 compatibility, `UserMessageChunk` suffices — the chunk contains the complete text in a single `TextContentBlock`. `protocol_version` is passed explicitly to the converter — no global state needed.

**Alternatives considered**:
- v1-only via `UserMessageChunk` — rejected because v2 `UserMessage` is the proper mechanism for whole-message insertion
- v2-only — rejected because Zed and other current clients implement v1

### Decision 5: Deduplication via shared `message_id` set per session

**Choice**: A shared `message_id` dedup set is accessible by BOTH the protocol handler emission path AND the `ACPEventConverter`/`EventProcessor` (EventBus path). Protocol handlers generate the ID first, register it in the dedup set, emit to client, then pass the ID to `send_message()` → `_route_message()`. The EventBus event carries the same ID and the converter checks the dedup set and skips.

**Rationale**: ACP `handle_prompt()` already emits `UserMessageChunk` for the initial prompt. OpenCode `message_routes.py` already creates `UserMessage`. If `_route_message()` also publishes `UserMessageInsertedEvent`, the same message would be displayed twice. Dedup by `message_id` prevents this.

**Current problem**: `_emit_user_message_chunks()` generates `message_id` internally via `build_user_message_chunks()` at `event_converter.py:294` and sends directly to client via `self.client.session_update(notification)`, bypassing `ACPEventConverter` entirely. This means the converter has no way to know which messages were already emitted.

**Fix**: Modify `_emit_user_message_chunks()` to:
1. Generate `message_id` FIRST (before emitting chunks).
2. Register `message_id` in the shared dedup set.
3. Pass `message_id` through `send_message(message_id=mid)` → `_route_message(message_id=mid)`.

The dedup set must be accessible by BOTH `_emit_user_message_chunks()` (protocol handler path) AND `ACPEventConverter` (EventBus path).

**Implementation**: The dedup set lives as a per-session `dict[str, set[str]]` on `SessionController` (keyed by `session_id`, value is `set[str]` of displayed `message_id`s). It is passed to `ACPEventConverter` and `EventProcessor` constructors as a `displayed_message_ids: set[str]` parameter. Entries are removed on session close. This avoids global state and keeps the set scoped to the session lifecycle.

**Alternatives considered**:
- Remove ad-hoc emission from protocol handlers — rejected as higher risk, more invasive change
- Use a flag on `_route_message()` to suppress event for protocol-initiated messages — rejected as fragile coupling

### Decision 6: `_meta.delivery` extraction at `acp_agent.py:prompt()`

**Choice**: `handle_prompt()` does NOT receive `_meta` directly. `_meta` is extracted at `acp_agent.py:prompt()` (line ~698) for trace context but NOT forwarded to `handle_prompt()`. The fix: extract `delivery` from `_meta` in `acp_agent.py:prompt()` and pass it as a `delivery` parameter through `handle_prompt()` → `send_message()` → `_route_message()`. Values: `"steer"` → priority `"asap"`, `"followup"` → priority `"when_idle"`, absent → `"when_idle"` (default).

**Rationale**: ACP v1 `PromptRequest` has no native `delivery` field. The `_meta` field is the standard extension mechanism. Extracting at `acp_agent.py:prompt()` (where `_meta` is already available) and passing it through the call chain is the cleanest approach.

**Alternatives considered**:
- Always default to `"when_idle"` — rejected because steer is a primary use case
- Propose ACP protocol extension for `delivery` field — out of scope, future work

### Decision 7: `source` field mapping

**Choice**: The `source` field on `UserMessageInsertedEvent` is populated based on the call site:

| Call site | `source` value |
|---|---|
| `_route_message()` from protocol handler | `"protocol"` |
| `steer_from_background_task()` | `"background_task"` |
| `steer()` / `followup()` direct call | `"internal"` |
| `_consume_run()` followup-from-queue | `"internal"` |

**Rationale**: Distinguishing the source allows protocol handlers and frontends to make rendering decisions based on where the message originated. Internal paths (`"background_task"`, `"internal"`) are always displayed since they have no prior protocol emission. Protocol paths (`"protocol"`) may be deduplicated.

### Decision 8: Interaction with RFC-0056 (SystemNotificationEvent)

**Choice**: When `emit_user_message=True`, `SystemNotificationEvent` (if implemented in the future via RFC-0056) should default to suppressed for the same message to avoid redundant display. This is a decision, not an open question.

**Rationale**: Both event types serving the same steer message would result in duplicate display (one as `role="user"`, one as `ToolPart(tool="system")`). When `UserMessageInsertedEvent` is emitted, the `SystemNotificationEvent` for the same content is redundant. Future implementations of RFC-0056 should check whether a `UserMessageInsertedEvent` was already emitted for the same `message_id` and skip the notification.

### Decision 9: `EventBus is None` guard for standalone execution

**Choice**: When no EventBus is available (standalone `agent.run()`), publication is silently skipped. All spec language uses "SHALL publish ... if EventBus is available" to reflect this.

**Rationale**: Standalone execution (`agent.run()` without a protocol server) has no EventBus. Forcing EventBus availability would break standalone usage. Display notifications are only meaningful in protocol server contexts where frontends are connected.

## Risks / Trade-offs

- **[Double display if dedup fails]** → shared `message_id` dedup set per session; protocol handlers generate the ID first and register before emitting, then pass the same ID to `send_message()` → `_route_message()`
- **[Event ordering: EventBus event arrives before protocol handler emission]** → EventBus event is published from `_route_message()` which is called AFTER protocol handler's ad-hoc emission, so ordering is correct (ad-hoc first, EventBus event second, dedup skips)
- **[`asyncio.create_task()` emission lost on shutdown]** → acceptable for display notifications; the message is still processed by the agent
- **[No running event loop]** → `try/except RuntimeError` at all `create_task()` sites; emission silently skipped in non-async contexts
- **[ACP v1 clients may have UI ordering assumptions]** → `UserMessageChunk` for steer arrives mid-turn; clients that assume user messages only appear between turns may have rendering issues. Mitigation: document this behavior
- **[Exhaustive match sites need audit]** → 27 `match event:` sites across 20 files; most have `case _:` catch-all. Audit task covers all sites
- **[Orphan traces from `create_task()` emission]** → emission helper wraps in `logfire.span("event.user_message_inserted.emit")` to prevent orphan traces
- **[Exception in emission coroutine crashes task]** → emission helper catches all exceptions with `try/except Exception` and logs warning
- **[No `reply_to` correlation]** → ACP has no such field. Clients correlate by timing: the steer `UserMessageChunk` is followed by a new `AgentMessageChunk` (new `message_id`). Document this as the correlation mechanism
- **[`EventBus is None` for standalone execution]** → publication silently skipped; all spec language uses "if EventBus is available"

## Technical Design

### Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Entry Points                                                     │
│  ┌──────────────┐  ┌──────────────────┐  ┌────────────────────┐ │
│  │ ACP          │  │ OpenCode         │  │ Internal           │ │
│  │ handle_prompt│  │ message_routes   │  │ steer_from_bg_task │ │
│  │ (delivery    │  │ (delivery field) │  │ (no protocol,      │ │
│  │  from _meta) │  │                  │  │  SYNC method)      │ │
│  └──────┬───────┘  └────────┬─────────┘  └─────────┬──────────┘ │
│         │                   │                      │            │
│         ▼                   ▼                      │            │
│  ┌──────────────────────────────────────┐          │            │
│  │ session_pool.send_message()          │          │            │
│  │ → session_pool_messaging.py          │          │            │
│  │ → SessionController._route_message() │          │            │
│  │ ┌─ publish UserMessageInsertedEvent ─┼──────────┘            │
│  │ │  (if EventBus available)           │                       │
│  │ └────────────────────────────────────┘                       │
│  └──────────────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ _consume_run() / _create_per_prompt  │                       │
│  │  _handle() — followup-from-queue     │                       │
│  │  publish UserMessageInsertedEvent    │                       │
│  │  (source="internal", delivery=       │                       │
│  │   "followup", if EventBus available) │                       │
│  └──────────────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ EventBus                             │                       │
│  │ event_bus.publish(session_id, event) │                       │
│  └──────────┬───────────────────────────┘                       │
│             │                                                   │
│             ▼                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Protocol Server Consumers (ProtocolEventConsumerMixin)     │ │
│  │                                                            │ │
│  │  ┌──────────────────────┐  ┌─────────────────────────────┐│ │
│  │  │ ACP ACPEventConverter│  │ OpenCode EventProcessor     ││ │
│  │  │ (protocol_version)   │  │                             ││ │
│  │  │ case UserMessageIns: │  │ case UserMessageInserted:   ││ │
│  │  │  check dedup set     │  │  check dedup set            ││ │
│  │  │  v1→UserMessageChunk │  │  → create UserMessage       ││ │
│  │  │  v2→UserMessage      │  │  → broadcast                ││ │
│  │  │  (dedup by msg_id)   │  │    MessageUpdatedEvent      ││ │
│  │  └──────────────────────┘  └─────────────────────────────┘│ │
│  └────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Key Components

#### `UserMessageInsertedEvent` (new)

```python
@dataclass(frozen=True)
class UserMessageInsertedEvent:
    session_id: str = ""
    message_id: str = ""                      # unique per insertion, for dedup
    content: str | list[Any] = ""             # str or list[Any] for multi-modal
    delivery: Literal["initial", "steer", "followup"] = "initial"
    source: Literal["protocol", "background_task", "internal"] = "protocol"
    timestamp: float = field(default_factory=time.time)
```

Added to `RichAgentStreamEvent` PEP 695 `type` statement.

The `content` field is `str | list[Any]` to support multi-modal prompts (text + images, structured content blocks, etc.). `_route_message()` accepts `content: str | list[Any]`, and `steer()` accepts `message: str | list[Any]`.

#### `source` Field Mapping

| Call site | `source` value |
|---|---|
| `_route_message()` from protocol handler | `"protocol"` |
| `steer_from_background_task()` | `"background_task"` |
| `steer()` / `followup()` direct call | `"internal"` |
| `_consume_run()` followup-from-queue | `"internal"` |

#### `SessionController._route_message()` (modified)

```python
async def _route_message(
    self, session_id, content: str | list[Any],
    priority="when_idle", message_id=None, delivery=None,
):
    message_id = message_id or str(uuid.uuid4())
    if delivery is None:
        delivery = "initial" if session_idle else ("steer" if priority == "asap" else "followup")

    # Publish event BEFORE routing, if EventBus is available
    if self._event_bus:
        event = UserMessageInsertedEvent(
            session_id=session_id,
            message_id=message_id,
            content=content,
            delivery=delivery,
            source="protocol",
        )
        await self._event_bus.publish(session_id, event)

    # ... existing routing logic ...
```

#### ACP `_meta` extraction (modified — `acp_agent.py:prompt()` ~line 698)

`handle_prompt()` does NOT receive `_meta` directly. `_meta` is extracted at `acp_agent.py:698` for trace context but NOT forwarded to `handle_prompt()`. The fix: extract `delivery` from `_meta` in `acp_agent.py:prompt()` and pass it through the call chain.

```python
# In acp_agent.py:prompt() around line 698
# _meta is already extracted for trace context at this point
delivery = "steer" if (meta and meta.get("delivery") == "steer") else "when_idle"
# Pass delivery to handle_prompt → send_message → _route_message
await self.handle_prompt(session_id, prompt, delivery=delivery)
```

#### `steer_from_background_task()` (modified — SYNC, not async)

`steer_from_background_task()` is on `SessionController` at `session_controller.py:305`, uses `_active_steer_callback`, and is **synchronous**. It must NOT be made `async def`. Instead, use `asyncio.create_task()` for the EventBus publish:

```python
def steer_from_background_task(self, session_id, content: str | list[Any]):
    # ... existing steer logic via _active_steer_callback ...
    if emit_user_message and self._event_bus:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._emit_user_message_inserted(
                session_id=session_id,
                content=content,
                delivery="steer",
                source="background_task",
            ))
        except RuntimeError:
            pass  # no running loop — emission silently skipped
```

> **Note**: If no event loop is running (non-async context), emission is silently skipped.

#### `_consume_run()` followup-from-queue (modified)

`_consume_run()` → `_create_per_prompt_handle()` (line 150) creates `RunHandle` directly WITHOUT calling `_route_message()`. This means followup messages picked up from `prompt_queue` bypass the event publication entirely. Fix: add event publication in `_consume_run()`:

```python
# In _consume_run() after picking up from prompt_queue
if self._event_bus:
    event = UserMessageInsertedEvent(
        session_id=session_id,
        message_id=str(uuid.uuid4()),
        content=content,
        delivery="followup",
        source="internal",
    )
    await self._event_bus.publish(session_id, event)
```

#### Emission helper (new — with `logfire.span` and exception handling)

All `asyncio.create_task()` emission calls use this shared helper. It wraps the emission in a `logfire.span` to prevent orphan traces, and catches all exceptions to prevent task failures from propagating.

**Ownership**: The helper is defined on BOTH `RunHandle` (for `steer()`/`followup()` direct calls) and `SessionController` (for `steer_from_background_task()` and `_consume_run()`). Both classes have `self._event_bus` access. The helper is a private async method on each class — not shared via mixin, to avoid coupling between the two class hierarchies. The implementation is identical on both.

```python
async def _emit_user_message_inserted(self, session_id, content, delivery, source):
    with logfire.span("event.user_message_inserted.emit", session_id=session_id):
        try:
            event = UserMessageInsertedEvent(
                session_id=session_id,
                message_id=str(uuid.uuid4()),
                content=content,
                delivery=delivery,
                source=source,
            )
            if self._event_bus:
                await self._event_bus.publish(session_id, event)
        except Exception:
            logger.warning("Failed to emit UserMessageInsertedEvent", exc_info=True)
```

All `asyncio.create_task()` call sites must have try/except for `RuntimeError` (no running loop):

```python
try:
    loop = asyncio.get_running_loop()
    loop.create_task(self._emit_user_message_inserted(...))
except RuntimeError:
    pass  # no running loop — emission silently skipped
```

#### `RunHandle.steer()` / `followup()` (modified)

```python
def steer(self, content: str | list[Any], emit_user_message: bool = True) -> None:
    # ... existing steer logic ...
    if emit_user_message:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._emit_user_message_inserted(content, "steer", "internal")
            )
        except RuntimeError:
            pass  # no running loop — emission silently skipped

def followup(self, content: str | list[Any], emit_user_message: bool = False) -> None:
    # ... existing followup logic ...
    if emit_user_message:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._emit_user_message_inserted(content, "followup", "internal")
            )
        except RuntimeError:
            pass  # no running loop — emission silently skipped
```

#### `ACPEventConverter` (modified — with `protocol_version`)

`ACPEventConverter` currently has NO `protocol_version` field. Protocol version is stored on the ACP agent (`acp_agent.py:380`). Fix: add `protocol_version: int` to the constructor, passed from the ACP agent:

```python
class ACPEventConverter:
    def __init__(self, ..., protocol_version: int = 1):
        self._protocol_version = protocol_version
        self._displayed_message_ids: set[str] = set()
        # ... other init ...

    # In the event handler:
    case UserMessageInsertedEvent(message_id=mid, content=content):
        if mid in self._displayed_message_ids:
            return  # dedup — already emitted by protocol handler
        # Convert content (str | list[Any]) to ContentBlocks
        blocks = _content_to_blocks(content)
        if self._protocol_version >= 2:
            yield SessionUpdate(
                session_update=SessionUpdateKind.UserMessage,
                message_id=mid,
                content=blocks,
            )
        else:
            for block in blocks:
                yield SessionUpdate(
                    session_update=SessionUpdateKind.UserMessageChunk,
                    message_id=mid,
                    content=block,
                )
        self._displayed_message_ids.add(mid)
```

When `content` is `list[Any]`, convert each item to the appropriate `ContentBlock` (e.g., `str` → `TextContentBlock`, dict with image → `ImageContentBlock`).

#### OpenCode `EventProcessor` (modified)

```python
case UserMessageInsertedEvent(message_id=mid, content=content, timestamp=ts):
    if mid in self._displayed_message_ids:
        return  # dedup
    # Convert content (str | list[Any]) to Parts
    parts = _content_to_parts(content)
    user_msg = UserMessage(
        id=mid,
        session_id=ctx.session_id,
        role="user",
        content=parts,
        created_at=ts,
    )
    yield MessageUpdatedEvent(message=user_msg)
    yield PartUpdatedEvent(...)
    self._displayed_message_ids.add(mid)
```

When `content` is `list[Any]`, convert each item to the appropriate `Part` (e.g., `str` → `TextPart`, dict with image → `ImagePart`).

#### ACP `UserMessage` schema (new)

```python
class UserMessage(BaseModel):
    session_update: Literal["user_message"] = "user_message"
    message_id: str
    content: list[ContentBlock] | None = None
    meta: dict[str, Any] | None = None
```

Added to `SessionUpdate` union in `session_updates.py`.

### Data Model

```python
@dataclass(frozen=True)
class UserMessageInsertedEvent:
    session_id: str = ""
    message_id: str = ""
    content: str | list[Any] = ""
    delivery: Literal["initial", "steer", "followup"] = "initial"
    source: Literal["protocol", "background_task", "internal"] = "protocol"
    timestamp: float = field(default_factory=time.time)
```

### Deduplication Strategy

The dedup mechanism uses a **shared `message_id` dedup set** accessible by BOTH the protocol handler emission path AND the `ACPEventConverter`/`EventProcessor` (EventBus path).

**Current problem**: `_emit_user_message_chunks()` generates `message_id` internally via `build_user_message_chunks()` at `event_converter.py:294` and sends directly to client via `self.client.session_update(notification)`, bypassing `ACPEventConverter` entirely. This means the converter has no way to know which messages were already emitted.

**Fix**: Modify `_emit_user_message_chunks()` to:
1. Generate `message_id` FIRST (before emitting chunks).
2. Register `message_id` in the shared dedup set.
3. Pass `message_id` through `send_message(message_id=mid)` → `_route_message(message_id=mid)`.

The dedup set must be accessible by BOTH `_emit_user_message_chunks()` (protocol handler path) AND `ACPEventConverter` (EventBus path). Implementation: per-session `dict[str, set[str]]` on `SessionController`, passed to converters as `displayed_message_ids: set[str]`.

```
Protocol handler (ACP/OpenCode)
  │
  ├─ 1. Generate message_id = str(uuid.uuid4())
  ├─ 2. Register message_id in shared dedup set
  ├─ 3. Emit user message to frontend (UserMessageChunk / UserMessage + SSE)
  ├─ 4. Pass message_id to send_message()
  │
  ▼
send_message() → session_pool_messaging.py → _route_message()
  │
  ├─ 5. Publish UserMessageInsertedEvent(message_id=same_id)
  │     (if EventBus is available)
  │
  ▼
EventBus → Protocol Consumer (ACPEventConverter / EventProcessor)
  │
  ├─ 6. Check message_id in shared dedup set
  ├─ 7. Found → skip (dedup — already emitted by protocol handler)
  └─ 8. Not found → emit (internal path, no prior protocol emission)
```

**For ACP**: `_emit_user_message_chunks()` generates ID, registers in dedup set, emits to client, passes ID to `send_message()` → `_route_message()`. The `ACPEventConverter` checks the same dedup set.

**For OpenCode**: `message_routes.py` generates ID, registers in dedup set, creates `UserMessage`, passes ID to `route_message()`. The `EventProcessor` checks the same dedup set. **All 6+ `UserMessage` creation sites** must be wired with dedup:
- `message_routes.py:311,884`
- `session_routes.py:414,638`
- `opencode_event_bridge.py:368,638`

## Review History

| Reviewer | Date | Verdict | Key Findings |
|----------|------|---------|--------------|
| Metis (Pre-Planning) | 2026-07-21 | 7 blockers, 12 concerns | B1-B7 codebase accuracy; C1-C12 implementation concerns |
| Oracle (Architecture) | 2026-07-21 | 2 blockers, 6 concerns, 2 acceptable | Dedup mechanism broken; followup-from-queue gap |
| Momus (Plan Quality) | 2026-07-21 | Rejected (format) | Only accepts .omo/plans/*.md paths |

### Revision History

| Revision | Date | Changes |
|----------|------|---------|
| 1 | 2026-07-21 | Initial draft |
| 2 | 2026-07-21 | Post-review: removed PR #219 dependency; fixed receive_request→send_message; fixed steer_from_background_task sync; fixed _meta extraction; content: str→str\|list[Any]; fixed dedup mechanism; added protocol_version to converter; added _consume_run publication; added exception handling; added EventBus=None guard; defined source mapping; added logfire.span; updated match site count to 27; resolved OQ5 as decision |
