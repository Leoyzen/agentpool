## ADDED Requirements

### Requirement: TurnState enum replaces TOCTOU is-None checks
Each session SHALL maintain an explicit `turn_state: TurnState` enum with four values: `IDLE`, `BOOTING`, `RUNNING`, `TEARDOWN`. All steer/followup routing logic SHALL use `match session.turn_state` instead of checking `current_run_id is None` and `active_agent_run is None` independently.

#### Scenario: TurnState transitions follow turn lifecycle
- **WHEN** `_run_turn_unlocked` sets `current_run_id`
- **THEN** `turn_state` SHALL transition to `BOOTING`
- **WHEN** `RunExecutor` sets `active_agent_run`
- **THEN** `turn_state` SHALL transition to `RUNNING`
- **WHEN** `RunExecutor` clears `active_agent_run`
- **THEN** `turn_state` SHALL transition to `TEARDOWN`
- **WHEN** `_run_turn_unlocked` clears `current_run_id`
- **THEN** `turn_state` SHALL transition to `IDLE`

### Requirement: steer() uses match on turn_state instead of cascade
The `steer()` method SHALL use a single `match session.turn_state` to route messages:

```python
match session.turn_state:
    case TurnState.RUNNING:
        # enqueue via agent_run.enqueue()
    case _:
        # write SteerItem to work stream
```

It SHALL NOT use `current_run_id is None` or `active_agent_run is None` checks for routing decisions.

#### Scenario: steer during RUNNING enqueues directly
- **WHEN** `steer()` is called and `turn_state == RUNNING`
- **THEN** the message SHALL be enqueued via `agent_run.enqueue(priority="asap")`

#### Scenario: steer during BOOTING writes to work stream
- **WHEN** `steer()` is called and `turn_state == BOOTING`
- **THEN** a `SteerItem` SHALL be written to the work stream (not enqueued, not receive_request)

#### Scenario: steer during TEARDOWN writes to work stream
- **WHEN** `steer()` is called and `turn_state == TEARDOWN`
- **THEN** a `SteerItem` SHALL be written to the work stream (not enqueued, not receive_request)

#### Scenario: steer during IDLE writes to work stream (not receive_request)
- **WHEN** `steer()` is called and `turn_state == IDLE`
- **THEN** a `SteerItem` SHALL be written to the work stream, NOT calling `receive_request()`

### Requirement: TurnState transitions are set at exact lifecycle points
Each transition SHALL be set at the EXACT point where the underlying state changes, not earlier or later. The `BOOTING` transition happens when `current_run_id` is set (before `agent_run` is established). The `RUNNING` transition happens when `agent_run` becomes active (inside `RunExecutor.execute()`). The `TEARDOWN` transition happens when `agent_run` is cleared. The `IDLE` transition happens when `current_run_id` is cleared.

#### Scenario: No window between state changes
- **WHEN** `turn_state == RUNNING`
- **THEN** both `current_run_id` and `active_agent_run` SHALL be non-None
- **WHEN** `turn_state == TEARDOWN`
- **THEN** `current_run_id` SHALL be non-None and `active_agent_run` SHALL be None
- **WHEN** `turn_state == IDLE`
- **THEN** `current_run_id` SHALL be None

### Requirement: TurnState guarantees deterministic steer routing
For any call to `steer()` at any point in the turn lifecycle, the routing decision SHALL be deterministic based solely on `session.turn_state`:

| turn_state | steer action |
|------------|-------------|
| IDLE | write SteerItem to work stream |
| BOOTING | write SteerItem to work stream |
| RUNNING | enqueue via agent_run.enqueue() |
| TEARDOWN | write SteerItem to work stream |

#### Scenario: Every state has a defined steer action
- **WHEN** `steer()` is called at any `turn_state`
- **THEN** there SHALL be a matching `case` arm in the `match` block, and no fall-through or `is None` fallback
