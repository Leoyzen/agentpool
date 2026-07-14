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

`ProtocolChannel` SHALL support revoking and replacing pending feedback by `message_id`. The feedback queue SHALL be upgraded from a plain `asyncio.Queue` to a `collections.deque` with ID-based tracking.

- `ProtocolChannel` SHALL maintain `_pending: dict[str, Feedback]` for O(1) lookup by `message_id`
- `ProtocolChannel` SHALL maintain `_revoked: set[str]` for tombstone tracking
- `ProtocolChannel` SHALL maintain `_delivered: set[str]` for already-delivered tracking
- `revoke(message_id: str) -> bool` SHALL remove the feedback from `_pending` and the queue, add `message_id` to `_revoked`, and return `True`. If the `message_id` is in `_delivered`, return `False`. If the `message_id` is unknown (not in `_pending` or `_delivered`), return `True` (idempotent).
- `replace(message_id: str, new_content: str) -> bool` SHALL update the `content` of the pending `Feedback` in-place, preserving queue position. Return `True` on success, `False` if already delivered or unknown.
- `deliver_feedback(feedback)` SHALL check `_revoked` before enqueuing — if the `message_id` is in `_revoked`, return `False`.
- `recv()` SHALL move the `message_id` from `_pending` to `_delivered` when dequeuing.

#### Scenario: Revoke pending feedback before delivery

- **WHEN** `revoke(message_id)` is called with a `message_id` in `_pending`
- **THEN** the `Feedback` SHALL be removed from `_pending` and the queue
- **AND** `message_id` SHALL be added to `_revoked`
- **AND** `recv()` SHALL NOT return that `Feedback`
- **AND** the return value SHALL be `True`

#### Scenario: Revoke already-delivered feedback

- **WHEN** `revoke(message_id)` is called with a `message_id` in `_delivered`
- **THEN** the return value SHALL be `False`
- **AND** no exception SHALL be raised

#### Scenario: Revoke unknown message_id (idempotent)

- **WHEN** `revoke(message_id)` is called with a `message_id` not in `_pending`, `_delivered`, or `_revoked`
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

### Requirement: CommChannel Protocol declares revoke and replace methods

The `CommChannel` Protocol in `lifecycle/protocols.py` SHALL declare `revoke(message_id: str) -> bool` and `replace(message_id: str, new_content: str) -> bool` method signatures.

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
