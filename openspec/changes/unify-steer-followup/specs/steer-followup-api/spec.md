## ADDED Requirements

### Requirement: TurnRunner exposes steer() and followup() with agent-type awareness

`TurnRunner` SHALL expose `steer()` and `followup()` methods that route messages based on agent type. For native agents, they SHALL call `pydantic_ai_run.enqueue()` with the appropriate priority. For non-native agents, they SHALL delegate to `PromptInjectionManager.inject()` / `PromptInjectionManager.queue()`.

- `steer(message)` SHALL map to `enqueue(priority='asap')` for native agents — the message is drained before the next LLM call via `PendingMessageDrainCapability.before_model_request()`
- `followup(message)` SHALL map to `enqueue(priority='when_idle')` for native agents — the message is drained only when the agent would otherwise terminate, via `PendingMessageDrainCapability.after_node_run()` redirect
- For non-native agents, `steer()` SHALL call `injection_manager.inject(message)` and `followup()` SHALL call `injection_manager.queue(message)`
- Agent type SHALL be detected via `agent.AGENT_TYPE` (ClassVar), NOT via `session.metadata.get("agent_type")` — the agent is already resolved in `_run_turn_unlocked()` and `_create_run()`
- `TurnRunner` SHALL access the active `AgentRun` via `run_handle.active_agent_run` (set by `RunExecutor`, not by `TurnRunner`)
- `TurnRunner.steer()` SHALL NOT silently drop messages when no active run exists for native agents — instead, it SHALL delegate to `receive_request(session_id, content, priority="steer")` to start a new run
- `TurnRunner.followup()` SHALL delegate to `receive_request(session_id, content, priority="followup")` when no active run exists for native agents (same as steer)

#### Scenario: Native agent receives steer during active run
- **WHEN** `TurnRunner.steer(message)` is called on a native agent session with an active run
- **THEN** the system calls `pydantic_ai_run.enqueue(message, priority='asap')`
- **AND** the message is drained at the next `before_model_request` hook
- **AND** the agent processes the message in its next LLM call

#### Scenario: Native agent receives followup during active run
- **WHEN** `TurnRunner.followup(message)` is called on a native agent session with an active run
- **THEN** the system calls `pydantic_ai_run.enqueue(message, priority='when_idle')`
- **AND** the message remains queued while the agent processes tool calls
- **AND** when the agent would otherwise terminate, `PendingMessageDrainCapability.after_node_run()` drains the queue
- **AND** the run continues with an additional `ModelRequestNode`

#### Scenario: Non-native agent receives steer during active run
- **WHEN** `TurnRunner.steer(message)` is called on a non-native (ACP) agent session with an active run
- **THEN** the system calls `run_ctx.injection_manager.inject(message)`
- **AND** the message is consumed by `after_tool_execute` hooks
- **AND** the message is wrapped in `<injected-context>` XML and attached to the next tool result

#### Scenario: Non-native agent receives followup during active run
- **WHEN** `TurnRunner.followup(message)` is called on a non-native agent session with an active run
- **THEN** the system calls `run_ctx.injection_manager.queue(message)`
- **AND** the message is processed by the manual follow-up loop after the current turn completes

#### Scenario: Steer called on idle native agent
- **WHEN** `TurnRunner.steer(message)` is called on a native agent session with no active run
- **THEN** the system delegates to `receive_request(session_id, message, priority="steer")`
- **AND** a new run is created with the steer message

#### Scenario: Followup called on idle native agent
- **WHEN** `TurnRunner.followup(message)` is called on a native agent session with no active run
- **THEN** the system delegates to `receive_request(session_id, message, priority="followup")`
- **AND** a new run is created with the follow-up message

#### Scenario: Steer called on idle non-native agent
- **WHEN** `TurnRunner.steer(message)` is called on a non-native agent session with no active run
- **THEN** the system stores the message in `_post_turn_injections[session_id]`
- **AND** calls `_trigger_auto_resume()` to start a new run

#### Scenario: Followup called on idle non-native agent
- **WHEN** `TurnRunner.followup(message)` is called on a non-native agent session with no active run
- **THEN** the system stores the message in `_post_turn_prompts[session_id]`
- **AND** calls `_trigger_auto_resume()` to start a new run

### Requirement: SessionController.receive_request() accepts steer/followup priority aliases

`SessionController.receive_request()` SHALL accept `priority="steer"` and `priority="followup"` as aliases for `"asap"` and `"when_idle"` respectively. The existing `"asap"`/`"when_idle"` values SHALL continue to work for backward compatibility.

- `priority="steer"` SHALL be internally mapped to `"asap"` for routing
- `priority="followup"` SHALL be internally mapped to `"when_idle"` for routing
- The method SHALL accept all four values (`"steer"`, `"followup"`, `"asap"`, `"when_idle"`)

