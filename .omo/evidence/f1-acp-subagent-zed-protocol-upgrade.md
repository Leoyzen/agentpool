# F1: Plan Compliance Audit — acp-subagent-zed-protocol-upgrade

**Date:** 2026-06-27
**Auditor:** Automated (F1 verification wave)
**Verdict:** ✅ APPROVE

## Summary

All 67 OpenSpec tasks (1.1-1.6, 2.1-2.4, 3.1-3.5, 4.1-4.9, 5.1-5.3, 7.1-7.8, 8.1-8.18, 9.1-9.14) are implemented and verified against source code. All 12 plan todos (T1-T12) are marked `[x]`.

## Verification Method

Cross-referenced each OpenSpec task against:
1. The plan's `[x]` completion markers in `.omo/plans/acp-subagent-zed-protocol-upgrade.md`
2. Actual source code in 9 files
3. Test files for tasks 8.1-8.18 and 9.1-9.14

## Task-by-Task Verification

### Section 1: Framework-Level Auto-Emit (context.py) — 6/6 ✅

| Task | Status | Evidence |
|------|--------|----------|
| 1.1 | ✅ | `create_child_session()` has `spawn_mechanism`, `description`, `tool_call_id` keyword params at `context.py:278-280` |
| 1.2 | ✅ | `MAX_SUBAGENT_DEPTH = 5` at `context.py:58`, `SubagentDepthError` class at `context.py:62` |
| 1.3 | ✅ | Depth check `child_depth = self.run_ctx.depth + 1` + `SubagentDepthError` raise at `context.py:342-346` |
| 1.4 | ✅ | `SpawnSessionStart(...)` constructed with `tool_call_id`, `depth`, `spawn_mechanism`, `description` at `context.py:352-361` |
| 1.5 | ✅ | `await self.events.emit_event(spawn_event)` at `context.py:362` (NOT `self.node._events`) |
| 1.6 | ✅ | No `getattr` in `create_child_session()`. Uses `self.tool_call_id`, `self.run_ctx.depth` directly. `events` property uses `self.run_ctx.event_bus if self.run_ctx else None` (line 235). `get_session_state` uses `self.node.agent_pool` (line 207). |

### Section 2: Simplify Call Sites — 4/4 ✅

| Task | Status | Evidence |
|------|--------|----------|
| 2.1 | ✅ | `subagent_tools.py:207` — calls `create_child_session()` with `spawn_mechanism`, `description`, `tool_call_id`. Comment: "SpawnSessionStart is auto-emitted by create_child_session()" |
| 2.2 | ✅ | `workers.py:152` — same pattern with `spawn_mechanism="task"`, `description`, `tool_call_id` |
| 2.3 | ✅ | `workers.py:257` — same pattern |
| 2.4 | ✅ | team.py/teamrun.py NOT modified — verified by `test_team_py_uses_yield_spawn_pattern` test asserting `yield SpawnSessionStart(` in source and `create_child_session` NOT in source |

### Section 3: Event Converter Fixes (event_converter.py) — 5/5 ✅

| Task | Status | Evidence |
|------|--------|----------|
| 3.1 | ✅ | `kind="subagent"` at `event_converter.py:693` (was `"other"`) |
| 3.2 | ✅ | `tool_call_id = event.tool_call_id or str(uuid.uuid4())` at `event_converter.py:679` |
| 3.3 | ✅ | `_meta` with `subagent_session_info` + `tool_name` passed to `ToolCallStart` via `_build_subagent_field_meta()` at line 681-683 and line 695. Also passed to `ToolCallProgress` in `build_subagent_completed()` at line 305-309. |
| 3.4 | ✅ | `build_subagent_completed()` method at `event_converter.py:287-310` |
| 3.5 | ✅ | `SubagentRunInfo(child_session_id=..., run_mode=..., display_name=...)` on `ToolCallStart` at `event_converter.py:696-700` |

### Section 4: Event + Closure Completion Notification (handler.py) — 9/9 ✅

