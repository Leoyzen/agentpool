## 1. AgentRunContext Enhancement

- [ ] 1.1 Add `session_id: str | None` field to `AgentRunContext` in `agentpool/agents/context.py`
- [ ] 1.2 Add `event_bus: Any | None` field to `AgentRunContext` in `agentpool/agents/context.py`
- [ ] 1.3 Remove `_DeprecatedField` wrapper from `AgentRunContext.session_id`
- [ ] 1.4 Update `AgentRunContext` docstring to document new fields

## 2. StreamEventEmitter Refactor

- [ ] 2.1 Update `StreamEventEmitter._emit()` to read `session_id` from `run_ctx.session_id` instead of `agent.session_id`
- [ ] 2.2 Update `StreamEventEmitter._emit()` to read `event_bus` from `run_ctx.event_bus` before falling back to global `StreamEventEmitter._event_bus`
- [ ] 2.3 Remove `run_ctx.event_queue.put(event)` from `_emit()` — events go to EventBus only
- [ ] 2.4 Remove `_stream_event_bus_set` logic from `TurnRunner` and `StreamEventEmitter`
- [ ] 2.5 Update `AgentContext.report_progress()` to publish to `run_ctx.event_bus` instead of `run_ctx.event_queue`

## 3. TurnRunner Lifecycle Unification

- [ ] 3.1 Add `_current_run_ctx_var.set(run_ctx)` at the start of `TurnRunner._run_turn_unlocked()`
- [ ] 3.2 Add `_current_run_ctx_var.reset(token)` in the `finally` block of `_run_turn_unlocked()`
- [ ] 3.3 Populate `run_ctx.session_id = session_id` and `run_ctx.event_bus = self.event_bus` when creating `run_ctx`
- [ ] 3.4 Remove `_consume_event_queue()` background task and its startup/shutdown logic
- [ ] 3.5 Add `injection_manager.insert_queued(prompts)` before the first iteration
- [ ] 3.6 Add `injection_manager.flush_pending_to_queue()` after each iteration
- [ ] 3.7 Add `run_ctx.completed = True` in the `finally` block
- [ ] 3.8 Set `session.active_run_ctx = run_ctx` at turn start and `session.active_run_ctx = None` in `finally`
- [ ] 3.9 Create per-run EventBus subscriber in TurnRunner that feeds events back into the stream
- [ ] 3.10 Pass TurnRunner-managed queue to `_run_stream_once()` for `merge_queue_into_iterator`

## 4. BaseAgent State Removal

- [ ] 4.1 Remove `self.session_id` instance attribute from `BaseAgent.__init__`
- [ ] 4.2 Remove `self._active_run_ctx` instance attribute from `BaseAgent.__init__`
- [ ] 4.3 Remove `self._current_stream_task` instance attribute from `BaseAgent.__init__`
- [ ] 4.4 Remove `self._event_queue` instance attribute from `BaseAgent.__init__`
- [ ] 4.5 Make `session_id` a required parameter in `BaseAgent._run_stream_once()` signature
- [ ] 4.6 Update `BaseAgent.run_stream()` to emit `DeprecationWarning` and delegate to `SessionPool`
- [ ] 4.7 Update `BaseAgent.run()` to delegate to `SessionPool` when available
- [ ] 4.8 Update `BaseAgent._current_run_ctx` property to rely solely on ContextVar (no `_active_run_ctx` fallback)
- [ ] 4.9 Update `BaseAgent.inject_prompt()` to delegate to `SessionPool.inject_prompt()`
- [ ] 4.10 Update `BaseAgent.interrupt()` to use `run_ctx.current_task` instead of `_current_stream_task`
- [ ] 4.11 Update `BaseAgent.get_active_run_context()` to read `_current_run_ctx_var` ContextVar first (fast path), with SessionPool fallback only when session_id is available

## 5. Native Agent Stream Refactor

