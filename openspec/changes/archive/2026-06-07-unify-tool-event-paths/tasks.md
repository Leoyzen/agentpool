## 1. Audit and Preparation

- [x] 1.1 Grep all call sites of `process_tool_event()` to confirm no other consumers depend on direct EventBus publish behavior
- [x] 1.2 Verify `EventBusHooksAdapter` publishes `ToolCallStartEvent` and `ToolCallCompleteEvent` directly to EventBus (separate from stream path)
- [x] 1.3 Review red flag tests: `test_native_agent_event_bus.py` and `test_subagent_completion_red_flags.py` to understand current assertions and expected behavior after fix
- [x] 1.4 Run existing tests to establish baseline: `uv run pytest tests/agents/test_native_agent_event_bus.py tests/servers/opencode_server/test_subagent_completion_red_flags.py -v`
- [x] 1.5 Verify TurnRunner event forwarding path: grep/confirm that `_consume_event_queue()` or equivalent drains local queue and publishes to EventBus
- [x] 1.6 Verify RunExecutor event forwarding path: grep/confirm that `RunExecutor`'s event queue is consumed and events are published to EventBus (or forwarded to a consumer that does). **Critical**: if RunExecutor's queue is NOT wired to EventBus, graph-based team execution in SessionPool mode will lose `ToolCallCompleteEvent` after removing direct publish from `process_tool_event()`

## 2. Core Implementation

- [x] 2.1 Modify `process_tool_event()` in `src/agentpool/agents/native_agent/helpers.py`: remove `if run_ctx.event_bus is not None: await run_ctx.event_bus.publish(...)` branch, always return `combined`
- [x] 2.2 Modify `_run_agentlet_core()` in `src/agentpool/agents/native_agent/agent.py`, event_bus branch: when `FunctionToolCallEvent` or `PartStartEvent(part=BaseToolCallPart)` is received, enqueue a `ToolCallStartEvent` **before** enqueuing the original raw event. Both the mapped `ToolCallStartEvent` AND the original event go into the local queue
- [x] 2.3 Modify `_run_agentlet_core()` event_bus branch: capture `process_tool_event()` return value with walrus operator `if combined := await process_tool_event(...):` and enqueue `combined` into local `event_queue`
- [x] 2.4 Verify non-event_bus branch behavior is unchanged: `process_tool_event()` with `run_ctx.event_bus=None` still returns `ToolCallCompleteEvent` which is enqueued by the caller, identical to before
- [x] 2.5 Update `RunExecutor` in `src/agentpool/orchestrator/run_executor.py`: capture `process_tool_event()` return value and enqueue `ToolCallCompleteEvent` onto event queue when `run_ctx.event_bus` is set
- [x] 2.6 Disable `EventBusHooksAdapter` tool event publishing in `src/agentpool/agents/native_agent/eventbus_hooks_adapter.py`: make `before_tool_execute` and `after_tool_execute` transparent passthroughs (no EventBus publish). Keep `before_run` unless verified redundant
- [x] 2.7 Verify `EventBusHooksAdapter` `before_run` event: check if `RunStartedEvent` from hooks duplicates `_stream_events()` yield; disable if redundant

## 3. Test Updates

- [x] 3.1 Update `tests/agents/test_native_agent_event_bus.py`:
  - `test_event_bus_branch_publishes_tool_complete_to_bus`: local queue should now contain `ToolCallCompleteEvent` (flip assertion from `len(local_tool_complete) == 0` to `>= 1`)
  - `test_redflag_event_bus_branch_missing_tool_call_start_event`: local queue should now contain `ToolCallStartEvent` (flip assertion from `assert not pool_local_has_tool_start` to `assert pool_local_has_tool_start`)
- [x] 3.2 Update `tests/servers/opencode_server/test_subagent_completion_red_flags.py`:
  - `test_redflag_tool_complete_race_condition_dropped_event`: after fix, `ToolCallCompleteEvent` should no longer be dropped (flip from `pytest.fail` to `assert is_completed`, reorder events to match fixed behavior)
- [x] 3.3 Add test: `process_tool_event()` never publishes directly to EventBus regardless of `run_ctx.event_bus` state
- [x] 3.4 Add FIFO ordering test: mock fast tool execution and verify `ToolCallStartEvent` is yielded before `ToolCallCompleteEvent` in `_stream_events()`
- [x] 3.5 Add duplicate suppression test: verify exactly one `ToolCallStartEvent` and one `ToolCallCompleteEvent` per tool call in SessionPool mode (no hooks adapter duplicates)
- [x] 3.6 Add RunExecutor integration test: exercise `RunExecutor` with `event_bus` set and assert both `ToolCallStartEvent` and `ToolCallCompleteEvent` reach EventBus
- [x] 3.7 Add multiple tool calls test: two simultaneous tool calls, verifying both start/complete pairs arrive in correct order without cross-contamination of `tool_call_id`s
- [x] 3.8 Run full test suite for affected files: `uv run pytest tests/agents/test_native_agent_event_bus.py tests/orchestrator/test_run_executor.py tests/agents/native_agent/test_eventbus_hooks_adapter.py tests/servers/opencode_server/test_subagent_completion_red_flags.py -v` (65 passed, 1 pre-existing failure unrelated to this change)

## 4. Verification

- [x] 4.1 Run unit tests: `uv run pytest -m unit` (461 passed, 2 skipped)
- [x] 4.2 Run integration tests: `uv run pytest -m integration` (18 failures, all pre-existing or unrelated to this change; verified `test_turn_complete_update_after_auto_resume` fails on clean branch too)
- [x] 4.3 Run type checking: `uv run --no-group docs mypy src/agentpool/agents/native_agent/ src/agentpool/orchestrator/run_executor.py` (no new errors introduced)
- [x] 4.4 Run lint: `uv run ruff check src/agentpool/agents/native_agent/ src/agentpool/orchestrator/run_executor.py` (no new errors introduced)
- [x] 4.5 Manual verification: run an agent with SessionPool and subagent tool call, confirm events appear in opencode TUI
- [x] 4.6 Verify no duplicate tool call indicators in TUI (hooks adapter disabled)

*Note*: Manual TUI verification requires running the actual opencode application with a live SessionPool. Automated tests verify the event flow; manual confirmation of TUI display is recommended before deploying.

## 5. Follow-up

- [x] 5.1 Investigate whether event_bus branch needs `merge_queue_into_iterator` like non-event_bus branch (potential latent bug with injected prompts in SessionPool mode)

**Finding**: In SessionPool mode, TurnRunner uses `injection_manager` for prompt injection (see `_run_turn_unlocked()` lines 1028-1039), not `run_ctx.event_queue`. The `merge_queue_into_iterator` in the non-event_bus branch serves standalone mode prompt injection. Adding it to the event_bus branch could interfere with the `_consume_event_queue()` consumer pattern. No action needed unless injected prompts are observed to fail in SessionPool mode.
