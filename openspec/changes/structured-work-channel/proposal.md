## Why

Background tasks spawned during an agent turn (e.g., code review, web search) often complete after the turn has ended. The current `steer()` and `followup()` mechanisms rely on TOCTOU-prone `is None` checks on `session.current_run_id` and `run_handle.active_agent_run` to decide whether a message should be injected into the active turn or queued for later. This race condition causes background task completion messages to be processed in a separate `run_loop` that the ACP client never sees (it already received `turn_complete`). The turn lifecycle fundamentally doesn't reflect reality: the turn ends before all work spawned during it is done.

## What Changes

- **New structured work channel**: Replace `_post_turn_injections`/`_post_turn_prompts` dicts with a `anyio.create_memory_object_stream` on `SessionState`, unifying steer/followup/background-task messages into a single ordered channel with backpressure.
- **Explicit turn state machine**: Replace TOCTOU `current_run_id is None` + `active_agent_run is None` branching with a typed `TurnState` enum (`IDLE`, `BOOTING`, `RUNNING`, `TEARDOWN`), eliminating the implicit "window" states where code cannot correctly determine what to do.
- **`run_loop` consumes from the work stream**: Instead of a fixed-iteration auto-resume loop that polls dictionaries, `run_loop` blocks on `work_receive.receive()` with timeout, allowing natural extension of the turn lifecycle to cover background task completion.
- **Remove dead code**: The `_post_turn_injections`/`_post_turn_prompts` dicts, `_safe_auto_resume`, and `_trigger_auto_resume` become unnecessary once the work stream is in place.
- **Red-flag test passes**: `test_background_task_wakeup_within_turn` (already committed) stops failing.

## Capabilities

### New Capabilities
- `structured-work-channel`: Per-session `anyio.MemoryObjectStream[WorkItem]` for all post-turn message delivery (steer, followup, background task notifications), with typed WorkItem union.
- `turn-state-machine`: Explicit `TurnState` enum eliminating TOCTOU race conditions in steer/followup routing.

### Modified Capabilities
- *(none — this is entirely new infrastructure, no existing spec behavior changes)*

## Impact

- **Core module** (`src/agentpool/orchestrator/core.py`): ~100 lines changed. `SessionState` gains `work_send`/`work_receive` and `turn_state` fields. `steer()`/`followup()` simplified to single-channel pattern. `run_loop()` rewritten to consume from work stream. `_process_queued_work` simplified. `_safe_auto_resume`, `_trigger_auto_resume` removed.
- **ACP event converter** (`acp_server/event_converter.py`): No changes — the fix is protocol-agnostic.
- **TurnRunner constructor** (`core.py`): `enable_auto_resume` parameter may change behavior (stream consumption replaces polling).
- **Test files**: Red-flag test already exists. Existing steer/followup/auto-resume tests need minor adaptation for the new channel pattern.
