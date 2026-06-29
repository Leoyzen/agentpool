## ADDED Requirements

### Requirement: merge_queue_into_iterator is deprecated and removed
The system SHALL deprecate and remove `merge_queue_into_iterator` from `src/agentpool/utils/streams.py`. Its sole caller in `src/agentpool/agents/acp_agent/acp_agent.py` SHALL be refactored to use `anyio.TaskGroup` + `anyio.create_memory_object_stream` instead.

#### Scenario: merge_queue_into_iterator is removed
- **WHEN** `from agentpool.utils.streams import merge_queue_into_iterator` is attempted
- **THEN** an `ImportError` is raised
- **AND** the function is removed from the module

#### Scenario: ACP agent uses TaskGroup + memory stream for event merging
- **WHEN** `ACPAgent._run_stream_once()` needs to merge `poll_acp_events()` with `event_source`
- **THEN** it creates an `anyio.create_memory_object_stream`
- **AND** spawns two forwarding tasks in an `anyio.create_task_group()`:
  - One task forwards from `poll_acp_events()` to the memory stream
  - One task forwards from `event_source` (the queue) to the memory stream
- **AND** the consumer iterates over the memory stream's receive end
- **AND** the `TaskGroup` guarantees cleanup of both forwarding tasks on exit

#### Scenario: GeneratorExit / cancellation is handled by TaskGroup
- **WHEN** the consumer breaks from iteration (GeneratorExit)
- **THEN** the `TaskGroup.__aexit__` cancels both forwarding tasks
- **AND** no `RuntimeError: Attempted to exit cancel scope in a different task` is raised
- **AND** no `ValueError: Token was created in a different Context` is raised

### Requirement: The known break-behavior bugs are fixed
The removal of `merge_queue_into_iterator` SHALL resolve the critical bugs documented in `tests/delegation/test_break_behavior.py`:
1. `RuntimeError: Attempted to exit cancel scope in a different task` — caused by the context manager's task switching during GeneratorExit
2. `ValueError: Token was created in a different Context` — same root cause
3. `RuntimeError: generator didn't stop after athrow()` — same root cause
4. Agent state corruption after breaking from `run_stream()` iteration

#### Scenario: Breaking from run_stream() does not corrupt agent state
- **WHEN** a consumer breaks from `async for event in agent.run_stream(...)`
- **THEN** no internal exceptions are printed to stderr
- **AND** a subsequent `run_stream()` call works without error
- **AND** conversation history is preserved correctly

### Requirement: stream_utils module retains other utilities
The removal SHALL only affect `merge_queue_into_iterator`. Other utilities in `src/agentpool/utils/streams.py` (`FileChange`, etc.) SHALL remain unchanged.

#### Scenario: Non-affected utilities continue to work
- **WHEN** any other utility from `streams.py` is imported
- **THEN** the import succeeds without error
- **AND** existing behavior is preserved
