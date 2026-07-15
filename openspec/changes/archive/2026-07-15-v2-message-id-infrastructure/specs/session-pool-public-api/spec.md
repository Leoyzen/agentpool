## NEW Requirements

### Requirement: DeliveryMode enum provides unified delivery semantics

The system SHALL define a `DeliveryMode` enum in `lifecycle/types.py` with values `STEER = "steer"` and `QUEUE = "queue"`. The enum values SHALL match ACP v2 RFD #1261 `mode` parameter and OpenCode `SessionDelivery.Delivery` wire formats. `Feedback.mode` SHALL use the same string values so `DeliveryMode` values can be used directly in `Feedback` construction without conversion.

- `DeliveryMode.STEER` SHALL map to `RunHandle.steer()`, pydantic-ai `"asap"` drain priority, and ACP `"steer"` mode — mid-turn injection at the next safe break-point.
- `DeliveryMode.QUEUE` SHALL map to `RunHandle.followup()`, pydantic-ai `"when_idle"` drain priority, and ACP `"queue"` mode — buffered until the agent goes idle.
- PydanticAI's internal `"asap"` / `"when_idle"` drain hooks SHALL be implementation details — external callers use `DeliveryMode`, the mapping happens inside `SessionPool`.

#### Scenario: DeliveryMode values match wire formats

- **WHEN** `DeliveryMode.STEER.value` is inspected
- **THEN** it equals `"steer"`
- **AND** `DeliveryMode.QUEUE.value` equals `"queue"`
- **AND** `Feedback(mode=DeliveryMode.STEER).mode` equals `"steer"`
- **AND** `Feedback(mode=DeliveryMode.QUEUE).mode` equals `"queue"`

### Requirement: SessionPool.send_message() provides unified messaging API

The system SHALL expose `SessionPool.send_message(session_id, content, *, mode=DeliveryMode.QUEUE, message_id=None) -> str | None` as the primary public API for sending messages to sessions. It SHALL return the `message_id` on success (both new runs and steer/followup into active runs) and `None` on failure.

- `content` parameter SHALL accept `str | list[Any]` — `str` for plain text, `list[Any]` for structured/multimodal content. When `list[Any]`, content SHALL be stored as `Feedback.content_blocks`. When `str`, content SHALL be stored as `Feedback.content`.
- `mode=DeliveryMode.STEER` SHALL route to `RunHandle.steer()` when a run is active, or create a new run when idle.
- `mode=DeliveryMode.QUEUE` SHALL route to `RunHandle.followup()` when a run is active, or create a new run when idle.
- `message_id` parameter SHALL be optional — when `None`, auto-generated as `str(uuid4())`. When provided, used for idempotency and revoke tracking.
- `send_message()` SHALL internally call `SessionController._route_message()` — a new internal method that replaces the current `receive_request()` dispatch logic.

#### Scenario: send_message with STEER mode on active session

- **WHEN** `send_message(session_id, "urgent update", mode=DeliveryMode.STEER)` is called on a session with an active run
- **THEN** the system calls `RunHandle.steer("urgent update")` with auto-generated `message_id`
- **AND** returns the `message_id` string

#### Scenario: send_message with QUEUE mode on idle session

- **WHEN** `send_message(session_id, "hello", mode=DeliveryMode.QUEUE)` is called on an idle session
- **THEN** the system creates a new `RunHandle` and starts a new turn
- **AND** returns the `message_id` string

#### Scenario: send_message with structured content

- **WHEN** `send_message(session_id, [ImageUrl(...), "describe this"], mode=DeliveryMode.QUEUE)` is called
- **THEN** the `Feedback.content_blocks` field is populated with the list
- **AND** `Feedback.content` is empty string
- **AND** for native agents, `agent_run.enqueue(*content_blocks, priority="when_idle")` is called

### Requirement: SessionPool.run_agent() provides one-shot agent execution

The system SHALL expose `SessionPool.run_agent(agent: str, prompt: str, parent_session_id: str | None = None, **metadata) -> str` as a convenience method for one-shot agent execution. It SHALL create a session, send the prompt, wait for completion, close the session, and return the result text.

- The method SHALL create a session via `create_session(agent_name=agent, parent_session_id=parent_session_id, **metadata)`.
- The prompt SHALL be sent via `send_message(session_id, prompt, mode=DeliveryMode.QUEUE)`.
- The method SHALL wait for completion via `wait_for_completion(session_id)`.
- The session SHALL be closed in a `finally` block — ensuring cleanup even on error.
- Recursion depth SHALL NOT be enforced in v1 — relies on model-level self-limitation and `max_member_turns` in team_mode config. A warning SHALL be logged if nesting exceeds 3 levels (tracked via ContextVar).

#### Scenario: run_agent success path

