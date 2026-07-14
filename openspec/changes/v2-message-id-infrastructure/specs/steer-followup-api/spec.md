## MODIFIED Requirements

### Requirement: TurnRunner exposes steer() and followup() with agent-type awareness

`TurnRunner` SHALL expose `steer()` and `followup()` methods that route messages based on agent type. For native agents, they SHALL call `pydantic_ai_run.enqueue()` with the appropriate priority. For non-native agents, they SHALL delegate to `PromptInjectionManager.inject()` / `PromptInjectionManager.queue()`.

- `steer(message, *, message_id=None)` SHALL map to `enqueue(priority='asap')` for native agents — the message is drained before the next LLM call via `PendingMessageDrainCapability.before_model_request()`. When `message` is a `list`, items SHALL be unpacked as `enqueue(*message)` for multimodal support.
- `followup(message, *, message_id=None)` SHALL map to `enqueue(priority='when_idle')` for native agents — the message is drained only when the agent would otherwise terminate, via `PendingMessageDrainCapability.after_node_run()` redirect. When `message` is a `list`, items SHALL be unpacked as `enqueue(*message)`.
- `message` parameter type SHALL be `str | list[Any]` — `str` for plain text, `list[Any]` for structured content (e.g. `[ImageUrl(...), "caption"]`). The pipeline carries structured content through without stringification.
- For non-native agents, `steer()` SHALL call `injection_manager.inject(message)` and `followup()` SHALL call `injection_manager.queue(message)`
- Agent type SHALL be detected via `agent.AGENT_TYPE` (ClassVar), NOT via `session.metadata.get("agent_type")` — the agent is already resolved in `_run_turn_unlocked()` and `_create_run()`
- `TurnRunner` SHALL access the active `AgentRun` via `run_handle.active_agent_run` (set by `RunExecutor`, not by `TurnRunner`)
- `TurnRunner.steer()` SHALL NOT silently drop messages when no active run exists for native agents — instead, it SHALL delegate to `receive_request(session_id, content, priority="steer")` to start a new run
- `TurnRunner.followup()` SHALL delegate to `receive_request(session_id, content, priority="followup")` when no active run exists for native agents (same as steer)
- `steer()` and `followup()` SHALL return `str | None` — the `message_id` on success, `None` on failure. When `message_id` parameter is provided, it SHALL be used; when `None`, a new UUID SHALL be auto-generated.
- `RunHandle.revoke(message_id: str) -> bool` SHALL revoke a pending steer/followup by `message_id`. Returns `True` if revoked, `False` if already delivered or unknown. This method SHALL delegate to `CommChannel.revoke()`.

#### Scenario: Native agent receives steer during active run

- **WHEN** `TurnRunner.steer(message)` is called on a native agent session with an active run
- **THEN** the system calls `pydantic_ai_run.enqueue(message, priority='asap')`
- **AND** the message is drained at the next `before_model_request` hook
- **AND** the agent processes the message in its next LLM call
- **AND** the return value is the `message_id` (a non-empty string)

#### Scenario: Native agent receives followup during active run

- **WHEN** `TurnRunner.followup(message)` is called on a native agent session with an active run
- **THEN** the system calls `pydantic_ai_run.enqueue(message, priority='when_idle')`
- **AND** the message remains queued while the agent processes tool calls
- **AND** when the agent would otherwise terminate, `PendingMessageDrainCapability.after_node_run()` drains the queue
- **AND** the run continues with an additional `ModelRequestNode`
- **AND** the return value is the `message_id` (a non-empty string)

#### Scenario: Non-native agent receives steer during active run

- **WHEN** `TurnRunner.steer(message)` is called on a non-native (ACP) agent session with an active run
- **THEN** the system calls `run_ctx.injection_manager.inject(message)`
- **AND** the message is consumed by `after_tool_execute` hooks
- **AND** the message is wrapped in `<injected-context>` XML and attached to the next tool result
- **AND** the return value is the `message_id` (a non-empty string)

#### Scenario: Non-native agent receives followup during active run

- **WHEN** `TurnRunner.followup(message)` is called on a non-native agent session with an active run
- **THEN** the system calls `run_ctx.injection_manager.queue(message)`
- **AND** the message is processed by the manual follow-up loop after the current turn completes
- **AND** the return value is the `message_id` (a non-empty string)

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

#### Scenario: Steer with explicit message_id

- **WHEN** `TurnRunner.steer(message, message_id="msg_custom_001")` is called
- **THEN** the resulting `Feedback.message_id` SHALL be `"msg_custom_001"`
- **AND** the return value SHALL be `"msg_custom_001"`
- **AND** no auto-generated UUID SHALL overwrite the provided value

#### Scenario: Steer with auto-generated message_id

