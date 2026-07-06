## MODIFIED Requirements

### Requirement: EventBus publishes events directly to subscriber queues without buffering
The EventBus SHALL NOT maintain any per-session coalescing buffer. The `publish()` method SHALL send each event directly to matching subscriber queues via the existing `_send()` path. The only preprocessing SHALL be dropping `PartDeltaEvent` instances where `delta` is `None`.

After the migration to `asyncio.Queue`, the `_send()` path SHALL use `put_nowait()` for all subscriber enqueue operations. The `block` overflow policy SHALL NOT be supported on the publish path — it would deadlock the run loop. Only `drop_oldest`, `drop_newest`, and `drop_subscriber` overflow policies SHALL be supported.

#### Scenario: publish uses put_nowait exclusively
- **WHEN** `EventBus.publish()` sends an event to subscriber queues
- **THEN** all enqueue operations SHALL use `put_nowait()`, never blocking `put()`

#### Scenario: PartDeltaEvent with None delta dropped
- **WHEN** `EventBus.publish()` receives a `PartDeltaEvent` with `delta=None`
- **THEN** the event SHALL be silently dropped and not enqueued to any subscriber queue

#### Scenario: No block policy supported
- **WHEN** `EventBus` is initialized with `overflow_policy="block"`
- **THEN** the initialization SHALL raise `ValueError` with a message explaining that `block` would deadlock the run loop
