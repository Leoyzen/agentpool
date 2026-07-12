## ADDED Requirements

### Requirement: EventBus uses asyncio.Queue for subscriber queues
The `EventBus` SHALL use `asyncio.Queue` with `maxsize` for subscriber queues instead of `anyio.create_memory_object_stream()`. Each subscriber SHALL have its own `asyncio.Queue` instance.

#### Scenario: Subscribe returns asyncio.Queue
- **WHEN** a caller subscribes to a session via `EventBus.subscribe()`
- **THEN** the returned object SHALL be an `asyncio.Queue` instance (or a wrapper providing `get()`, `get_nowait()`, `empty()`, `qsize()` methods)

#### Scenario: Queue has configurable max size
- **WHEN** `EventBus` is initialized with `max_queue_size=100`
- **THEN** each subscriber queue SHALL have `maxsize=100`

### Requirement: Configurable overflow policies
The `EventBus` SHALL support three overflow policies when a subscriber queue is full: `drop_oldest` (remove oldest event, enqueue new), `drop_newest` (keep queue as-is, drop new event), `drop_subscriber` (close the subscriber's queue and remove it). The policy SHALL be configurable per `EventBus` instance via `overflow_policy` parameter.

#### Scenario: drop_oldest removes oldest event
- **WHEN** a subscriber queue is full and `overflow_policy="drop_oldest"`
- **THEN** the oldest event SHALL be removed via `get_nowait()` and the new event SHALL be enqueued via `put_nowait()`

#### Scenario: drop_newest discards new event
- **WHEN** a subscriber queue is full and `overflow_policy="drop_newest"`
- **THEN** the new event SHALL be silently discarded and the queue contents SHALL remain unchanged

#### Scenario: drop_subscriber removes subscriber
- **WHEN** a subscriber queue is full and `overflow_policy="drop_subscriber"`
- **THEN** the subscriber's queue SHALL be closed, the subscriber SHALL be removed from the subscriber list, and the new event SHALL NOT be enqueued to that subscriber

### Requirement: No block policy on publish path
The `EventBus.publish()` method SHALL NOT block under any condition. The `block` overflow policy SHALL NOT be supported. All publish-path operations SHALL use non-blocking queue operations (`put_nowait()`).

#### Scenario: publish never blocks
- **WHEN** `EventBus.publish()` is called and all subscriber queues are full
- **THEN** the call SHALL return immediately without blocking, applying the configured overflow policy

### Requirement: Dead subscriber cleanup preserved
The `EventBus` SHALL continue to clean up subscribers whose queues are closed or whose consumers have exited. Dead subscriber detection SHALL use `asyncio.Queue` semantics (closed queue raises `QueueClosed` on `get()`).

#### Scenario: Dead subscriber removed after queue close
- **WHEN** a subscriber's queue is closed (consumer exited)
- **THEN** the next `publish()` call SHALL detect the closed queue and remove the subscriber from the subscriber list

### Requirement: Replay buffer behavior preserved
The replay buffer (retaining recent events per session for new subscribers) SHALL continue to work with `asyncio.Queue`. New subscribers SHALL receive replayed historical events before live events, with events enqueued via `put_nowait()`.

#### Scenario: New subscriber receives replayed events
- **WHEN** a new subscriber subscribes to a session that has events in the replay buffer
- **THEN** the subscriber's queue SHALL contain all replayed events before any new live events
