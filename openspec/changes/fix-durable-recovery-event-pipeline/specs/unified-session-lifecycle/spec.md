## ADDED Requirements

### Requirement: EventProcessorContext SHALL be serialized and restored across elicitation resume boundaries

`OpenCodeEventBridge` SHALL serialize the current `EventProcessorContext` after each `StreamCompleteEvent` and `RunFailedEvent` via `set_session_context_data(session_id, ctx.serialize())`. On elicitation resume (same-process checkpoint → resume), `_before_consumer_loop()` SHALL restore the context from `get_session_context_data()` using `EventProcessorContext.deserialize()`. This wires up the existing `serialize()`/`deserialize()` infrastructure that was implemented but never activated.

**Scope**: This requirement covers **elicitation resume only** (same-process). Cross-process crash recovery (server restart) requires persisting `EventProcessorContext` to the session store — tracked as a follow-up change. `_resume_contexts` is an in-memory dict that does not survive server restart.

- After `StreamCompleteEvent`, the bridge SHALL call `set_session_context_data(session_id, serialized_context)`
- After `RunFailedEvent`, the bridge SHALL call `set_session_context_data(session_id, serialized_context)`
- `_before_consumer_loop()` SHALL check `get_session_context_data(session_id)` and restore the context if present (existing code at lines 267-286)
- On restore, `_message_registered` SHALL be set to `True` so the first event of the resumed turn does not trigger a spurious assistant registration
- The serialized context SHALL include `assistant_msg_id`, `assistant_msg`, model metadata, and per-turn accumulated state
- The serialized context SHALL NOT include `_steer_received` (it is `False` after `StreamCompleteEvent` when serialization occurs). If a crash happens during the steer split (after `_steer_received = True`, before `PartStartEvent`), the resumed context won't have `_steer_received = True`. This is an accepted edge case — the steer message sorts after the assistant message instead of between two.
- If serialization fails (non-serializable field), the bridge SHALL log an error and fall back to fresh context creation

#### Scenario: Context serialized after StreamCompleteEvent

- **WHEN** a turn completes with `StreamCompleteEvent`
- **THEN** `set_session_context_data(session_id, serialized_context)` is called
- **AND** the serialized context includes `assistant_msg_id`, model metadata, and per-turn state

#### Scenario: Context restored on session resume

- **WHEN** a checkpointed session is resumed
- **AND** `get_session_context_data(session_id)` returns serialized data
- **THEN** `_before_consumer_loop()` deserializes the context via `EventProcessorContext.deserialize()`
- **AND** `_message_registered` is set to `True`
- **AND** the restored `assistant_msg_id` is used for subsequent events

#### Scenario: Context serialized after RunFailedEvent

- **WHEN** a turn fails with `RunFailedEvent`
- **THEN** `set_session_context_data(session_id, serialized_context)` is called
- **AND** the serialized context captures the state at failure time

#### Scenario: Serialization failure falls back to fresh context

- **WHEN** `EventProcessorContext.serialize()` raises an exception
- **THEN** the bridge logs the error at `ERROR` level
- **AND** falls back to fresh context creation in `_before_consumer_loop()`
- **AND** does not crash the turn
