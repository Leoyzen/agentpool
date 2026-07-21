## ADDED Requirements

### Requirement: UserMessageInsertedEvent follows EventBus routing rules

`UserMessageInsertedEvent` SHALL be published to `EventBus` via `event_bus.publish(session_id, event)`, if EventBus is available. It SHALL NOT be routed through `run_ctx.event_queue` (which is banned as an event channel). It SHALL NOT be wrapped in `SubAgentEvent` by business layer code. Protocol layer handlers (`ACPEventConverter`, `EventProcessor`) SHALL consume the event via `ProtocolEventConsumerMixin` EventBus subscription.

`UserMessageInsertedEvent` SHALL bypass `CommChannel` — it SHALL NOT be journaled or replayed. This matches the behavior of `StreamEventEmitter._emit()`.

When no EventBus is available (standalone `agent.run()` without a protocol server), publication SHALL be silently skipped. All spec language uses "SHALL publish ... if EventBus is available" to reflect this.

#### Scenario: UserMessageInsertedEvent published to EventBus
- **WHEN** `_route_message()` publishes `UserMessageInsertedEvent` and EventBus is available
- **THEN** the event is published via `event_bus.publish(session_id, event)`
- **AND** the event is NOT put into `run_ctx.event_queue`
- **AND** the event is NOT journaled via `CommChannel.publish()`

#### Scenario: Standalone execution without EventBus
- **WHEN** `_route_message()` is called and no EventBus is available (standalone `agent.run()`)
- **THEN** no `UserMessageInsertedEvent` is published
- **AND** the message is still routed normally (no functional impact)

#### Scenario: Protocol handler receives UserMessageInsertedEvent via EventBus subscription
- **WHEN** a protocol handler subscribes to `EventBus` with `scope="session"`
- **AND** `UserMessageInsertedEvent` is published for that session
- **THEN** the protocol handler's `_handle_event()` receives the event
- **AND** the handler converts it to the protocol-specific user message format

#### Scenario: UserMessageInsertedEvent not replayed on crash recovery
- **WHEN** the system recovers from a crash
- **AND** the journal is replayed
- **THEN** `UserMessageInsertedEvent` entries SHALL NOT be replayed (they were never journaled)
- **AND** no duplicate user messages appear in the frontend after recovery
