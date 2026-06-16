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