| Task | Status | Evidence |
|------|--------|----------|
| 4.1 | ✅ | `_parent_of: dict[str, str] = {}` in `__init__` at `handler.py:76` |
| 4.2 | ✅ | `done_event = self._consumer_done_events.get(child_sid)` at `handler.py:130` |
| 4.3 | ✅ | `_notify_completed()` async method at `handler.py:148-190` — calls `converter.build_subagent_completed()` + `client.session_update()` |
| 4.4 | ✅ | `done_event is None` race at `handler.py:140-146` — immediate `_notify_completed()` + `_parent_of.pop()` |
| 4.5 | ✅ | `_await_child_and_notify()` closure at `handler.py:192-229` — `await done_event.wait()` → `_parent_of.pop()` → `_notify_completed()` |
| 4.6 | ✅ | `ConnectionResetError`/`BrokenPipeError` caught at line 215, generic `Exception` caught at line 220 |
| 4.7 | ✅ | `finally:` block with `contextlib.suppress(ValueError): self._consumer_task_refs.remove(task)` at lines 225-229 |
| 4.8 | ✅ | `self._parent_of[child_sid] = session_id` at line 129, before closure task starts |
| 4.9 | ✅ | `asyncio.ensure_future()` at line 132, `self._consumer_task_refs.append(task)` at line 139 |

### Section 5: Recursive Cancellation (handler.py) — 3/3 ✅

| Task | Status | Evidence |
|------|--------|----------|
| 5.1 | ✅ | `_cancel_subagents(parent_sid)` at `handler.py:572-590` — walks `_parent_of` tree, recursively `stop_event_consumer` each child |
| 5.2 | ✅ | Pop before recursing: `self._parent_of.pop(child_sid, None)` at line 588 |
| 5.3 | ✅ | Wired into `close_session()` at `handler.py:606`: `await self._cancel_subagents(session_id)` |

### Section 7: Background Task Completion (context.py, run_executor.py, core.py) — 8/8 ✅

| Task | Status | Evidence |
|------|--------|----------|
| 7.1 | ✅ | `child_done_events: dict[str, anyio.Event]` at `context.py:127`. No `pending_background_tasks` or `background_tasks_complete` fields exist. Uses `anyio.Event` (not `asyncio.Event`). |
| 7.2 | ✅ | `_create_set_event()` function removed — grep returns no matches in `src/` |
| 7.3 | ✅ | In `create_child_session()`: `anyio.Event()` + `run_ctx.child_done_events[child_sid] = done_event` at `context.py:363-364`. `run_ctx is None` handled via `if self.run_ctx is not None:` guard at line 341. |
| 7.4 | ✅ | `complete_background_task()` at `context.py:136-158` — steer_callback first (skip if None + warning), then `.pop(child_session_id, None)` + set, catches exceptions |
| 7.5 | ✅ | RunExecutor at `run_executor.py:385-396`: `bool(run_ctx.child_done_events)`, `list(run_ctx.child_done_events.values())` snapshot, `run_ctx.child_done_events.clear()` |
| 7.6 | ✅ | `close_session()` at `core.py:3168-3173`: `cancelled = True`, `for ev in list(...child_done_events.values()): ev.set()`, `.clear()` |
| 7.7 | ✅ | `_run_turn_unlocked` finally at `core.py:2077-2086`: checks `_session.parent_session_id`, looks up parent session + run handle, `child_done_events.pop(key, None)`, sets event if not None |
| 7.8 | ✅ | `test_background_task_wakeup.py` uses `child_done_events` (8 refs). `test_session_lifecycle.py` uses `child_done_events` (6 refs). `test_steer_followup.py` has no refs (never had any). No `pending_background_tasks` or `background_tasks_complete` in any test file. |

### Section 8: Background Task Completion Tests — 18/18 ✅

All tests in `tests/orchestrator/test_child_done_events.py`:

