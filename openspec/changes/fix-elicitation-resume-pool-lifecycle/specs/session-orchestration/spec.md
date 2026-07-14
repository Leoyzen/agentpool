## MODIFIED Requirements

### Requirement: Resume through pool-managed turn

`resume_session()` SHALL route the resumed agent execution through the SessionPool's normal turn management (Path A: `session_pool.run_stream()` → `_run_stream_run_turn()` → `_create_run_handle()`), not through standalone execution (Path B: `agent.run_stream(_skip_pool=True)`). The resumed turn SHALL have full RunHandle lifecycle support including journal, snapshot, event delivery, and session coordination.

#### Scenario: Resume starts a pool-managed turn
- **WHEN** `resume_session()` is called with elicitation payloads
- **THEN** a `RunHandle` is created via `SessionPool._create_run_handle()` (`session_pool.py`)
- **AND** the `RunHandle` has full lifecycle dimensions (TriggerSource, Journal, SnapshotStore, CommChannel)
- **AND** events from the resumed turn are published to the shared EventBus

#### Scenario: Resume state passed as parameters
- **WHEN** `resume_session()` builds `cached_elicitation_responses` from elicitation payloads
- **THEN** `cached_elicitation_responses`, `deferred_tool_results`, and `message_history` are passed as optional parameters to `session_pool.run_stream()` → `_run_stream_run_turn()` → `_create_run_handle()`
- **AND** `_create_run_handle()` sets `cached_elicitation_responses` on the new `AgentRunContext`
- **AND** `deferred_tool_results` flows through to `NativeTurn` via `**pydantic_ai_kwargs` to `agentlet.iter()`
- **AND** `message_history` from the checkpoint initializes the `RunHandle._message_history` field (existing `list[ModelMessage]` field, not a new field)
- **AND** no persistent resume state is left on `SessionState` after the turn (parameters are used and discarded)

#### Scenario: Normal turns unaffected
- **WHEN** a normal (non-resume) turn is started via `run_stream()` or `receive_request()`
- **THEN** `cached_elicitation_responses`, `deferred_tool_results`, and `message_history` are all `None`
- **AND** the `AgentRunContext` is created without resume state
- **AND** runtime behavior is identical to before this change

### Requirement: _create_run_handle() provides full infrastructure

`SessionPool._create_run_handle()` SHALL set `_host_context` and `_agent_registry` on the `RunHandle` to match the capabilities of `SessionController._start_run_handle()`. Without these, `get_agentlet()` cannot create a `CheckpointManager` (requires `host_context`), and `SubagentCapability` cannot resolve agents (requires `agent_registry`).

#### Scenario: Resumed turn has host_context
- **WHEN** `_create_run_handle()` creates a `RunHandle` for a resumed turn
- **THEN** `run_handle._host_context` is set (from `pool.get_context()`)
- **AND** `run_handle._agent_registry` is built from the pool's manifest agent names
- **AND** `get_agentlet()` can create `CheckpointManager` (because `host_context is not None`)

### Requirement: _run_stream_run_turn() guards against concurrent runs

`_run_stream_run_turn()` SHALL acquire `session._request_lock` before checking `current_run_id` and calling `_create_run_handle()`, to prevent concurrent RunHandle creation when `resume_session()` and `receive_request()` race on the same session.

#### Scenario: Concurrent resume and normal turn
- **WHEN** `resume_session()` calls `run_stream()` and simultaneously `receive_request()` is called on the same session
- **THEN** only ONE `RunHandle` is created (serialized by `_request_lock`)
- **AND** the loser sees `current_run_id is not None` and either waits or raises `SessionBusyError`

### Requirement: Resume supports full session continuation

A resumed turn SHALL support the same capabilities as a normal turn, including: new elicitations with durable checkpoints, crash recovery for the resume run itself, and session state transitions.

#### Scenario: Second elicitation during resume is durable
- **WHEN** the resumed agent encounters a new elicitation (different `tool_call_id` from the cached one)
- **THEN** `handle_elicitation()` takes Path 3 (local tools): checkpoint + register future + await
- **AND** `elicitation_registry` is available (set by `get_agentlet()`)
- **AND** `checkpoint_manager` is available (set by `get_agentlet()`, requires `_host_context`)
- **AND** `input_provider` is available (set via `_current_input_provider` ContextVar by `RunHandle._execute_turn()`)

#### Scenario: Crash during resume is recoverable
- **WHEN** the resumed turn crashes (network error, process kill)
- **THEN** the RunHandle's journal and snapshot store preserve the resume turn's state
- **AND** `resume_session()` can be called again to recover from the checkpoint

### Requirement: message_history from checkpoint

`_resume_native_agent()` SHALL pass the checkpoint's `message_history` (as `list[ModelMessage]`, not wrapped in `MessageHistory`) to the pool-managed turn. `_create_run_handle()` initializes the existing `RunHandle._message_history` field with this list. `RunHandle._execute_turn()` passes it to `agent.create_turn()` as the starting conversation state.

#### Scenario: Checkpoint message history used as turn input
- **WHEN** `resume_session()` loads a checkpoint with `message_history`
- **THEN** the `message_history` (`list[ModelMessage]`) initializes `RunHandle._message_history`
- **AND** the agent re-executes from the checkpointed state, not from an empty conversation
- **AND** no `MessageHistory` wrapper is needed for the pool path (only `agent.run_stream()` standalone path uses `MessageHistory`)
