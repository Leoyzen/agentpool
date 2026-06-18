## MODIFIED Requirements

### Requirement: SessionController receives and routes all requests with agent-type awareness
The system SHALL route all session-bound requests through `SessionController.receive_request()`. `receive_request()` SHALL be fire-and-forget, returning `None`. Protocol handlers SHALL continue consuming events via `EventBus` subscription before calling `receive_request()`. `receive_request()` SHALL inspect the session's agent type and route accordingly:
- **Native agents (Phase 2)**: acquire `SessionState._request_lock`, then check `SessionState.current_run_id`. If idle, create a `RunHandle` with PydanticAI `AgentRun` and start execution via `RunExecutor`. If active, call `TurnRunner.steer()` or `TurnRunner.followup()` based on priority.
- **Non-native agents**: delegate to `TurnRunner.inject_prompt()` / `queue_prompt()` compatibility layer.

**Note**: Phase 1 (legacy `TurnRunner` without `steer()`/`followup()`) is removed. Native agents always use Phase 2 routing with `steer()`/`followup()`.

#### Phase 2 Scenario: Idle native session receives new request
- **WHEN** `receive_request()` is called on a native session with `current_run_id` equal to `None`
- **THEN** the system acquires `_request_lock`, verifies `current_run_id` is still `None`
- **AND** creates a new `RunHandle` with PydanticAI `AgentRun`
- **AND** adds the `RunHandle` to `SessionPool._runs`
- **AND** sets `SessionState.current_run_id` while still holding `_request_lock`
- **AND** releases `_request_lock`
- **AND** initiates turn execution via `RunExecutor`

#### Phase 2 Scenario: Active native session receives steering request
- **WHEN** `receive_request()` is called with `priority="steer"` (or `"asap"`) on a native session with an active run
- **THEN** the system calls `TurnRunner.steer(message)`
- **AND** `steer()` calls `pydantic_ai_run.enqueue(message, priority='asap')`
- **AND** the message is injected before the next LLM call

#### Phase 2 Scenario: Active native session receives followup request
- **WHEN** `receive_request()` is called with `priority="followup"` (or `"when_idle"`) on a native session with an active run
- **THEN** the system calls `TurnRunner.followup(message)`
- **AND** `followup()` calls `pydantic_ai_run.enqueue(message, priority='when_idle')`
- **AND** the message is processed when the agent would otherwise terminate

#### Scenario: Non-native session receives request (Phase 2)
- **WHEN** `receive_request()` is called on a non-native session
- **THEN** the system delegates to `TurnRunner.inject_prompt()` or `queue_prompt()`
- **AND** `TurnRunner` acquires `SessionState.turn_lock` for turn serialization
- **AND** existing non-native queue behavior is preserved

### Requirement: PydanticAI pending message queue replaces manual follow-up prompt queue for native agents only
The system SHALL use PydanticAI's `PendingMessageDrainCapability` for follow-up prompt delivery on native agents. `RunExecutor` (native-agent turn driver) SHALL NOT maintain `_post_turn_prompts` or `_injection_locks` for follow-up prompts. `BaseAgent._run_stream_once()` SHALL NOT contain its own internal prompt continuation loop for native agents.

**CRITICAL**: `PromptInjectionManager.inject()`/`consume()` (tool result augmentation via `after_tool_execute`) is NOT replaced by the steer/followup API. This mechanism modifies tool results with `<injected-context>` XML, not conversation messages. It SHALL be preserved for all agents.

#### Scenario: External code enqueues steer message on native agent
- **WHEN** external code calls `TurnRunner.steer(message)` while a native run is active
- **THEN** `pydantic_ai_run.enqueue(message, priority='asap')` is called
- **AND** `PendingMessageDrainCapability.before_model_request()` drains it before the next `ModelRequest`

#### Scenario: External code enqueues followup message on native agent
- **WHEN** external code calls `TurnRunner.followup(message)` while a native run is active
- **THEN** `pydantic_ai_run.enqueue(message, priority='when_idle')` is called
- **AND** the message remains queued until the agent would otherwise terminate
- **AND** PydanticAI extends the run with an additional model request