- **WHEN** `TurnRunner.steer(message)` is called without `message_id` parameter
- **THEN** a new UUID SHALL be auto-generated for `Feedback.message_id`
- **AND** the return value SHALL be that UUID string

#### Scenario: Revoke pending steer (CommChannel layer)

- **WHEN** `RunHandle.revoke(message_id)` is called with a valid `message_id` in `ProtocolChannel._pending`
- **THEN** the system calls `CommChannel.revoke(message_id)`
- **AND** the pending feedback SHALL be removed from `_pending` and the queue
- **AND** no `user_message` notification SHALL be emitted for that `message_id` in the future
- **AND** the return value SHALL be `True`

#### Scenario: Revoke steer already enqueued to PydanticAI (PydanticAI layer)

- **WHEN** `steer()` called `agent_run.enqueue(message)` and tracked the `PendingMessage` references in `_enqueued[message_id]`
- **AND** `RunHandle.revoke(message_id)` is called before `before_model_request` drain consumes the message
- **THEN** the tracked `PendingMessage` objects SHALL be removed from `agent_run.pending_messages` via `list.remove(pm)`
- **AND** the return value SHALL be `True`
- **AND** the subsequent `before_model_request` drain SHALL NOT find the revoked message

#### Scenario: Revoke steer after PydanticAI drain (already consumed)

- **WHEN** `RunHandle.revoke(message_id)` is called after `_drain_by_priority()` has already consumed the `PendingMessage`
- **THEN** `list.remove(pm)` SHALL raise `ValueError` (caught internally)
- **AND** the return value SHALL be `True` (idempotent — message is no longer in queue)
- **AND** no exception SHALL propagate to the caller

#### Scenario: Revoke already-delivered steer

- **WHEN** `RunHandle.revoke(message_id)` is called with a `message_id` that has already been delivered
- **THEN** the return value SHALL be `False`
- **AND** no exception SHALL be raised

#### Scenario: Revoke unknown message_id

- **WHEN** `RunHandle.revoke(message_id)` is called with a `message_id` that does not exist in pending or delivered
- **THEN** the return value SHALL be `True` (idempotent — safe to retry after transport loss)

### Requirement: SessionController.receive_request() accepts steer/followup priority aliases

`SessionController.receive_request()` SHALL accept `priority="steer"` and `priority="followup"` as aliases for `"asap"` and `"when_idle"` respectively. The existing `"asap"`/`"when_idle"` values SHALL continue to work for backward compatibility.

- `priority="steer"` SHALL be internally mapped to `"asap"` for routing
- `priority="followup"` SHALL be internally mapped to `"when_idle"` for routing
- The method SHALL accept all four values (`"steer"`, `"followup"`, `"asap"`, `"when_idle"`)
- The method SHALL accept an optional `message_id: str | None = None` keyword parameter. When provided, it SHALL be passed to `steer()` or `followup()`. When `None`, the called method SHALL auto-generate.
- The return type SHALL change from `RunHandle | None` to `RunHandle | str | None` — `RunHandle` for new runs (idle session), `str` for steer/followup success (the `message_id`), `None` for failure or rejection.
- `SessionController.revoke_inject(session_id: str, message_id: str) -> bool` SHALL revoke a pending inject by `message_id` on the session's active run. Returns `False` if no active run or revoke fails.

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

#### Scenario: receive_request with message_id

- **WHEN** `receive_request(session_id, content, priority="steer", message_id="msg_001")` is called
- **THEN** `steer(content, message_id="msg_001")` SHALL be called
- **AND** the `message_id` SHALL propagate to the `Feedback` object

#### Scenario: revoke_inject on active session

- **WHEN** `revoke_inject(session_id, message_id)` is called on a session with an active run
- **THEN** the system delegates to `RunHandle.revoke(message_id)` on the active run
- **AND** returns the result of `revoke()`

#### Scenario: revoke_inject on idle session

- **WHEN** `revoke_inject(session_id, message_id)` is called on a session with no active run
- **THEN** the system returns `False`
- **AND** no exception is raised

#### Scenario: receive_request returns message_id for steer on busy session

- **WHEN** `receive_request(session_id, content, priority="steer", message_id="msg_001")` is called on a busy session
- **THEN** the return value SHALL be `"msg_001"` (the message_id string)
- **AND** the return type SHALL be `str`, not `RunHandle` or `None`

#### Scenario: receive_request returns RunHandle for new run on idle session

- **WHEN** `receive_request(session_id, content, priority="when_idle")` is called on an idle session
- **THEN** the return value SHALL be a `RunHandle` instance
- **AND** the `message_id` (if auto-generated) SHALL be available on the `RunHandle` for future reference

#### Scenario: receive_request returns None on failure

- **WHEN** `receive_request(session_id, content)` is called on a closing session
- **THEN** the return value SHALL be `None`