#### Scenario: Protocol handler sends steer request
- **WHEN** a protocol handler calls `receive_request(session_id, content, priority="steer")`
- **THEN** the system internally maps `"steer"` to `"asap"`
- **AND** routes through the same path as `priority="asap"`

#### Scenario: Protocol handler sends followup request
- **WHEN** a protocol handler calls `receive_request(session_id, content, priority="followup")`
- **THEN** the system internally maps `"followup"` to `"when_idle"`
- **AND** routes through the same path as `priority="when_idle"`

#### Scenario: Backward compatibility with asap/when_idle
- **WHEN** a protocol handler calls `receive_request(session_id, content, priority="asap")`
- **THEN** the system processes the request identically to `priority="steer"`
- **AND** no deprecation warning is emitted

### Requirement: TurnRunner._run_turn_unlocked() removes manual follow-up loop for native agents

For native agents, `_run_turn_unlocked()` SHALL NOT execute the manual follow-up prompt loop (`while has_queued(): pop_queued() + _run_stream_once()`). `PendingMessageDrainCapability.after_node_run()` SHALL handle follow-up continuation via graph-node redirection.

For non-native agents, the manual loop SHALL be preserved unchanged.

- `flush_pending_to_queue()` SHALL be gated behind `agent.AGENT_TYPE != "native"` in `_run_turn_unlocked()` — for native agents, unconsumed injections from tool augmentation are intentionally dropped (if not consumed by `after_tool_execute` hooks, it's a bug in the tool/hook chain)
- The `while has_queued()` loop in `_run_turn_unlocked()` SHALL be gated behind `agent.AGENT_TYPE != "native"` (using `agent.AGENT_TYPE`, not session metadata)
- `_process_queued_work()` SHALL gate only the manual follow-up loop behavior, NOT the draining of `_post_turn_injections` and `_post_turn_prompts` — these dicts are populated only by non-native `steer()`/`followup()` fallback (for native agents, `steer()`/`followup()` on idle delegate to `receive_request()` instead)
- `_post_turn_injections` and `_post_turn_prompts` SHALL be preserved for non-native agents

#### Scenario: Native agent turn completes with no follow-up
- **WHEN** a native agent's `_run_stream_once()` completes and no messages are in the pending queue
- **THEN** `PendingMessageDrainCapability` returns `End` from `after_node_run`
- **AND** the turn completes normally
- **AND** the manual follow-up loop is NOT executed

#### Scenario: Native agent turn has enqueued follow-up message
- **WHEN** a native agent's `_run_stream_once()` completes and a `when_idle` message is in `GraphAgentState.pending_messages`
- **THEN** `PendingMessageDrainCapability.after_node_run()` drains the queue
- **AND** returns a new `ModelRequestNode` (redirect)
- **AND** the agent continues with another iteration
- **AND** the manual follow-up loop is NOT executed (would be redundant)

#### Scenario: Non-native agent turn completes with queued prompts
- **WHEN** a non-native agent's `_run_stream_once()` completes and `injection_manager.has_queued()` is true
- **THEN** the manual follow-up loop executes as before
- **AND** each queued prompt is processed via `_run_stream_once()`
- **AND** `flush_pending_to_queue()` is called between iterations

### Requirement: inject_prompt() and queue_prompt() deprecated for native agents

`BaseAgent.inject_prompt()` and `BaseAgent.queue_prompt()` SHALL emit a `DeprecationWarning` when called for native agents, directing callers to use `steer()`/`followup()` or `SessionController.receive_request()` instead.

- For native agents, `inject_prompt()` SHALL delegate to `steer()` internally
- For native agents, `queue_prompt()` SHALL delegate to `followup()` internally
- For non-native agents, `inject_prompt()` and `queue_prompt()` SHALL continue working without deprecation
- The dual-write pattern (writing to both `injection_manager` and `session_pool`) SHALL be removed for native agents

#### Scenario: Native agent inject_prompt called
- **WHEN** `BaseAgent.inject_prompt(message)` is called on a native agent
- **THEN** a `DeprecationWarning` is emitted
- **AND** the call delegates to `steer(message)` internally
- **AND** the message is enqueued via `agent_run.enqueue(priority='asap')`

#### Scenario: Non-native agent inject_prompt called
- **WHEN** `BaseAgent.inject_prompt(message)` is called on a non-native agent
- **THEN** no deprecation warning is emitted
- **AND** the call proceeds through the existing `injection_manager.inject()` path

### Requirement: BaseAgent._run_stream_direct() skips manual loop for native agents

`BaseAgent._run_stream_direct()` SHALL skip the explicit `while has_queued()` loop for native agents. The loop SHALL be preserved for non-native agents. Note: the gating (`self.AGENT_TYPE == "native"`) already exists at `base_agent.py:1080` — this requirement documents the existing behavior rather than introducing a new change.

- For native agents, `_run_stream_direct()` SHALL call `_run_stream_once()` once and return (the `PendingMessageDrainCapability` handles continuation)
- For non-native agents, the `while has_queued()` loop SHALL remain unchanged

#### Scenario: Native agent _run_stream_direct called
- **WHEN** `_run_stream_direct()` is called for a native agent
- **THEN** the system calls `_run_stream_once()` once
- **AND** returns without looping on queued prompts
- **AND** follow-up is handled by `PendingMessageDrainCapability`

#### Scenario: Non-native agent _run_stream_direct called
- **WHEN** `_run_stream_direct()` is called for a non-native agent
- **THEN** the system calls `_run_stream_once()` and then enters the `while has_queued()` loop
- **AND** processes each queued prompt as before

### Requirement: RunHandle exposes active_agent_run for TurnRunner access

`RunHandle` SHALL gain an `active_agent_run: AgentRun | None` field. `RunExecutor` SHALL set this field when a native run starts (at `agentlet.iter()` context manager entry) and clear it in the `finally` block on completion. `TurnRunner.steer()`/`followup()` SHALL read `run_handle.active_agent_run` to call `enqueue()`.

- The field SHALL default to `None`
- `RunExecutor` SHALL set `run_handle.active_agent_run = agent_run` immediately after entering `async with agentlet.iter(...) as agent_run:`
- `RunExecutor` SHALL clear `run_handle.active_agent_run = None` in the `finally` block (before the `async with` exit)
- `TurnRunner` SHALL read `run_handle.active_agent_run` — if `None`, fall back to `receive_request()` for steer or `_post_turn_prompts` for followup
- This is the ONLY mechanism for `TurnRunner` to access the `AgentRun` — no `ContextVar` or callback pattern is used

#### Scenario: RunExecutor sets active_agent_run on run start
- **WHEN** `RunExecutor.execute()` begins a native agent run
- **THEN** it enters `async with agentlet.iter(...) as agent_run:`
- **AND** sets `run_handle.active_agent_run = agent_run`
- **AND** the agent_run is now accessible to `TurnRunner.steer()`/`followup()`

#### Scenario: RunExecutor clears active_agent_run on run completion
- **WHEN** a native agent run completes (normally or with error)
- **THEN** the `finally` block in `RunExecutor` clears `run_handle.active_agent_run = None`
- **AND** subsequent `steer()` calls fall back to `receive_request()`

### Requirement: NativeAgentHookManager keeps injection_manager.consume() for tool augmentation

`NativeAgentHookManager.as_capability()` SHALL keep the `after_tool_execute` hook that calls `injection_manager.consume()` for tool result augmentation. This is **not** replaced by `enqueue()` — it modifies tool results with `<injected-context>` XML, which is a different semantic from inserting conversation messages.

- `PromptInjectionManager.inject()`/`consume()` SHALL remain available for ALL agents (native and non-native)
- The `consume()` result is wrapped in `<injected-context>` XML and attached to the tool result as `additional_context`
- This mechanism is independent of the steer/followup API

#### Scenario: Tool injects context on native agent
- **WHEN** a tool calls `run_ctx.injection_manager.inject("also check tests")` on a native agent
- **THEN** `NativeAgentHookManager.after_tool_execute` consumes the injection
- **AND** the injected context is wrapped in `<injected-context>` XML
- **AND** the context is added to the tool result
- **AND** this is separate from any `enqueue()` call

### Requirement: _run_agentlet_core() uses next() loop

`_run_agentlet_core()` SHALL use explicit `agent_run.next(node)` calls instead of bare `async for node in agent_run:`. This applies to ALL code paths through `_run_agentlet_core()`. `async for` calls `__anext__` which does NOT invoke `_run_node_with_hooks`, so `after_node_run` capability hooks (including `PendingMessageDrainCapability.when_idle` drain) never fire.

`_run_agentlet_core()` SHALL preserve the existing dual-streaming branches:
- With `event_bus`: events go directly to `event_queue`
- Without `event_bus`: uses `merge_queue_into_iterator`

The fix SHALL mirror the `next()` pattern from `RunExecutor.execute()`. The existing `_run_turn_unlocked()` SHALL continue calling `_run_stream_once()` (not `RunExecutor`), because `_run_stream_once()` handles prompt conversion, history resolution, pre-run hooks, and event dispatch that `RunExecutor` does not.

`RunExecutor.execute()` is NOT affected by this change — it already uses `next()` correctly.

#### Scenario: All native agent streams fire after_node_run hooks
- **WHEN** any native agent streams via `_run_agentlet_core()`
- **THEN** the system uses `agent_run.next(node)` in a loop (not `async for`)
- **AND** `after_node_run` hooks fire after each node
- **AND** `PendingMessageDrainCapability` drains `when_idle` messages at `after_node_run`
- **AND** the run continues with a redirect if messages exist