## 1. Phase 1 — Core Abstractions

- [ ] 1.1 Create `orchestrator/turn.py` with `Turn` ABC: `execute()` abstract async generator, `message_history` property, `final_message` property (raises RuntimeError if accessed before execute)
- [ ] 1.2 Create `orchestrator/event_mapper.py` with `EventMapper` class: constructor takes `agent_name` and `message_id`, tracks pending tool calls by `tool_call_id`, maps `FunctionToolCallEvent` → `ToolCallStartEvent`, `FunctionToolResultEvent` → `ToolCallCompleteEvent`, passes through unmatched events. Extract logic from `RunExecutor` L220-283.
- [ ] 1.3 Create `agents/native_agent/turn.py` with `NativeTurn`: wraps `agentlet.iter()` → `next(node)` → `End` cycle, uses `EventMapper`, handles `RunAbortedError`/`UndrainedPendingMessagesError`/`CancelledError`, ~80 lines
- [ ] 1.4 Add `RunStatus` enum (idle/running/done) to `orchestrator/run.py`
- [ ] 1.5 Restructure `RunHandle` in `orchestrator/run.py`: add `_idle_event` (asyncio.Event), `_message_queue` (list[str]), `_message_history` (list[ModelMessage]), `_closing` (bool), `_status` (RunStatus). Add `start()` async generator, `steer()`, `followup()`, `close()`, `cancel()`, `__aenter__`/`__aexit__`. Preserve existing fields. Add extensibility docstring.
- [ ] 1.6 Add `create_turn()` abstract method to `BaseAgent`, override in `NativeAgent` to return `NativeTurn`
- [ ] 1.7 Add `BaseAgent.run()` returning `RunHandle` (constructs and returns, no execution)
- [ ] 1.8 Add `BaseAgent.run_stream()` as v1-compatible async generator: wraps `agent.run()` + `run.start()`, detects `StreamCompleteEvent` → calls `run.close()` → breaks

## 2. Phase 1 — SessionController Simplification

- [ ] 2.1 Simplify `SessionController.receive_request()`: session validation (exists, not closing, max_concurrent_runs) + delegate to `RunHandle.start()` (idle) or `RunHandle.steer()`/`.followup()` (busy). ~15 lines.
- [ ] 2.2 Remove `SessionController._create_run()` — RunHandle constructs itself
- [ ] 2.3 Remove `SessionController._cleanup_run()` — RunHandle manages own cleanup
- [ ] 2.4 Remove `SessionController.cancel_run_for_session()` — callers use `RunHandle.cancel()`
- [ ] 2.5 Update `close_session()` to call `RunHandle.close()` before cancelling scope, with 30s timeout fallback to `RunHandle.cancel()`

## 3. Phase 1 — TurnRunner Deprecation

- [ ] 3.1 Add `DeprecationWarning` to `TurnRunner.__init__()`
- [ ] 3.2 Make `TurnRunner.steer()` a thin delegate to `RunHandle.steer()` with `DeprecationWarning`
- [ ] 3.3 Make `TurnRunner.followup()` a thin delegate to `RunHandle.followup()` with `DeprecationWarning`
- [ ] 3.4 Make `TurnRunner.run_loop()` a thin delegate to `RunHandle.start()` with `DeprecationWarning`
- [ ] 3.5 Add feature flag `AGENTPOOL_USE_RUN_TURN` (default: `false`) to `SessionController.receive_request()` routing — native agents use RunHandle when true, existing TurnRunner when false

## 4. Phase 1 — Subagent Interaction

- [ ] 4.1 Wire `steer_callback` on `AgentRunContext` to `RunHandle.steer()` in `RunHandle.__init__`
- [ ] 4.2 Add `child_done_events` check between Turns in `RunHandle.start()` (after StreamCompleteEvent, before idle): wait for child events, process `queued_steer_messages` as next Turn prompts

## 5. Phase 1 — Tests

- [ ] 5.1 Write `NativeTurn.execute()` tests: verify iter/next/stream cycle, event mapping, exception handling
- [ ] 5.2 Write `RunHandle` lifecycle tests: idle/wake/steer/followup/close/cancel, async with protocol
- [ ] 5.3 Write `EventMapper` tests: tool call tracking, event mapping, unmatched passthrough
- [ ] 5.4 Update `receive_request` tests for delegation pattern (idle → RunHandle.start, busy → RunHandle.steer/followup)
- [ ] 5.5 Mark existing `TurnRunner` tests with `@pytest.mark.deprecated`

## 6. Phase 2 — ACP Migration

- [ ] 6.1 Create `agents/acp_agent/turn.py` with `ACPTurn`: wraps ACP `session/prompt` → stream → complete, uses `PromptInjectionManager.inject()`/`consume()` for tool-result augmentation, ~30 lines
- [ ] 6.2 Override `create_turn()` in `ACPAgent` to return `ACPTurn`
- [ ] 6.3 Add `AGENTPOOL_USE_RUN_TURN_FOR_ACP` feature flag (default: `false`) for ACP routing
- [ ] 6.4 Deprecate `PromptInjectionManager.queue()` and `.pop_queued()` with `DeprecationWarning`
- [ ] 6.5 Update ACP integration tests: test `RunHandle.steer()` for ACP path, verify tool-result augmentation still works
- [ ] 6.6 Verify `_post_turn_injections` and `_post_turn_prompts` are no longer populated for ACP agents using new path

## 7. Phase 3 — Cleanup and Deletion

- [ ] 7.1 Delete `TurnRunner` class entirely from `orchestrator/core.py`
- [ ] 7.2 Delete `RunExecutor` class entirely — delete `orchestrator/run_executor.py` file
- [ ] 7.3 Delete `PromptInjectionManager.queue()` and `.pop_queued()` and `flush_pending_to_queue()`
- [ ] 7.4 Remove `AGENTPOOL_USE_RUN_TURN` feature flag — RunHandle is the only path
- [ ] 7.5 Remove `AGENTPOOL_USE_RUN_TURN_FOR_ACP` feature flag
- [ ] 7.6 Update all `SessionPool` methods that delegate to `TurnRunner` to delegate to `RunHandle` directly
- [ ] 7.7 Update protocol server references (ACP, OpenCode, AG-UI, OpenAI API) — replace `TurnRunner` with `RunHandle`
- [ ] 7.8 Delete deprecated `TurnRunner` tests
- [ ] 7.9 Delete `RunExecutor` tests
- [ ] 7.10 Delete `PromptInjectionManager` queuing tests (keep `inject()`/`consume()` tests)
- [ ] 7.11 Delete `TurnRunner` fields: `_post_turn_injections`, `_post_turn_prompts`, `_injection_locks`, `_session_task_groups`, `_runs`, `_enable_auto_resume`, `_max_auto_resume`
- [ ] 7.12 Run full test suite — verify no `DeprecationWarning` from orchestrator layer
