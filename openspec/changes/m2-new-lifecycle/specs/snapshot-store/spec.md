## ADDED Requirements

### Requirement: SnapshotStore is a Protocol with save, load, save_turn_result, has_turn_result, and clear methods

SnapshotStore SHALL be a `@runtime_checkable` Protocol for loop-layer state persistence. It SHALL persist full state images at Turn boundaries and provide idempotency keys via `turn_id`.

#### Scenario: Protocol conformance

- **WHEN** a class implements `save`, `load`, `save_turn_result`, `has_turn_result`, and `clear` methods with the correct signatures
- **THEN** `isinstance(instance, SnapshotStore)` SHALL return `True`

### Requirement: SnapshotStore.save() persists a full state snapshot

`save(state)` SHALL persist a full `RunState` snapshot and return the sequence number of the snapshot. Future journal entries after this sequence are "inflight" until the next snapshot. Entries before this sequence become "committed" and are eligible for compaction.

#### Scenario: Save snapshot at Turn boundary

- **WHEN** `save(run_state)` is called after a Turn completes
- **THEN** the full RunState SHALL be persisted
- **AND** the returned sequence number SHALL indicate the snapshot's journal position

### Requirement: SnapshotStore.load() returns the latest snapshot

`load()` SHALL return a tuple of `(RunState, last_journal_seq)` or `None` if no snapshot exists. This is called by `Journal.resume()` during crash recovery.

#### Scenario: Load existing snapshot

- **WHEN** `load()` is called and a snapshot exists
- **THEN** a tuple of `(RunState, int)` SHALL be returned
- **AND** the `last_journal_seq` SHALL indicate the journal position at snapshot time

#### Scenario: Load with no snapshot

- **WHEN** `load()` is called and no snapshot exists
- **THEN** `None` SHALL be returned

### Requirement: SnapshotStore.save_turn_result() saves a completed Turn's result

`save_turn_result(turn_id, result)` SHALL persist a completed Turn's result for idempotency. On crash recovery, the RunLoop uses this to skip already-completed Turns.

#### Scenario: Save Turn result

- **WHEN** `save_turn_result("turn_123", result)` is called
- **THEN** the result SHALL be persisted with the `turn_id` as the key

### Requirement: SnapshotStore.has_turn_result() checks if a Turn was completed

`has_turn_result(turn_id)` SHALL return `True` if the Turn was already completed and its result was saved. This is used for crash recovery idempotency.

#### Scenario: Check completed Turn

- **WHEN** `has_turn_result("turn_123")` is called and the Turn was previously completed
- **THEN** `True` SHALL be returned

#### Scenario: Check uncompleted Turn

- **WHEN** `has_turn_result("turn_456")` is called and the Turn was never completed
- **THEN** `False` SHALL be returned

### Requirement: SnapshotStore.clear() removes all snapshots and turn results

`clear()` SHALL remove all stored snapshots and turn results, resetting the store to its initial state.

#### Scenario: Clear snapshot store

- **WHEN** `clear()` is called
- **THEN** all snapshots SHALL be removed
- **AND** all turn results SHALL be removed
- **AND** `load()` SHALL return `None` after clearing

### Requirement: MemorySnapshotStore is the default in-memory implementation

`MemorySnapshotStore` SHALL implement the SnapshotStore Protocol using in-memory data structures. It SHALL NOT persist data across process restarts. It SHALL be the default when no SnapshotStore is explicitly provided.

#### Scenario: In-memory snapshot store loses data on process exit

- **WHEN** a `MemorySnapshotStore` is used and the process exits
- **THEN** all snapshots and turn results SHALL be lost (no persistence)

### Requirement: SQLSnapshotStore persists snapshots to a SQL database

`SQLSnapshotStore` SHALL implement the SnapshotStore Protocol using a SQL database backend. It SHALL persist data across process restarts. It SHALL be used for protocol session persistence.

#### Scenario: SQL snapshot survives restart

- **WHEN** a `SQLSnapshotStore` is used, a snapshot is saved, and the process restarts
- **THEN** `load()` SHALL return the persisted snapshot after restart

### Requirement: DurableSnapshotStore provides crash-safe state persistence

`DurableSnapshotStore` SHALL implement the SnapshotStore Protocol with crash-safe writes (fsync/WAL). It SHALL be used when `lifecycle.snapshot: durable` is configured.

#### Scenario: Durable snapshot survives crash

- **WHEN** a `DurableSnapshotStore` is used, a snapshot is saved, and the process crashes
- **THEN** on restart, `load()` SHALL return the last successfully saved snapshot
- **AND** no partial or corrupted snapshots SHALL be returned

### Requirement: SnapshotStore is owned by RunLoop

The SnapshotStore reference SHALL be held by RunLoop. RunLoop SHALL call `save()`, `load()` (via `journal.resume()`), `save_turn_result()`, and `has_turn_result()` directly. CommChannel SHALL NOT access SnapshotStore.

#### Scenario: RunLoop saves snapshot at Turn boundary

- **WHEN** a Turn completes
- **THEN** RunLoop SHALL call `snapshot_store.save(state)` and `snapshot_store.save_turn_result(turn_id, result)`
- **AND** CommChannel SHALL NOT be involved in snapshot operations

### Requirement: SnapshotStore and Journal are independently composable

Any execution mode SHALL be able to use any combination of Journal and SnapshotStore implementations. A standalone run MAY use `DurableJournal + DurableSnapshotStore` for crash recovery; a protocol session MAY use `MemoryJournal + MemorySnapshotStore` for low-latency.

#### Scenario: Standalone with durable persistence

- **WHEN** a standalone RunLoop is configured with `lifecycle.journal: durable` and `lifecycle.snapshot: durable`
- **THEN** `DurableJournal` and `DurableSnapshotStore` SHALL be used
- **AND** crash recovery SHALL be available even in standalone mode

#### Scenario: Protocol session with in-memory persistence

- **WHEN** a protocol session RunLoop is configured with `MemoryJournal` and `MemorySnapshotStore`
- **THEN** events SHALL NOT be persisted across restarts
- **AND** no crash recovery SHALL be available
