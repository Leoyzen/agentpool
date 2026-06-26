## Why

RFC-0027 Phase 1 implemented basic Zed subagent compatibility (`SubagentSessionInfo` model, `_build_subagent_field_meta()`, `zed` display mode). However, Oracle evaluation (2026-06-26, 4 rounds of review) identified 10 critical gaps: `tool_call_id` disconnect, wrong `kind`, no completion notification, missing `_meta` on `ToolCallProgress`, no depth enforcement, no cancellation propagation, and 3 sites of duplicated 15-line `SpawnSessionStart` boilerplate. ACP v1.0.0 was released 2026-06-24 and Zed's ACP SDK is at `=1.0.0` — the wire protocol is stable and these fixes are now unblocked.

## What Changes

- `create_child_session()` in `context.py` auto-emits `SpawnSessionStart` with `tool_call_id` from `ctx.tool_call_id`, `depth` from `run_ctx.depth`, and `MAX_SUBAGENT_DEPTH` check — eliminating 3 × 15-line manual boilerplate (subagent_tools, workers ×2). Team/teamrun unaffected (uses `yield` pattern).
- `event_converter.py`: Fix `kind` from `"other"` to `"subagent"`; use `event.tool_call_id` instead of `uuid4()`; add `_meta` to `ToolCallProgress`; add `build_subagent_completed()` method.
- `handler.py`: Event + closure completion notification using mixin's existing `_consumer_done_events: dict[str, anyio.Event]`. On `_on_spawn_session_start`, grab `done_event` reference, capture parent context via closure, spawn background task that `await done_event.wait()` then emits `ToolCallProgress(completed)`. Race condition handled (None → immediate notification). Error handling with try/except. Memory cleanup via `_consumer_task_refs.remove(task)`.
- `handler.py`: Recursive cancellation via `_parent_of` lightweight mapping and `_cancel_subagents()` walk-tree.
- `handler.py`: `MAX_SUBAGENT_DEPTH=1` enforcement in `create_child_session()`.

## Capabilities

### New Capabilities

- `subagent-completion-notification`: Event + closure mechanism for detecting child session completion via `_consumer_done_events` and emitting `ToolCallProgress(status="completed")` to parent session's ACP client
- `subagent-auto-emit`: Framework-level auto-emission of `SpawnSessionStart` from `create_child_session()`, eliminating manual boilerplate and ensuring `tool_call_id` consistency

### Modified Capabilities

- `session-aware-event-routing`: Subagent `ToolCallStart` now uses `kind="subagent"` (was `"other"`), and `ToolCallProgress` carries `_meta.subagent_session_info` + `tool_name`
- `child-session-policy`: `MAX_SUBAGENT_DEPTH=1` enforced at `create_child_session()` level; recursive cancellation propagation via `_parent_of` mapping

## Impact

- **Files modified**: `context.py`, `event_converter.py`, `handler.py`, `subagent_tools.py`, `workers.py`
- **Files NOT modified**: `team.py`, `teamrun.py` (use `yield` pattern, don't call `create_child_session()`)
- **API changes**: `create_child_session()` gains `spawn_mechanism`, `description`, `tool_call_id` keyword params; `ACPEventConverter` gains `build_subagent_completed()` method; `ACPProtocolHandler` gains `_parent_of` dict and `_cancel_subagents()` method
- **Backward compatibility**: All changes gated behind `subagent_display_mode="zed"` — legacy mode behavior unchanged
- **Protocol**: ACP v1.0.0 wire protocol stable at version 1, no breaking changes
- **RFC**: Implements RFC-0039 (supersedes RFC-0027)
