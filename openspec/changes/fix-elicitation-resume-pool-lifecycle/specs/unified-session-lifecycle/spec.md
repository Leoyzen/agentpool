## MODIFIED Requirements

### Requirement: Resumed turns participate in RunHandle lifecycle

Resumed turns SHALL execute through the full RunHandle lifecycle, including TriggerSource, Journal, SnapshotStore, and CommChannel dimensions. This enables crash recovery for the resume run itself and ensures session state transitions are managed by the RunHandle, not manually by `resume_session()`.

#### Scenario: Resumed turn has RunHandle
- **WHEN** `resume_session()` starts a resumed turn via the pool
- **THEN** a `RunHandle` is created with all lifecycle dimensions
- **AND** the `RunHandle` manages session state transitions (idle → running → done)
- **AND** journal entries are written for the resumed turn

#### Scenario: Resumed turn crash is recoverable
- **WHEN** the resumed turn crashes mid-execution
- **THEN** the journal and snapshot store preserve partial state
- **AND** `resume_session()` can be called again to recover from the last checkpoint

### Requirement: Resumed turns with durable journals start fresh

Resumed turns with `lifecycle: journal: durable` SHALL start with a fresh `MemoryJournal`. The original turn's journal entries are from a different `RunHandle` — replaying them into the resumed turn's journal would create duplicates. The checkpoint in storage is the authoritative state for crash recovery.

#### Scenario: Durable journal resumed turn
- **WHEN** `resume_session()` starts a resumed turn with `lifecycle: journal: durable` configured
- **THEN** the `RunHandle` creates a new `MemoryJournal` (not replaying the original turn's journal)
- **AND** the resumed turn's journal entries are independent of the original turn's entries
- **AND** if the resumed turn crashes, recovery uses the checkpoint in storage (not journal replay)