- [ ] 5.1 Update `NativeAgent._stream_events()` to use the `session_id` parameter instead of `self.session_id`
- [ ] 5.2 Replace `merge_queue_into_iterator(stream, run_ctx.event_queue)` with `merge_queue_into_iterator(stream, turn_runner_queue)`
- [ ] 5.3 Update `NativeAgent._stream_events()` docstring to reflect the TurnRunner-managed queue
- [ ] 5.4 Update tests that relied on `run_ctx.event_queue` for tool event injection

## 6. ClaudeCodeAgent and ACPAgent Stream Refactor

- [ ] 6.1 Update `ClaudeCodeAgent._stream_events()` to use TurnRunner-managed queue instead of `run_ctx.event_queue`
- [ ] 6.2 Update `ACPAgent._stream_events()` to use TurnRunner-managed queue instead of `run_ctx.event_queue`
- [ ] 6.3 Add tests for ClaudeCodeAgent event flow through EventBus
- [ ] 6.4 Add tests for ACPAgent event flow through EventBus

## 7. SessionPool Always-On

- [ ] 7.1 Remove `session_pool.enabled` feature flag from YAML config schema
- [ ] 7.2 Update `AgentPool.__init__()` to always create `SessionPool`
- [ ] 7.3 Remove feature flag checks in `AgentPool` that branch between old path and SessionPool path
- [ ] 7.4 Update `AgentPool` configuration validation to reject `session_pool.enabled: false`
- [ ] 7.5 Update documentation to reflect that SessionPool is always enabled

## 8. Protocol Handler Alignment

- [ ] 8.1 Verify ACP handler uses `EventBus.subscribe(session_id, scope="descendants")`
- [ ] 8.2 Update OpenCode handler to use `EventBus.subscribe(session_id, scope="descendants")`
- [ ] 8.3 Verify AG-UI handler uses `EventBus.subscribe(session_id, scope="descendants")`
- [ ] 8.4 Add event conversion for `PartDeltaEvent`, `ToolCallStartEvent`, `ToolCallProgressEvent` in OpenCode handler
- [ ] 8.5 Remove `TODO` comment about incomplete event conversion in OpenCode handler
- [ ] 8.6 Test that child session events reach ACP, OpenCode, and AG-UI subscribers

## 9. Test Migration and Validation

- [ ] 9.1 Migrate tests that call `agent.run_stream()` directly to use `session_pool.run_stream()`
- [ ] 9.2 Update `test_turn_runner.py` red flag tests to verify ContextVar is set
- [ ] 9.3 Update `test_acp_sessionpool_inject_redflag.py` to verify no dual-consumer race
- [ ] 9.4 Add test: shared agent used across sessions has no instance-level session state
- [ ] 9.5 Add test: `StreamEventEmitter._emit()` uses `run_ctx.session_id` not `agent.session_id`
- [ ] 9.6 Add test: tool events appear exactly once in EventBus (no duplication)
- [ ] 9.7 Add test: tool events are visible in the stream yielded by `_run_stream_once()`
- [ ] 9.8 Add test: child session events propagate to parent subscriber with `descendants` scope
- [ ] 9.9 Add test: `_stream_event_bus_set` race is eliminated
- [ ] 9.10 Add test: interrupt works for all agent types using `run_ctx.current_task`
- [ ] 9.11 Add test: EventBus descendant lookup performance is acceptable (benchmark)
- [ ] 9.12 Run full test suite and fix regressions
- [ ] 9.13 Run `mypy src/` and fix type errors introduced by signature changes
- [ ] 9.14 Run `ruff check src/` and fix lint errors

## 10. Documentation and Cleanup

- [ ] 10.1 Update `BaseAgent.run_stream()` docstring with deprecation notice
- [ ] 10.2 Update AGENTS.md architecture notes to reflect SessionPool-only execution
- [ ] 10.3 Document known limitation: shared agents (non-native) still share conversation history
- [ ] 10.4 Remove debug `logger.error("DEBUG_...")` calls from EventBus and StreamEventEmitter
- [ ] 10.5 Update changelog with breaking changes and migration guide
- [ ] 10.6 Review and close related issues (#39 and any SessionPool event routing issues)
