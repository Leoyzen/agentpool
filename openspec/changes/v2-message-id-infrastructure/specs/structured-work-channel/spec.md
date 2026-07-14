## MODIFIED Requirements

### Requirement: Background task registration via pending_background_tasks counter

Tools that spawn background tasks SHALL increment `run_ctx.pending_background_tasks` before spawning and decrement it in `finally` when the task completes. The `background_tasks_complete` asyncio.Event SHALL be initially set (via custom factory, not `default_factory=asyncio.Event` which creates an unset event) and cleared when counter > 0 and set when counter returns to 0. A `steer_callback` on `AgentRunContext` SHALL provide tools with a path to call `steer()` without direct `TurnRunner` access.

#### Scenario: Tool increments on spawn

- **WHEN** a tool spawns a background task
- **THEN** `run_ctx.pending_background_tasks` SHALL be incremented by 1 before `asyncio.create_task()`
- **AND** `run_ctx.background_tasks_complete` SHALL be cleared

#### Scenario: Tool decrements on completion

- **WHEN** a background task completes (success, error, or cancellation)
- **THEN** `run_ctx.pending_background_tasks` SHALL be decremented by 1 in a `finally` block
- **AND** if counter reaches 0, `run_ctx.background_tasks_complete` SHALL be set

#### Scenario: Counter defaults to 0

- **WHEN** an `AgentRunContext` is created
- **THEN** `pending_background_tasks` SHALL be 0
- **AND** `background_tasks_complete` SHALL be set (via custom factory `_create_set_event()`, NOT `default_factory=asyncio.Event` which creates an unset event)
- **AND** `steer_callback` SHALL be None (set by `TurnRunner` when creating the `RunHandle`)

## ADDED Requirements

### Requirement: ProtocolChannel supports revoke and replace for pending feedback

`ProtocolChannel` SHALL support revoking and replacing pending feedback by `message_id`. The feedback queue SHALL be upgraded from a plain `asyncio.Queue` to a `collections.deque` with ID-based tracking. Revoke SHALL operate at two layers: the CommChannel feedback queue (for undelivered feedback) and the PydanticAI `pending_messages` list (for already-enqueued steer messages).

- `ProtocolChannel` SHALL maintain `_pending: dict[str, Feedback]` for O(1) lookup by `message_id`
- `ProtocolChannel` SHALL maintain `_revoked: set[str]` for tombstone tracking
- `ProtocolChannel` SHALL maintain `_delivered: set[str]` for already-delivered tracking
- `ProtocolChannel` SHALL maintain `_enqueued: dict[str, list[PendingMessage]]` for tracking steer messages that have been enqueued to PydanticAI's `agent_run.pending_messages` list. Key is `message_id`, value is the list of `PendingMessage` references appended by `enqueue()`.
- `revoke(message_id: str) -> bool` SHALL:
  1. Check `_pending` — if found, remove from `_pending` and queue, add to `_revoked`, return `True`
  2. Check `_enqueued` — if found, remove each `PendingMessage` from `agent_run.pending_messages` via `list.remove(pm)` (identity comparison). Catch `ValueError` (already drained). Remove from `_enqueued`, return `True`
  3. Check `_delivered` — if found, return `False` (already delivered and consumed)
  4. Otherwise return `True` (idempotent unknown)
- `replace(message_id: str, new_content: str | list[Any]) -> bool` SHALL update the `content` (when `new_content` is `str`) or `content_blocks` (when `new_content` is `list[Any]`) of the pending `Feedback` in-place, preserving queue position. Return `True` on success, `False` if already delivered, enqueued, or unknown. Replace only works at the CommChannel layer (before `enqueue()`).
- `deliver_feedback(feedback)` SHALL check `_revoked` before enqueuing — if the `message_id` is in `_revoked`, return `False`.
- `recv()` SHALL move the `message_id` from `_pending` to `_delivered` when dequeuing.
- `_track_enqueued(message_id: str, items: list[PendingMessage])` SHALL store the `PendingMessage` references in `_enqueued[message_id]`. Called by `RunHandle.steer()` after `agent_run.enqueue()`.

#### Scenario: Revoke pending feedback before delivery (CommChannel layer)

- **WHEN** `revoke(message_id)` is called with a `message_id` in `_pending`
- **THEN** the `Feedback` SHALL be removed from `_pending` and the queue
- **AND** `message_id` SHALL be added to `_revoked`
- **AND** `recv()` SHALL NOT return that `Feedback`
- **AND** the return value SHALL be `True`

#### Scenario: Revoke steer message already enqueued to PydanticAI (PydanticAI layer)

- **WHEN** `steer()` calls `agent_run.enqueue(message)` and tracks the `PendingMessage` references in `_enqueued[message_id]`
- **AND** `revoke(message_id)` is called before `before_model_request` drain
- **THEN** each tracked `PendingMessage` SHALL be removed from `agent_run.pending_messages` via `list.remove(pm)`
- **AND** `message_id` SHALL be removed from `_enqueued`
- **AND** the return value SHALL be `True`
- **AND** the subsequent `before_model_request` drain SHALL NOT find the revoked `PendingMessage`

#### Scenario: Revoke steer message after PydanticAI drain (already consumed)

- **WHEN** `revoke(message_id)` is called with a `message_id` in `_enqueued`
- **AND** `_drain_by_priority()` has already consumed the `PendingMessage` from `agent_run.pending_messages`
- **THEN** `list.remove(pm)` SHALL raise `ValueError` (caught)
- **AND** the return value SHALL be `True` (idempotent — message is no longer in queue)
- **AND** no exception SHALL propagate

