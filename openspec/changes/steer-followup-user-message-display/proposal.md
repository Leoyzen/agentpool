## Why

When steer/followup messages are injected into an active agent session — whether from a user sending a second prompt while the session is busy, or from a background task completing and steering the model — the frontend (ACP client like Zed, or OpenCode TUI) has no unified mechanism to display these injected messages as user messages in the conversation transcript. Current behavior is ad-hoc: ACP's `handle_prompt()` emits `UserMessageChunk` for every `session/prompt` call (including when busy), and OpenCode's `message_routes.py` creates a `UserMessage` before routing — but internal steer paths (background tasks, programmatic API) publish no user message event at all, leaving the user with no visibility into what was injected.

This change establishes a unified `UserMessageInsertedEvent` that flows through the `EventBus`, allowing all steer/followup entry points to trigger user message display across both ACP and OpenCode protocols.

## What Changes

- Add `UserMessageInsertedEvent` to the `RichAgentStreamEvent` union — a typed event carrying the inserted message content, delivery type (`steer` / `followup` / `initial`), `message_id`, and optional `source` metadata
- Publish `UserMessageInsertedEvent` from `SessionController._route_message()` for all routing paths (idle, steer, followup) — the single choke point for message routing
- Publish `UserMessageInsertedEvent` from `steer_from_background_task()` — covers internal steer from background task completion
- Handle `UserMessageInsertedEvent` in the OpenCode `EventProcessor` — create a `UserMessage` and broadcast `MessageUpdatedEvent` + `PartUpdatedEvent` via SSE, matching the existing `message_routes.py` pattern
- Handle `UserMessageInsertedEvent` in the ACP `ACPEventConverter` — emit `UserMessageChunk` (v1) or `UserMessage` (v2) via `session/update` notification to the client
- Add `UserMessage` Pydantic model to `src/acp/schema/session_updates.py` — the v2 whole-message upsert variant (currently missing from the Python schema)
- Add deduplication by `message_id` in both protocol handlers — protocol handlers that already emit user messages for the initial prompt (ACP `_emit_user_message_chunks()`, OpenCode `message_routes.py`) skip the EventBus-derived event to avoid double display
- Add `_meta.delivery` field support to ACP `handle_prompt()` — allows ACP clients to specify `steer` vs `followup` priority via `_meta` (ACP v1 `PromptRequest` has no native `delivery` field)

## Capabilities

### New Capabilities

- `user-message-insertion-event`: A unified EventBus event (`UserMessageInsertedEvent`) that carries inserted user message content through the event stream, enabling all steer/followup entry points to trigger user message display in protocol frontends

### Modified Capabilities

- `steer-followup-api`: `RunHandle.steer()` and `followup()` now publish `UserMessageInsertedEvent` via `asyncio.create_task()` (fire-and-forget). `SessionController._route_message()` publishes the event for all routing paths. `steer_from_background_task()` also publishes the event. `_consume_run()` publishes for followup-from-queue messages.
- `unified-event-routing`: `UserMessageInsertedEvent` is added to the `RichAgentStreamEvent` union and follows the existing `event_bus.publish()` routing pattern (not `event_queue`, which is banned by this spec)
- `acp-server`: ACP `handle_prompt()` reads `_meta.delivery` for steer/followup priority. ACP `ACPEventConverter` handles `UserMessageInsertedEvent` → `UserMessageChunk` (v1) / `UserMessage` (v2). `UserMessage` Pydantic model added to schema.

## Impact

- **Event system** (`src/agentpool/agents/events/events.py`): New `UserMessageInsertedEvent` dataclass added to the `RichAgentStreamEvent` union (21st → 22nd type). All `match event:` dispatch sites need audit for exhaustive handling.
- **Session controller** (`src/agentpool/orchestrator/session_controller_runs.py`, `session_controller.py`): `_route_message()` and `steer_from_background_task()` publish the new event. `_consume_run()` publishes for followup-from-queue. `RunHandle.steer()` and `followup()` gain `emit_user_message` parameter (default `True` for steer, `False` for followup).
- **ACP server** (`src/agentpool_server/acp_server/handler.py`, `event_converter.py`): `handle_prompt()` reads `_meta.delivery`. `ACPEventConverter` gains a new `case UserMessageInsertedEvent` that emits `UserMessageChunk` / `UserMessage`. Dedup via `message_id` set.
- **ACP schema** (`src/acp/schema/session_updates.py`): New `UserMessage` model (v2 whole-message upsert) added to `SessionUpdate` union. `send_user_message()` in `notifications.py` optionally emits `UserMessage` for v2.
- **OpenCode server** (`src/agentpool_server/opencode_server/event_processor.py`, `routes/message_routes.py`): `EventProcessor` gains a new `case UserMessageInsertedEvent` that creates `UserMessage` and broadcasts SSE events. `message_routes.py` dedup via `message_id` set.
- **Downstream consumers**: All `match event:` sites (27 sites across 20 files) need audit — most have `case _:` catch-all and are unaffected.
- **No breaking changes**: New event type, new optional parameters with defaults, dedup prevents double display. Existing callers of `steer()`/`followup()` are unaffected.
- **PR #219 interaction**: `SystemNotificationEvent` (PR #219) and `UserMessageInsertedEvent` (this change) serve complementary purposes. `SystemNotificationEvent` renders as a system-level notification (`ToolPart(tool="system")`); `UserMessageInsertedEvent` renders as an actual user message (`role="user"`). Both can coexist — `SystemNotificationEvent` for lifecycle/background task notifications, `UserMessageInsertedEvent` for user-authored content display.
