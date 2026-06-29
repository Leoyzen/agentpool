## ADDED Requirements

### Requirement: Recursive cancellation propagation for subagent sessions
When a parent session's event consumer is stopped, the system SHALL recursively stop all child session consumers via `_parent_of` mapping walk-tree.

#### Scenario: Parent cancellation cascades to children
- **GIVEN** parent session `P` has child sessions `C1` and `C2`
- **AND** `C1` has a grandchild `G1`
- **WHEN** `stop_event_consumer("P")` is called (or `_cancel_subagents("P")`)
- **THEN** `G1` SHALL be stopped first (deepest first)
- **AND** `C1` SHALL be stopped after `G1`
- **AND** `C2` SHALL be stopped
- **AND** `_parent_of` entries for `C1`, `C2`, and `G1` SHALL be removed

#### Scenario: _parent_of cleanup on normal child exit
- **WHEN** a child session's consumer loop exits normally (not via cancellation)
- **THEN** the `_parent_of` entry for that child SHALL be removed by the completion notification closure

### Requirement: _parent_of lightweight mapping for cancellation only
The `_parent_of: dict[str, str]` mapping (child_sid → parent_sid) SHALL be used solely for recursive cancellation. Completion notification SHALL NOT depend on `_parent_of` — it uses closure capture instead.

#### Scenario: _parent_of populated on spawn
- **WHEN** `_on_spawn_session_start` processes a `SpawnSessionStart` with `child_session_id="C1"` and parent `session_id="P"`
- **THEN** `self._parent_of["C1"] = "P"` SHALL be set

#### Scenario: _parent_of cleaned on cancellation
- **WHEN** `_cancel_subagents("P")` stops child `"C1"`
- **THEN** `self._parent_of.pop("C1", None)` SHALL be called