| Task | Test Name | Status |
|------|-----------|--------|
| 8.1 | `test_create_child_session_registers_done_event` | ✅ |
| 8.2 | `test_complete_background_task_calls_steer_before_set` | ✅ |
| 8.3 | `test_complete_background_task_unknown_child_still_calls_steer` | ✅ |
| 8.4 | `test_complete_background_task_no_steer_callback` | ✅ |
| 8.5 | `test_complete_background_task_steer_callback_raises` | ✅ |
| 8.6 | `test_complete_background_task_called_twice` | ✅ |
| 8.7 | `test_run_executor_waits_on_child_done_events` | ✅ |
| 8.8 | `test_run_executor_skips_wait_when_empty` | ✅ |
| 8.9 | `test_run_executor_clears_child_done_events_on_reiterate` | ✅ |
| 8.10 | `test_close_session_sets_all_and_clears` | ✅ |
| 8.11 | `test_finally_sets_parent_done_event` | ✅ |
| 8.12 | `test_finally_noop_when_already_popped` | ✅ |
| 8.13 | `test_finally_noop_when_parent_run_completed` | ✅ |
| 8.14 | `test_finally_noop_when_parent_not_found` | ✅ |
| 8.15 | `test_finally_noop_when_no_parent` | ✅ |
| 8.16 | `test_synchronous_child_event_set_before_reiteration` | ✅ |
| 8.17 | `test_safety_net_fires_without_steer` | ✅ |
| 8.18 | `test_multiple_children_all_must_complete` | ✅ |

### Section 9: Subagent Tests — 14/14 ✅

| Task | Test Name | File | Status |
|------|-----------|------|--------|
| 9.1 | `test_create_child_session_auto_emits_spawn_with_tool_call_id` | test_zed_subagent_spawn.py | ✅ |
| 9.2 | `test_tool_call_id_flows_event_to_converter_consistently` | test_zed_subagent_spawn.py | ✅ |
| 9.3 | `test_zed_mode_tool_call_start_has_kind_subagent` | test_event_converter_snapshots.py | ✅ |
| 9.4 | `test_zed_mode_build_subagent_completed_has_meta_and_tool_name` | test_event_converter_snapshots.py | ✅ |
| 9.5 | `test_notify_completed_called_when_done_event_set` | test_subagent_events.py | ✅ |
| 9.6 | `test_done_event_none_race_immediate_notification` | test_subagent_events.py | ✅ |
| 9.7 | `test_concurrent_children_each_get_completion_notification` | test_subagent_events.py | ✅ |
| 9.8 | `test_closure_error_logged_not_swallowed` | test_subagent_events.py | ✅ |
| 9.9 | `test_consumer_task_refs_cleanup_after_closure` | test_subagent_events.py | ✅ |
| 9.10 | `test_parent_of_cleanup_on_child_exit` | test_subagent_events.py | ✅ |
| 9.11 | `test_max_subagent_depth_raises_at_depth_6` | test_zed_subagent_spawn.py | ✅ |
| 9.12 | `test_recursive_cancellation_cascades_to_grandchildren` | test_subagent_events.py | ✅ |
| 9.13 | Legacy guardrail tests (5 tests) | test_meta_guardrails.py | ✅ |
| 9.14 | `test_team_py_uses_yield_spawn_pattern` | test_zed_subagent_spawn.py | ✅ |

## Additional Checks

| Check | Status | Notes |
|-------|--------|-------|
| `_create_set_event()` removed | ✅ | No matches in `src/` |
| `anyio.Event` used everywhere (not `asyncio.Event`) | ✅ | `context.py:127,363`, test files all use `anyio.Event` |
| Stale pyc files deleted | ✅ | 3 files confirmed absent |
| `getattr`/`hasattr` in scope-specific locations fixed | ✅ | The targeted getattr violations (context.py:201→typed, context.py:229→typed, handler.py:119→typed, workers.py:149→typed, workers.py:267→typed, pool.py:241→typed, pool.py:243→typed) are all fixed. Pre-existing getattr in unrelated methods (context.py:255 `handle_confirmation`, handler.py:457 command dispatch, workers.py:131/173/238 session_id/input_provider) remain but were NOT in scope. |
| team.py/teamrun.py unchanged | ✅ | Verified by test assertion |
| `MAX_SUBAGENT_DEPTH=5` (hardcoded, not YAML-configurable) | ✅ | Module-level constant at `context.py:58` |

## VERDICT: APPROVE

All 67 OpenSpec tasks are implemented. All 12 plan todos are marked complete. Source code verification confirms implementation matches specification in all 9 checked files. Test coverage is complete for all 32 test tasks (8.1-8.18 + 9.1-9.14).
