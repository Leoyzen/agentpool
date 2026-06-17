## MODIFIED Requirements

### Requirement: SessionPool is the mandatory execution entry point
The system SHALL route all streaming agent execution through `SessionPool` when `AgentPool` is active. `BaseAgent.run_stream()` SHALL delegate to `SessionPool.run_stream()` and emit a deprecation warning. `BaseAgent` SHALL NOT store `session_id`, `_active_run_ctx`, `_current_stream_task`, or `_event_queue` as instance state.

## MODIFIED Requirements

### Requirement: InjectionManager mid-turn injection (native agents only)
**Reason**: For native agents, replaced by `steer()`/`followup()` API which maps to `agent_run.enqueue(priority='asap'/'when_idle')`. Note: `PromptInjectionManager.inject()`/`consume()` for tool result augmentation (wrapping in `<injected-context>` XML) is NOT replaced and remains for all agents.
**Migration**: Native-agent tools previously using `run_ctx.injection_manager.inject()` for conversation injection shall use `run_ctx.enqueue()` instead. Tools using `inject()` for tool result augmentation keep using it. Protocol handlers previously calling `TurnRunner.inject_prompt()` shall use `TurnRunner.steer()` for native agents.

### Requirement: BaseAgent internal prompt continuation loop (native agents only)
**Reason**: `BaseAgent._run_stream_direct()` contains a `while True` loop that processes queued prompts from the run context after each stream completes. For native agents, this loop duplicates PydanticAI's `PendingMessageDrainCapability` behavior and conflicts with it. Non-native agents retain this loop as it is their only continuation mechanism.
**Migration**: Remove the internal loop from `_run_stream_direct()` for native agents. PydanticAI handles continuation via `PendingMessageDrainCapability` at `before_model_request` and `after_node_run`. Non-native agents keep the loop.

## ADDED Requirements

### Requirement: TurnRunner._run_turn_unlocked() follow-up loop removed for native agents
For native agents, `_run_turn_unlocked()` SHALL NOT execute the manual follow-up prompt loop. `PendingMessageDrainCapability.after_node_run()` SHALL handle follow-up continuation via graph-node redirection. For non-native agents, the manual loop SHALL be preserved.

#### Scenario: Native agent turn completes with enqueued follow-up
- **WHEN** a native agent's `_run_stream_once()` completes and `PendingMessageDrainCapability` has messages to drain
- **THEN** `after_node_run()` returns a `ModelRequestNode` redirect
- **AND** the manual `while has_queued()` loop is NOT executed
- **AND** no `flush_pending_to_queue()` call is needed for native agents in this context

#### Scenario: Non-native agent turn completes with queued prompts
- **WHEN** a non-native agent's `_run_stream_once()` completes and `injection_manager.has_queued()` is true
- **THEN** the manual follow-up loop executes as before
- **AND** `flush_pending_to_queue()` is called between iterations

#### Scenario: Direct run_stream triggers deprecation
- **WHEN** a caller invokes `agent.run_stream()` on an agent that is part of an `AgentPool`
- **THEN** the system emits a `DeprecationWarning` and delegates execution to `SessionPool.run_stream()`

#### Scenario: Shared agent used across sessions
- **WHEN** a shared agent instance is used in two different sessions concurrently
- **THEN** neither session's `session_id` or `run_ctx` is stored on the agent instance
- **AND** both sessions execute independently without state corruption for the explicitly removed attributes

### Requirement: AgentRunContext carries session identity and event routing
`AgentRunContext` SHALL expose `session_id: str | None` and `event_bus: Any | None` fields. `TurnRunner` SHALL populate these fields when creating `AgentRunContext`. `StreamEventEmitter._emit()` SHALL use `run_ctx.session_id` and `run_ctx.event_bus` for event routing instead of agent instance state.

#### Scenario: Tool event routing
- **WHEN** a tool calls `ctx.events.tool_call_progress()` during a SessionPool-managed turn
- **THEN** the emitted event carries the correct `session_id` from `run_ctx.session_id`
- **AND** the event is published to the `EventBus` instance referenced by `run_ctx.event_bus`

#### Scenario: Event emission without agent instance state
- **WHEN** `StreamEventEmitter._emit()` is invoked
- **THEN** it reads `session_id` from `run_ctx.session_id` and does NOT read `agent.session_id`
- **AND** it reads `event_bus` from `run_ctx.event_bus` before falling back to `StreamEventEmitter._event_bus`

## REMOVED Requirements

### Requirement: TurnLock serialization
**Reason**: The per-session `turn_lock` was used to guard the manual queue system (`_post_turn_injections`, `_post_turn_prompts`). With PydanticAI's `PendingMessageDrainCapability` handling queueing internally for native agents, turn execution needs no explicit lock during execution. However, the check-and-create sequence in `receive_request()` requires mutual exclusion; this is provided by `SessionState._request_lock` (per-session lock, not global).
**Migration**: Concurrency control for run creation is handled by per-session `_request_lock`. Run execution serialization is implicit in PydanticAI's agent loop for native agents. Non-native agents continue using `LegacyTurnRunner` which retains its own concurrency model.

### Requirement: InjectionManager mid-turn injection (native agents only)
**Reason**: For native agents, replaced by PydanticAI's native `ctx.enqueue_message(..., priority='asap')`. Non-native agents retain `injection_manager`.
**Migration**: Native-agent tools previously using `run_ctx.injection_manager.inject()` shall use PydanticAI's `ctx.enqueue_message()` instead. Non-native agents continue using `injection_manager`. Protocol handlers previously calling `TurnRunner.inject_prompt()` shall use `SessionController.receive_request()` with steering semantics for native agents.

### Requirement: BaseAgent internal prompt continuation loop (native agents only)
**Reason**: `BaseAgent._run_stream_once()` contains a `while True` loop that processes queued prompts from the run context after each stream completes. For native agents, this loop duplicates PydanticAI's `PendingMessageDrainCapability` behavior and conflicts with it. Non-native agents retain this loop as it is their only continuation mechanism.
**Migration**: Remove the internal loop from `_run_stream_once()` for native agents. PydanticAI handles continuation via `PendingMessageDrainCapability` at `before_model_request` and `after_node_run`. Non-native agents keep the loop.

## ADDED Requirements (from change remove-acp-opencode-legacy-flags)

### Requirement: Protocol handlers SHALL NOT conditionally bypass SessionPool
The system SHALL NOT use feature flags, canary flags, or conditional logic in protocol handlers to bypass SessionPool and route execution to legacy non-session-pool paths. When SessionPool is active, all protocol-level prompt processing MUST route through `SessionPool.receive_request()` or equivalent SessionPool APIs.

#### Scenario: ACP prompt handling without bypass flags
- **WHEN** an ACP client sends a prompt to an ACP agent
- **THEN** the ACP protocol handler invokes `SessionPool.receive_request()`
- **AND** the handler does NOT check per-agent metadata flags to decide whether to use SessionPool
- **AND** the handler does NOT fall back to `session.process_prompt()` or other legacy paths

#### Scenario: OpenCode command execution without category flags
- **WHEN** an OpenCode client executes a command, skill, init, summarize, or MCP operation
- **THEN** the OpenCode protocol handler invokes `SessionPool.receive_request()` for the operation
- **AND** the handler does NOT check per-category feature flags to decide whether to use SessionPool
- **AND** the handler does NOT fall back to direct agent invocation or other legacy paths
