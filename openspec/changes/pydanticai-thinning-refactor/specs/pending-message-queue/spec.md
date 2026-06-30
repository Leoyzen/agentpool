## MODIFIED Requirements

### Requirement: PydanticAI pending message queue replaces manual follow-up prompt queue for native agents only
The system SHALL use PydanticAI's `PendingMessageDrainCapability` for follow-up prompt delivery on native agents. `RunExecutor` (native-agent turn driver) SHALL NOT maintain `_post_turn_prompts` or `_injection_locks` for follow-up prompts. `BaseAgent._run_stream_once()` SHALL NOT contain its own internal prompt continuation loop for native agents. `PromptInjectionManager.queue()`/`pop_queued()`/`flush_pending_to_queue()` SHALL NOT be called for native agents.

**CRITICAL**: `PromptInjectionManager.inject()`/`consume()` (tool result augmentation via `after_tool_execute`) is NOT replaced by PydanticAI's queue. This mechanism modifies tool results, not conversation messages. It SHALL be preserved for native agents.

**CRITICAL**: `PromptInjectionManager.queue()`/`pop_queued()`/`flush_pending_to_queue()` SHALL be preserved for ACP (non-native) agents that do not use PydanticAI's agent loop.

#### Scenario: Tool enqueues steering message on native agent
- **WHEN** a tool calls `ctx.enqueue(content, priority='asap')` during a native turn
- **THEN** PydanticAI's `PendingMessageDrainCapability` drains it before the next `ModelRequest`
- **AND** the message is injected into the active conversation

#### Scenario: External code enqueues follow-up message on native agent
- **WHEN** external code calls `pydantic_ai_run.enqueue(content, priority='when_idle')` while a native run is active
- **THEN** the message remains queued until the agent would otherwise terminate
- **AND** PydanticAI extends the run with an additional model request

#### Scenario: No manual auto-resume needed for native agents
- **WHEN** a follow-up message is queued after a native turn ends
- **THEN** PydanticAI's `after_node_run` hook automatically drains the queue
- **AND** no `_trigger_auto_resume()` or `_process_queued_work()` logic is executed
- **AND** no `PromptInjectionManager.queue()` or `pop_queued()` is called for native agents

#### Scenario: Tool result augmentation still works for native agents
- **WHEN** a tool calls `agent.inject_prompt("also check tests")` during a native turn
- **THEN** `PromptInjectionManager.inject()` stores the message
- **AND** the `Hooks` capability's `after_tool_execute` callback consumes it via `injection_manager.consume()`
- **AND** the injected context is added to the tool result (wrapped in `<injected-context>` tags)
- **AND** this is separate from PydanticAI's `enqueue()` conversation queue

#### Scenario: ACP agent still uses manual queue
- **WHEN** a follow-up message is queued for an ACP (non-native) agent
- **THEN** `PromptInjectionManager.queue()` and `pop_queued()` are used (manual queue preserved)
- **AND** `TurnRunner._process_queued_work()` drains the queue
- **AND** `PendingMessageDrainCapability` is not involved (ACP agents don't use PydanticAI's agent loop)