- **WHEN** `run_agent("researcher", "find X")` is called
- **THEN** a new session is created with `agent_name="researcher"`
- **AND** the prompt is sent via `send_message(mode=QUEUE)`
- **AND** `wait_for_completion()` returns the agent's text output
- **AND** the session is closed
- **AND** the result text is returned to the caller

#### Scenario: run_agent error path

- **WHEN** `run_agent("researcher", "find X")` is called and the agent errors
- **THEN** the session is closed in the `finally` block
- **AND** the exception propagates to the caller

### Requirement: SessionPool.revoke_message() provides public revoke API

The system SHALL expose `SessionPool.revoke_message(session_id, message_id) -> bool` as the public API for revoking pending messages. It SHALL wrap `SessionController.revoke_inject()`.

- Returns `True` if the message was still pending and successfully revoked.
- Returns `False` if the message was already delivered or is unknown.
- A message CAN be revoked if it is still pending in the CommChannel queue or PydanticAI `pending_messages` list. Once consumed by the model, it is irreversible.

#### Scenario: revoke_message on pending feedback

- **WHEN** `revoke_message(session_id, msg_id)` is called on a message still in the CommChannel queue
- **THEN** the system removes it from the queue
- **AND** returns `True`

#### Scenario: revoke_message on delivered message

- **WHEN** `revoke_message(session_id, msg_id)` is called on a message already consumed by the model
- **THEN** the system returns `False`

### Requirement: SessionPool.wait_for_completion() blocks until run finishes

The system SHALL expose `SessionPool.wait_for_completion(session_id, timeout=None) -> str` that subscribes to the EventBus and waits for the session's current run to complete.

- On `StreamCompleteEvent`, SHALL extract and return the text content from the final message.
- On `RunErrorEvent`, SHALL raise `RunError` with the error message.
- On timeout (when `timeout` is provided), SHALL raise `asyncio.TimeoutError`.
- If `session_id` does not exist, SHALL raise `SessionNotFoundError`.

#### Scenario: wait_for_completion with successful run

- **WHEN** `wait_for_completion(session_id)` is called while a run is active
- **THEN** the system subscribes to EventBus for `session_id`
- **AND** waits until `StreamCompleteEvent` is received
- **AND** returns the text content from `event.message.content`

### Requirement: receive_request() is deprecated and delegates to send_message()

The system SHALL mark `SessionPool.receive_request()` as deprecated with `DeprecationWarning`. It SHALL delegate to `send_message()` with `priority` mapped to `DeliveryMode`.

- `priority="asap"` SHALL map to `DeliveryMode.STEER`.
- `priority="when_idle"` SHALL map to `DeliveryMode.QUEUE`.
- Unknown priority values SHALL emit an additional `DeprecationWarning` and default to `DeliveryMode.QUEUE`.
- The return type SHALL narrow from `RunHandle | None` to `str | None` (the `message_id`).
- Existing callers using `if receive_request(...):` SHALL continue to work because truthy `str` behaves identically to `True`.

### Requirement: DelegationService is deprecated and delegates to SessionPool.run_agent()

The system SHALL mark `DelegationService` and `RunLoopDelegationService` as deprecated with `DeprecationWarning`.

- `DelegationService.spawn_subagent(name, prompt)` SHALL delegate to `ctx.host.session_pool.run_agent(name, prompt, parent_session_id)`.
- `DelegationService.get_available_agents()` SHALL delegate to `list(ctx.agent_registry.agent_configs.keys())`.
- `RunLoopDelegationService.spawn_subagent()` SHALL delegate to `self._host.session_pool.run_agent(name, prompt, self._session_id)`.
- The `AgentContext.delegation` field SHALL remain for backward compatibility but SHALL be marked deprecated in its docstring.

### Requirement: SubagentCapability migrates to SessionPool.run_agent()

The system SHALL migrate `SubagentCapability` from `ctx.delegation.spawn_subagent()` to `ctx.host.session_pool.run_agent()`. The migration SHALL be internal — the `task` tool's external behavior SHALL remain unchanged.

- The `task` tool SHALL call `ctx.host.session_pool.run_agent(agent_name, prompt, parent_session_id=ctx.session_id)` instead of `ctx.delegation.spawn_subagent(agent_name, prompt)`.
- The `DelegationService` protocol and `RunLoopDelegationService` class SHALL emit `DeprecationWarning` when called, but SHALL still function correctly.

#### Scenario: SubagentCapability task tool after migration

- **WHEN** the `task` tool is invoked with `agent_or_team="researcher"` and `prompt="find X"`
- **THEN** the system calls `ctx.host.session_pool.run_agent("researcher", "find X", parent_session_id=ctx.session_id)`
- **AND** the tool returns the agent's text output
- **AND** no `DeprecationWarning` is emitted (the migration is internal)
