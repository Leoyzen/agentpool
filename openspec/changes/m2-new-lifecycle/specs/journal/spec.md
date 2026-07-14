## ADDED Requirements

### Requirement: Journal is a Protocol with append, upsert, replay, resume, compact, and clear methods

Journal SHALL be a `@runtime_checkable` Protocol for event-layer persistence. It SHALL support two write semantics: `append()` for delta events (each entry is a new record) and `upsert(key, event)` for entity-state events (latest state per key replaces previous). Both methods SHALL return a monotonically increasing sequence number (`seq`).

#### Scenario: Protocol conformance

- **WHEN** a class implements `append`, `upsert`, `replay`, `resume`, `compact`, `clear`, `log_tool_execution`, and `get_tool_executions` methods
- **THEN** `isinstance(instance, Journal)` SHALL return `True`

### Requirement: Journal.append() creates a new entry for delta events

`append(event)` SHALL create a new journal entry and return the sequence number. Each call creates a distinct entry — entries are not deduplicated.

#### Scenario: Append delta event

- **WHEN** `append(PartDeltaEvent(delta="hello"))` is called
- **THEN** a new journal entry SHALL be created
- **AND** the returned `seq` SHALL be greater than all previous `seq` values

### Requirement: Journal.upsert() replaces entity-state events by key

`upsert(key, event)` SHALL replace any existing entry with the same key. If no entry exists, a new one is created. The returned `seq` SHALL be monotonically increasing.

#### Scenario: Upsert creates new entry

- **WHEN** `upsert("tool_call:abc", ToolCallUpdateEvent(...))` is called and no entry with key `"tool_call:abc"` exists
- **THEN** a new journal entry SHALL be created
- **AND** the returned `seq` SHALL be greater than all previous `seq` values

#### Scenario: Upsert replaces existing entry

- **WHEN** `upsert("tool_call:abc", updated_event)` is called and an entry with key `"tool_call:abc"` already exists
- **THEN** the existing entry SHALL be replaced with the updated event
- **AND** the returned `seq` SHALL be greater than all previous `seq` values

### Requirement: Journal.replay() returns events from a sequence range

`replay(from_seq, to_seq)` SHALL return an async iterator of events. Append entries SHALL all be returned, ordered by `seq`. Upsert entries SHALL return only the latest state per key. Mixed entries SHALL be ordered by `seq` with upsert keys deduplicated to latest.

#### Scenario: Replay from beginning

- **WHEN** `replay(from_seq=0)` is called on a journal with 10 entries
- **THEN** all 10 events SHALL be returned in sequence order
- **AND** upsert keys SHALL appear only once with their latest state

#### Scenario: Replay from specific sequence

- **WHEN** `replay(from_seq=5)` is called
- **THEN** only events with `seq >= 5` SHALL be returned
- **AND** upsert keys SHALL reflect their latest state as of `from_seq`

### Requirement: Journal.resume() coordinates snapshot and journal for crash recovery

`resume(snapshot_store)` SHALL be the primary crash recovery entry point. It SHALL: (1) load the latest snapshot via `snapshot_store.load()`, (2) if no snapshot exists, return `None`, (3) replay journal events since the snapshot, (4) determine if a Turn was in-flight, (5) return a `ResumeResult`.

#### Scenario: No prior state — fresh start

- **WHEN** `resume(snapshot_store)` is called and `snapshot_store.load()` returns `None`
- **THEN** `None` SHALL be returned (no state to resume from)

#### Scenario: Normal recovery — no in-flight Turn

- **WHEN** `resume(snapshot_store)` is called and the snapshot exists but no Turn was in-flight
- **THEN** a `ResumeResult` with `is_inflight=False` SHALL be returned
- **AND** `ResumeResult.state` SHALL contain the restored state
- **AND** `ResumeResult.events` SHALL contain events since the snapshot

#### Scenario: Crash during in-flight Turn

- **WHEN** `resume(snapshot_store)` is called and the journal has entries after the snapshot with no corresponding `turn_result`
- **THEN** a `ResumeResult` with `is_inflight=True` SHALL be returned
- **AND** `ResumeResult.events` SHALL contain the in-flight Turn's events
- **AND** `ResumeResult.inflight_turn_id` SHALL contain the Turn ID