#### Scenario: Revoke already-delivered feedback

- **WHEN** `revoke(message_id)` is called with a `message_id` in `_delivered`
- **THEN** the return value SHALL be `False`
- **AND** no exception SHALL be raised

#### Scenario: Revoke unknown message_id (idempotent)

- **WHEN** `revoke(message_id)` is called with a `message_id` not in `_pending`, `_delivered`, `_revoked`, or `_enqueued`
- **THEN** the return value SHALL be `True`
- **AND** no state change SHALL occur

#### Scenario: Revoke already-revoked message_id (idempotent)

- **WHEN** `revoke(message_id)` is called with a `message_id` already in `_revoked`
- **THEN** the return value SHALL be `True`
- **AND** no state change SHALL occur

#### Scenario: Replace pending feedback content

- **WHEN** `replace(message_id, new_content)` is called with a `message_id` in `_pending`
- **THEN** the `Feedback.content` SHALL be updated to `new_content`
- **AND** the queue position SHALL be preserved
- **AND** the return value SHALL be `True`

#### Scenario: Replace already-enqueued feedback returns False

- **WHEN** `replace(message_id, new_content)` is called with a `message_id` in `_enqueued`
- **THEN** the return value SHALL be `False`
- **AND** no exception SHALL be raised
- **AND** the `PendingMessage` in PydanticAI's queue SHALL NOT be modified

#### Scenario: Replace already-delivered feedback

- **WHEN** `replace(message_id, new_content)` is called with a `message_id` in `_delivered`
- **THEN** the return value SHALL be `False`
- **AND** no exception SHALL be raised

#### Scenario: Deliver feedback after revoke rejection

- **WHEN** `deliver_feedback(feedback)` is called with a `message_id` in `_revoked`
- **THEN** the return value SHALL be `False`
- **AND** the feedback SHALL NOT be enqueued

#### Scenario: recv marks feedback as delivered

- **WHEN** `recv()` dequeues a `Feedback` with `message_id="msg_001"`
- **THEN** `"msg_001"` SHALL be moved from `_pending` to `_delivered`
- **AND** subsequent `revoke("msg_001")` SHALL return `False`

#### Scenario: _track_enqueued stores PendingMessage references

- **WHEN** `steer()` calls `agent_run.enqueue(message)` and then `_track_enqueued(message_id, new_items)`
- **THEN** `_enqueued[message_id]` SHALL contain the `PendingMessage` references
- **AND** `revoke(message_id)` SHALL be able to remove them from `agent_run.pending_messages`

### Requirement: CommChannel Protocol declares revoke and replace methods

The `CommChannel` Protocol in `lifecycle/protocols.py` SHALL declare `revoke(message_id: str) -> bool` and `replace(message_id: str, new_content: str | list[Any]) -> bool` method signatures.

- `DirectChannel` SHALL implement `revoke()` returning `False` (no feedback queue)
- `DirectChannel` SHALL implement `replace()` returning `False` (no feedback queue)
- `ProtocolChannel` SHALL implement both with real logic per the `ProtocolChannel supports revoke and replace` requirement

#### Scenario: DirectChannel revoke returns False

- **WHEN** `DirectChannel.revoke(message_id)` is called
- **THEN** the return value SHALL be `False`
- **AND** no exception SHALL be raised

#### Scenario: DirectChannel replace returns False

- **WHEN** `DirectChannel.replace(message_id, new_content)` is called
- **THEN** the return value SHALL be `False`
- **AND** no exception SHALL be raised

### Requirement: RunHandle idle loop and drain carry content_blocks from Feedback

The `_idle_loop()` and `_drain_events()` methods in `RunHandle` SHALL read both `content` and `content_blocks` from `Feedback` when draining `ProtocolChannel.recv()`. The `_message_queue` type SHALL change from `list[str]` to `list[str | list[Any]]` to support structured content.

- `_idle_loop()` SHALL append `fb.content_blocks` to `_message_queue` when `content_blocks` is not `None`, else append `fb.content`
- `_drain_events()` SHALL append `fb.content_blocks` to `_message_queue` (for non-steer feedback) or to `feedback_steer` (for steer feedback) when `content_blocks` is not `None`, else append `fb.content`
- `_execute_turn()` SHALL accept `current_prompts: list[str | list[Any]]` — each prompt is either a plain text string or a list of structured content blocks
- For native agents: when a prompt is `list[Any]`, `_execute_turn()` SHALL pass it to the agent's turn as structured content (e.g. `enqueue(*content_blocks)`); when `str`, pass as plain text

#### Scenario: _idle_loop drains Feedback with content_blocks

- **WHEN** `_idle_loop()` calls `recv()` and gets a `Feedback` with `content_blocks=[{"type": "image", ...}, "caption"]`
- **THEN** `_message_queue` SHALL receive `[{"type": "image", ...}, "caption"]` (the list, not the string)
- **AND** `fb.content` (the text fallback) SHALL NOT be appended

#### Scenario: _idle_loop drains Feedback with content only

- **WHEN** `_idle_loop()` calls `recv()` and gets a `Feedback` with `content="hello"` and `content_blocks=None`
- **THEN** `_message_queue` SHALL receive `"hello"` (the string)
- **AND** the behavior SHALL be identical to the current implementation (backward compatible)

#### Scenario: _execute_turn receives list prompt

- **WHEN** `_execute_turn()` receives `current_prompts=[{"type": "image", ...}, "caption"]` (a list)
- **THEN** for native agents, the turn SHALL pass the list items to `agent_run` as structured content
- **AND** for non-native agents, the turn SHALL pass the list to the injection path as-is
- **AND** no exception SHALL be raised
