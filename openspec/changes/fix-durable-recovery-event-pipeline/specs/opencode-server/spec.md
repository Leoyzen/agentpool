## ADDED Requirements

### Requirement: _message_registered SHALL reset on turn completion

`OpenCodeEventBridge._handle_event()` SHALL set `_message_registered[session_id] = False` when processing `StreamCompleteEvent` and `RunFailedEvent`. This ensures the next turn starts with a fresh assistant message registration, eliminating false "Finalizing incomplete turn" warnings.

- On `StreamCompleteEvent`, after `_finalize_assistant_time()` and `_persist_assistant_message()`, the bridge SHALL set `_message_registered[session_id] = False`
- On `RunFailedEvent`, the bridge SHALL set `_message_registered[session_id] = False`
- The flag SHALL NOT be reset in `_finalize_assistant_time()` (it is called from both turn-completion and turn-start paths)
- The flag SHALL NOT be reset in the `RunStartedEvent` handler's D1 block (it is already set to `False` before the D1 block, and resetting there would be redundant)

#### Scenario: _message_registered resets after StreamCompleteEvent

- **WHEN** a turn completes with `StreamCompleteEvent`
- **THEN** `_message_registered[session_id]` is set to `False`
- **AND** the next `RunStartedEvent` does NOT trigger the D1 "finalize incomplete turn" block
- **AND** the next turn's assistant message is registered fresh

#### Scenario: _message_registered resets after RunFailedEvent

- **WHEN** a turn fails with `RunFailedEvent`
- **THEN** `_message_registered[session_id]` is set to `False`
- **AND** the next turn starts with a fresh assistant message registration

#### Scenario: No false "Finalizing incomplete turn" warning on turn 2

- **WHEN** turn 1 completes normally with `StreamCompleteEvent`
- **AND** the user sends a new message to start turn 2
- **THEN** `RunStartedEvent` for turn 2 finds `_message_registered=False`
- **AND** the D1 block is NOT entered
- **AND** no "Finalizing incomplete turn" warning is logged

### Requirement: Protocol-sourced user messages SHALL emit PartUpdatedEvent

`EventProcessor._process_user_message_inserted()` SHALL unconditionally yield `PartUpdatedEvent` for each part of a user message, regardless of the `source` field. Previously, protocol-sourced messages (`source="protocol"`) only yielded `MessageUpdatedEvent` and relied on the TUI's initial `sync.session.sync()` to load parts from the DB. However, the TUI calls `sync()` only ONCE per session (`fullSyncedSessions` set prevents re-sync). New user messages after `sync()` have no parts source — the TUI has NO optimistic mechanism (`submitInner()` sends REST and clears input without adding to store), and `message.updated` SSE events only update metadata (not parts). Without `PartUpdatedEvent`, `store.part[messageID]` is empty and `<Show when={text()}>` renders nothing.

- The method SHALL ALWAYS yield `MessageUpdatedEvent.create(user_message)` (existing behavior)
- The method SHALL ALWAYS yield `PartUpdatedEvent` for each part in the message, regardless of `source`
- **TUI mechanism**: The TUI has NO `replayedParts` deduplication (that exists only in the CLI's `stream.transport.ts`). The TUI app stores parts in `store.part[messageID]`, populated by `message.part.updated` SSE events or initial `sync()`. For NEW user messages (after `sync()`), SSE is the ONLY parts source — no duplicate risk.
- **Part ID alignment**: For historical messages loaded by `sync()` AND replayed via SSE, part IDs MUST match to avoid duplicates. Task P1.0 SHALL verify that `_deserialize_part()` preserves the original part ID from `meta` so it matches the DB-stored part. If IDs differ, an additional fix to align part IDs is required.
- The `source` field SHALL be preserved for informational purposes but SHALL NOT affect event emission

#### Scenario: Protocol message emits PartUpdatedEvent

- **WHEN** a protocol-sourced user message (`source="protocol"`) is processed
- **THEN** `MessageUpdatedEvent` is yielded for the user message
- **AND** `PartUpdatedEvent` is yielded for each part in the message
- **AND** the TUI receives all parts via SSE and stores them in `store.part[messageID]`
- **AND** the `<Show when={text()}>` renders the user message content

#### Scenario: Non-protocol message emits PartUpdatedEvent (unchanged)

- **WHEN** a non-protocol user message (`source="internal"`) is processed
- **THEN** `MessageUpdatedEvent` is yielded for the user message
- **AND** `PartUpdatedEvent` is yielded for each part (existing behavior, no change)

#### Scenario: New user message after sync() displays correctly (primary fix)

- **WHEN** the TUI has completed initial `sync()` (loaded historical messages with parts)
- **AND** the user submits a new message
- **THEN** the TUI sends REST and clears input (no optimistic add)
- **AND** SSE delivers `message.updated` (metadata) and `message.part.updated` (parts)
- **AND** `store.part[messageID]` is populated with parts from SSE
- **AND** the user message content is visible in the TUI

#### Scenario: Part ID mismatch causes duplicate rendering for historical messages (known risk)

- **WHEN** the TUI has loaded parts from the DB via `sync()` with DB-generated part IDs
- **AND** SSE delivers `PartUpdatedEvent` with `meta`-reconstructed part IDs that differ from DB IDs
- **THEN** the TUI has BOTH sets of parts in `store.part[messageID]` (no deduplication in TUI app)
- **AND** the parts ARE rendered twice
- **NOTE** This scenario only affects historical messages replayed via SSE, NOT new messages. Task P1.0 MUST verify part ID alignment before removing the guard. If mismatch is confirmed, fix `_deserialize_part()` to preserve original part IDs.
