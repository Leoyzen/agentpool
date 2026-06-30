## MODIFIED Requirements

### Requirement: RunHandle._message_queue replaces dual queue system

The dual queue system (`_post_turn_injections`, `_post_turn_prompts`, `_injection_locks`, auto-resume via `_trigger_auto_resume()` and `_process_queued_work()`) SHALL be replaced by `RunHandle._message_queue` — a single `list[str]` with `idle_event` wake mechanism.

- `RunHandle._message_queue` SHALL be a plain `list[str]` — no external dependency
- Messages are appended by `steer()` (when idle or non-native running) and `followup()` (always)
- Messages are drained between Turns: `copy()` → `clear()` → use as next Turn prompts
- `_trigger_auto_resume()` SHALL be deleted — RunHandle does not exit between Turns, so no resume is needed
- `_process_queued_work()` SHALL be deleted — `RunHandle.start()`'s inner loop handles queued messages
- `_post_turn_injections` and `_post_turn_prompts` SHALL be deleted — replaced by `RunHandle._message_queue`
- `PromptInjectionManager.queue()` and `.pop_queued()` SHALL emit `DeprecationWarning` in Phase 1-2, deleted in Phase 3
- `PromptInjectionManager.inject()` and `.consume()` SHALL be retained for tool-result augmentation in `ACPTurn.execute()`

#### Scenario: Follow-up message queued during active Turn
- **WHEN** `run_handle.followup(message)` is called while a Turn is executing
- **THEN** the message is appended to `_message_queue`
- **AND** `_idle_event` is NOT set (Turn is still running)
- **AND** after the Turn completes, `start()` drains `_message_queue` and creates a new Turn with the messages

#### Scenario: Steer message wakes idle RunHandle
- **WHEN** `run_handle.steer(message)` is called while RunHandle is idle
- **THEN** the message is appended to `_message_queue`
- **AND** `_idle_event.set()` wakes the RunHandle
- **AND** `start()` drains `_message_queue` and creates a new Turn

#### Scenario: No auto-resume needed
- **WHEN** a Turn completes and no messages are queued
- **THEN** RunHandle enters idle via `await self._idle_event.wait()`
- **AND** no `_trigger_auto_resume()` or `_process_queued_work()` is called
- **AND** RunHandle remains idle until `steer()`, `followup()`, or `close()` wakes it

#### Scenario: PromptInjectionManager tool-result augmentation retained
- **WHEN** a tool on a non-native agent calls `injection_manager.inject("context")` during a Turn
- **THEN** `injection_manager.consume()` is called by the tool hook
- **AND** the injected context is wrapped in `<injected-context>` XML and attached to the tool result
- **AND** this is separate from `RunHandle._message_queue`

## REMOVED Requirements

### Requirement: PydanticAI pending message queue replaces manual follow-up prompt queue for native agents only
**Reason**: The distinction between native and non-native follow-up handling is eliminated. `RunHandle._message_queue` handles all follow-up delivery uniformly. For native agents, `PendingMessageDrainCapability` handles in-turn drain (unchanged). Between Turns, `RunHandle._message_queue` handles all agent types.
**Migration**: `_post_turn_prompts` and `_post_turn_injections` dicts are deleted. `_trigger_auto_resume()` and `_process_queued_work()` are deleted. `flush_pending_to_queue()` is deleted. All follow-up delivery goes through `RunHandle._message_queue` + `idle_event` wake.