### Requirement: Journal.compact() removes entries before a sequence number

`compact(before_seq)` SHALL remove journal entries with `seq < before_seq`. This is called after a successful snapshot to prevent unbounded journal growth.

#### Scenario: Compact after snapshot

- **WHEN** `compact(before_seq=100)` is called on a journal with entries at seq 0-99 and 100+
- **THEN** entries with `seq < 100` SHALL be removed
- **AND** entries with `seq >= 100` SHALL remain

### Requirement: Journal.clear() removes all entries

`clear()` SHALL remove all journal entries, resetting the sequence counter.

#### Scenario: Clear journal

- **WHEN** `clear()` is called
- **THEN** all journal entries SHALL be removed
- **AND** the next `append()` or `upsert()` SHALL return `seq=1` (or the implementation's initial value)

### Requirement: Journal maintains a tool execution log

Journal SHALL maintain a separate tool execution log via `log_tool_execution(record)` and `get_tool_executions(turn_id)`. This log records tool calls within Turns for idempotent crash recovery.

#### Scenario: Log tool execution

- **WHEN** `log_tool_execution(ToolExecutionRecord(turn_id="t1", tool_name="bash", ...))` is called
- **THEN** the record SHALL be stored
- **AND** it SHALL be retrievable via `get_tool_executions("t1")`

#### Scenario: Retrieve tool executions for a Turn

- **WHEN** `get_tool_executions("t1")` is called and Turn "t1" had 3 tool calls
- **THEN** a list of 3 `ToolExecutionRecord` instances SHALL be returned
- **AND** each record SHALL include `status`, `result`, and `tool_name`

### Requirement: MemoryJournal is the default in-memory implementation

`MemoryJournal` SHALL implement the Journal Protocol using in-memory data structures. It SHALL NOT persist data across process restarts. It SHALL be the default when no Journal is explicitly provided.

#### Scenario: In-memory journal loses data on process exit

- **WHEN** a `MemoryJournal` is used and the process exits
- **THEN** all journal entries SHALL be lost (no persistence)

#### Scenario: Sequence numbers are monotonic in MemoryJournal

- **WHEN** `append()` or `upsert()` is called multiple times on a `MemoryJournal`
- **THEN** each returned `seq` SHALL be strictly greater than the previous one

### Requirement: SQLJournal persists events to a SQL database

`SQLJournal` SHALL implement the Journal Protocol using a SQL database backend. It SHALL persist data across process restarts. It SHALL be used for protocol session persistence.

#### Scenario: SQL journal survives restart

- **WHEN** a `SQLJournal` is used, entries are appended, and the process restarts
- **THEN** all journal entries SHALL be retrievable via `replay()` after restart

### Requirement: DurableJournal provides crash-safe event persistence

`DurableJournal` SHALL implement the Journal Protocol with crash-safe writes (fsync/WAL). It SHALL be used when `lifecycle.journal: durable` is configured. It SHALL support `resume()` for crash recovery.

#### Scenario: Durable journal survives crash

- **WHEN** a `DurableJournal` is used, events are appended, and the process crashes mid-Turn
- **THEN** on restart, `resume(snapshot_store)` SHALL return a `ResumeResult` with the events that were persisted before the crash
- **AND** no persisted events SHALL be lost or corrupted

### Requirement: Journal is owned by CommChannel, not RunLoop

The Journal reference SHALL be held by CommChannel. CommChannel SHALL call `journal.append()` and `journal.upsert()` internally during `publish()`. RunLoop SHALL NOT call Journal methods directly except for `resume()` during crash recovery.

#### Scenario: CommChannel journals events

- **WHEN** `comm_channel.publish(event)` is called and `_replaying` is `False`
- **THEN** the CommChannel SHALL call `journal.append()` or `journal.upsert()` before delivering the event to the consumer

#### Scenario: RunLoop calls resume during crash recovery

- **WHEN** RunLoop starts and needs crash recovery
- **THEN** RunLoop SHALL call `journal.resume(snapshot_store)` directly
- **AND** this SHALL be the only direct Journal call from RunLoop
