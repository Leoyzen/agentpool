## ADDED Requirements

### Requirement: Single TurnRunner class
The system SHALL provide exactly one `TurnRunner` class in `orchestrator/core.py`. The system SHALL NOT maintain a separate `LegacyTurnRunner` class or module. All turn execution for both native and non-native agents SHALL route through this unified `TurnRunner`.

#### Scenario: Native agent turn execution
- **WHEN** a native agent turn is started via `SessionPool.receive_request()`
- **THEN** `TurnRunner` dispatches execution to `NativeExecutionStrategy`
- **AND** the turn completes using PydanticAI's `agentlet.iter()` loop

#### Scenario: Non-native agent turn execution
- **WHEN** a non-native agent turn (ACP, ClaudeCode, AGUI) is started via `SessionPool.receive_request()`
- **THEN** `TurnRunner` dispatches execution to `NonNativeExecutionStrategy`
- **AND** the turn completes using the manual `_run_stream_once()` + queue drain loop

### Requirement: Turn execution strategy protocol
The system SHALL define a `TurnExecutionStrategy` protocol with a single `execute_turn()` method. `TurnRunner` SHALL hold a mapping from `AGENT_TYPE` string to strategy instance. `TurnRunner` SHALL delegate turn execution to `strategy.execute_turn(...)` without branching on agent type in the main turn loop.

#### Scenario: Strategy lookup by agent type
- **WHEN** `TurnRunner` begins a turn for an agent with `AGENT_TYPE == "native"`
- **THEN** it resolves the strategy using the `"native"` key in its registry
- **AND** invokes `execute_turn()` on that strategy

#### Scenario: Strategy isolation
- **WHEN** two concurrent turns run for agents of different types
- **THEN** each turn uses its respective strategy
- **AND** neither strategy shares mutable state with the other

### Requirement: Shared infrastructure preserved
`TurnRunner` SHALL continue to manage all shared turn infrastructure regardless of agent type: per-session injection locks (`_injection_locks`), post-turn queues (`_post_turn_injections`, `_post_turn_prompts`), auto-resume loop (`_process_queued_work()`), `RunHandle` lifecycle, event queue consumption, and timing tracking.

#### Scenario: Auto-resume for native agent
- **WHEN** a native agent turn completes and queued prompts remain
- **THEN** `TurnRunner._process_queued_work()` drains the queues
- **AND** starts a follow-up turn via `NativeExecutionStrategy`

#### Scenario: Auto-resume for non-native agent
- **WHEN** a non-native agent turn completes and queued prompts remain
- **THEN** `TurnRunner._process_queued_work()` drains the queues
- **AND** starts a follow-up turn via `NonNativeExecutionStrategy`

### Requirement: Legacy runner removal
The system SHALL NOT contain a module at `orchestrator/legacy_runner.py`. All imports of `LegacyTurnRunner` SHALL be removed. All tests referencing `LegacyTurnRunner` SHALL be updated to use `TurnRunner` with the appropriate strategy.

#### Scenario: Import legacy runner fails
- **WHEN** any code attempts `from agentpool.orchestrator.legacy_runner import LegacyTurnRunner`
- **THEN** an `ImportError` is raised