#### Scenario: No manual auto-resume needed for native agents
- **WHEN** a follow-up message is enqueued after a native turn ends
- **THEN** PydanticAI's `after_node_run` hook automatically drains the queue
- **AND** no `_trigger_auto_resume()` or `_process_queued_work()` logic is executed for native agents

#### Scenario: Tool result augmentation still works for native agents
- **WHEN** a tool calls `run_ctx.injection_manager.inject("also check tests")` during a native turn
- **THEN** `PromptInjectionManager.inject()` stores the message
- **AND** `NativeAgentHookManager.after_tool_execute` consumes it via `injection_manager.consume()`
- **AND** the injected context is added to the tool result (wrapped in `<injected-context>` tags)
- **AND** this is separate from the steer/followup API

### Requirement: TurnRunner accesses AgentRun via RunHandle for native agents
`TurnRunner` SHALL access the active PydanticAI `AgentRun` via `run_handle.active_agent_run` (set by `RunExecutor`). `TurnRunner` SHALL NOT maintain its own `_active_agent_run` field — the `RunHandle` is the canonical source. `steer()` and `followup()` SHALL use `run_handle.active_agent_run` to call `enqueue()`.

#### Scenario: Steer during active native run
- **WHEN** `TurnRunner.steer()` is called during an active native run
- **THEN** the system reads `run_handle.active_agent_run`
- **AND** calls `run_handle.active_agent_run.enqueue(message, priority='asap')`

#### Scenario: Steer called with no active native run
- **WHEN** `TurnRunner.steer()` is called and `run_handle.active_agent_run` is `None`
- **THEN** the system delegates to `receive_request(session_id, message, priority="steer")`
- **AND** a new run is started with the steer message

### Requirement: Agent type detection uses agent.AGENT_TYPE ClassVar
The system SHALL use `agent.AGENT_TYPE` (a `ClassVar` on the agent instance) for all agent-type-aware routing decisions in `_run_turn_unlocked()` and `_create_run()`. The `session.metadata.get("agent_type", "unknown")` pattern SHALL NOT be used for gating behavior — it only returns `"unknown"` for native agents (only ACP sessions set this metadata).

- `agent.AGENT_TYPE == "native"` for `Agent` (native agent)
- `agent.AGENT_TYPE == "acp"` for `ACPAgent`
- In `_run_turn_unlocked()`, the agent instance is already resolved — read `agent.AGENT_TYPE` directly
- In `_create_run()`, the agent instance is already resolved — read `agent.AGENT_TYPE` directly
- In `SessionController.receive_request()`, `session.metadata.get("agent_type", "unknown")` may be used as a fallback only when the agent is not yet resolved

#### Scenario: Native agent detected correctly
- **WHEN** `_run_turn_unlocked()` checks agent type for a native agent session
- **THEN** it reads `agent.AGENT_TYPE` which returns `"native"`
- **AND** the manual follow-up loop is correctly skipped

#### Scenario: Non-native agent detected correctly
- **WHEN** `_run_turn_unlocked()` checks agent type for an ACP agent session
- **THEN** it reads `agent.AGENT_TYPE` which returns `"acp"`
- **AND** the manual follow-up loop is correctly preserved

## REMOVED Requirements

### Requirement: Phase 1 active native session follow-up via inject_prompt/queue_prompt
**Reason**: Phase 1 routing is removed. Native agents now use `steer()`/`followup()` → `agent_run.enqueue()`.
**Migration**: Callers previously using `TurnRunner.inject_prompt()` for native agents should use `TurnRunner.steer()`. Callers using `TurnRunner.queue_prompt()` for native agents should use `TurnRunner.followup()`.

### Requirement: Phase 1 idle non-native session via existing TurnRunner
**Reason**: Phase 1 is removed. Non-native sessions always use the existing `TurnRunner` with `inject_prompt()`/`queue_prompt()`.
**Migration**: No change needed — non-native routing is identical in behavior.

### Requirement: Phase 1 active non-native session via TurnRunner
**Reason**: Phase 1 is removed. Behavior is identical for non-native agents.
**Migration**: No change needed.