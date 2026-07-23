## ADDED Requirements

### Requirement: UserMessageInsertedEvent SHALL be journaled for steer/followup messages

`SessionController._emit_user_message_inserted()` SHALL route `UserMessageInsertedEvent` through `ProtocolChannel.publish()` when a `ProtocolChannel` is available (i.e., during an active run). For initial REST messages where no `ProtocolChannel` exists yet, the event SHALL be published directly to `EventBus.publish()` (existing behavior). The `ProtocolChannel.publish()` path journals the event via `Journal.append()` before publishing to the `EventBus`, ensuring the event is persisted for crash recovery replay.

- For steer messages (sent during an active turn via `RunHandle.steer()`), the event SHALL go through `ProtocolChannel.publish()`
- For followup messages (queued for the next turn via `RunHandle.followup()`), the event SHALL go through `ProtocolChannel.publish()`
- For initial REST messages (sent when the session is idle, before `_start_run_handle()`), the event SHALL go through `EventBus.publish()` directly (no `ProtocolChannel` exists)
- For `background_task` and `internal` sources, the event SHALL continue to be published directly to `EventBus.publish()` (these sources represent internal/programmatic operations, not user-initiated protocol messages, and are out of scope for this change)
- The `ProtocolChannel` SHALL use append semantics for `UserMessageInsertedEvent` (each user message is a distinct event, not an upsert)

#### Scenario: Steer message is journaled

- **WHEN** a steer message is sent during an active turn
- **THEN** `UserMessageInsertedEvent` is published through `ProtocolChannel.publish()`
- **AND** the event is appended to the Journal
- **AND** the event is published to the EventBus
- **AND** on crash recovery, the event is replayed from the Journal

#### Scenario: Followup message is journaled

- **WHEN** a followup message is queued for the next turn during an active run
- **THEN** `UserMessageInsertedEvent` is published through `ProtocolChannel.publish()`
- **AND** the event is appended to the Journal

#### Scenario: Initial REST message is not journaled

- **WHEN** an initial REST message is sent when the session is idle
- **THEN** `UserMessageInsertedEvent` is published directly to `EventBus.publish()`
- **AND** the event is NOT journaled (no `ProtocolChannel` exists yet)
- **AND** this is acceptable because the run has not started — there is no journal to write to

### Requirement: ProtocolChannel SHALL deduplicate UserMessageInsertedEvent during replay

`ProtocolChannel.publish()` SHALL skip EventBus publication of `UserMessageInsertedEvent` when `_replaying=True`. During crash recovery, journaled events are replayed through `ProtocolChannel.publish()` with `_replaying=True`. If `UserMessageInsertedEvent` was journaled, it would be replayed AND fresh-published by `_route_message()`, causing duplicates. The deduplication guard prevents this.

- When `_replaying=True` and the event is `UserMessageInsertedEvent`, `ProtocolChannel.publish()` SHALL skip the EventBus publish step
- When `_replaying=False`, `ProtocolChannel.publish()` SHALL publish to the EventBus as normal
- The journaling step SHALL be skipped for all event types when `_replaying=True` (existing behavior)

#### Scenario: Replay skips EventBus publish for UserMessageInsertedEvent

- **WHEN** `UserMessageInsertedEvent` is replayed from the Journal during crash recovery
- **THEN** `ProtocolChannel.publish()` is called with `_replaying=True`
- **AND** the event is NOT published to the EventBus
- **AND** no duplicate event reaches SSE subscribers

#### Scenario: Normal publish includes EventBus for UserMessageInsertedEvent

- **WHEN** `UserMessageInsertedEvent` is published during a live run (not replay)
- **THEN** `ProtocolChannel.publish()` is called with `_replaying=False`
- **AND** the event is journaled via `Journal.append()`
- **AND** the event is published to the EventBus

#### Scenario: UserMessageInsertedEvent lost when crash occurs between journal and delivery (accepted edge case)

- **WHEN** `UserMessageInsertedEvent` is journaled via `Journal.append()` (succeeds)
- **AND** the server crashes before `EventBus.publish()` runs
- **THEN** on recovery, the event is replayed through `ProtocolChannel.publish()` with `_replaying=True`
- **AND** the deduplication guard skips the EventBus publish
- **AND** the TUI does NOT display the user message
- **NOTE** This is an accepted edge case — the window is microseconds (between `journal.append()` and `event_bus.publish()`). A future improvement could narrow the guard to only skip when the event was actually delivered.
