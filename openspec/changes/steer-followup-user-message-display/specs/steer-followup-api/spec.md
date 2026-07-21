## ADDED Requirements

### Requirement: RunHandle.steer() and followup() accept emit_user_message parameter

`RunHandle.steer()` SHALL accept an `emit_user_message: bool = True` parameter. When `True`, the method SHALL schedule `UserMessageInsertedEvent` emission via `asyncio.get_running_loop().create_task()`. `RunHandle.followup()` SHALL accept an `emit_user_message: bool = False` parameter with the same behavior.

The emission SHALL use the existing `event_bus.publish()` pattern, if EventBus is available. The task SHALL be fire-and-forget — `steer()`/`followup()` SHALL NOT await the emission. Failure to publish SHALL log a warning but SHALL NOT raise. All `create_task()` call sites SHALL use `try/except RuntimeError` to handle no-running-loop scenarios.

When `emit_user_message=True`, `SystemNotificationEvent` (if implemented in the future via RFC-0056) should default to suppressed for the same message to avoid redundant display.

#### Scenario: steer() emits user message with default
- **WHEN** `RunHandle.steer(content, emit_user_message=True)` is called
- **THEN** `UserMessageInsertedEvent(delivery="steer", source="internal")` is scheduled via `asyncio.create_task()`
- **AND** the frontend displays a user message in the transcript

#### Scenario: steer() with emit_user_message=False
- **WHEN** `RunHandle.steer(content, emit_user_message=False)` is called
- **THEN** no `UserMessageInsertedEvent` is scheduled from `steer()`
- **AND** the event may still be published by `_route_message()` if applicable

#### Scenario: followup() with default emit_user_message=False
- **WHEN** `RunHandle.followup(content)` is called with default parameters
- **THEN** no `UserMessageInsertedEvent` is scheduled from `followup()`
- **AND** the event may still be published by `_consume_run()` for followup-from-queue

#### Scenario: followup() with emit_user_message=True
- **WHEN** `RunHandle.followup(content, emit_user_message=True)` is called
- **THEN** `UserMessageInsertedEvent(delivery="followup", source="internal")` is scheduled via `asyncio.create_task()`

#### Scenario: steer() with no running event loop
- **WHEN** `RunHandle.steer(content, emit_user_message=True)` is called outside an async context
- **THEN** the `RuntimeError` from `asyncio.get_running_loop()` is caught
- **AND** emission is silently skipped (the steer still proceeds)

### Requirement: SessionController._route_message() publishes UserMessageInsertedEvent for all paths

`SessionController._route_message()` SHALL publish `UserMessageInsertedEvent` to `EventBus` for all three routing paths: idle (initial), busy+asap (steer), and busy+when_idle (followup), if EventBus is available. The event SHALL be published before the routing action (e.g., before `_start_run_handle()`, `run.steer()`, or `prompt_queue.put_nowait()`).

The `message_id` SHALL be generated as `str(uuid.uuid4())`. If the protocol handler has already generated a `message_id` for this message, the protocol handler SHALL pass it to `send_message()` as a parameter, and `_route_message()` SHALL use it for the event. If no `message_id` is provided, `_route_message()` SHALL generate one.

#### Scenario: Protocol handler passes message_id for dedup
- **WHEN** ACP `handle_prompt()` generates `message_id="msg_abc"` and passes it to `send_message(message_id="msg_abc")`
- **AND** `_route_message()` publishes `UserMessageInsertedEvent(message_id="msg_abc")`
- **THEN** the ACP `ACPEventConverter` finds `"msg_abc"` in the shared dedup set (from `handle_prompt()`'s prior emission)
- **AND** skips the EventBus-derived emission

#### Scenario: Internal path with no message_id from protocol
- **WHEN** `steer_from_background_task()` calls `run_handle.steer(content)` directly
- **AND** `steer()` generates `message_id=str(uuid.uuid4())`
- **THEN** `UserMessageInsertedEvent` is published with the generated `message_id`
- **AND** no protocol handler has this `message_id` in its dedup set
- **AND** the frontend displays the user message

#### Scenario: Standalone execution without EventBus
- **WHEN** `_route_message()` is called and no EventBus is available
- **THEN** no `UserMessageInsertedEvent` is published
- **AND** the message is still routed normally
