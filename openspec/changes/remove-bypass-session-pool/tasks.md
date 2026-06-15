## 1. SessionState: Add turn_owner_task field

- [ ] 1.1 Add `_turn_owner_task: asyncio.Task[Any] | None = None` field to `SessionState` dataclass in `orchestrator/core.py`

## 2. Add _in_turn_context secondary guard

- [ ] 2.1 Add `_in_turn_context: ContextVar[bool] = ContextVar("_in_turn_context", default=False)` to module level in `base_agent.py` (replaces `_bypass_session_pool`)
- [ ] 2.2 In `_should_route_via_sessionpool()` (replacing `_should_bypass_session_pool()`), check `_in_turn_context.get()` FIRST: if True → return False (direct execution, catches child tasks)

## 3. TurnRunner: Set and clear turn_owner_task + _in_turn_context

- [ ] 3.1 In `_run_turn_unlocked()`, set `session._turn_owner_task = asyncio.current_task()` at the start of the try block
- [ ] 3.2 In `_run_turn_unlocked()`, import and set `_in_turn_context.set(True)` alongside turn_owner_task at entry
- [ ] 3.3 In `_run_turn_unlocked()`, clear `session._turn_owner_task = None` and `_in_turn_context.set(False)` in the `finally` block
- [ ] 3.4 Remove `from agentpool.agents.base_agent import _bypass_session_pool` import from `_run_turn_unlocked()` — keep `_current_run_ctx_var` import
- [ ] 3.5 Remove `_bypass_session_pool.set(True)` and `_bypass_session_pool.set(False)` calls from `_run_turn_unlocked()`

## 4. BaseAgent: Replace _should_bypass_session_pool with _should_route_via_sessionpool

- [ ] 4.1 Remove `_bypass_session_pool` ContextVar and `_should_bypass_session_pool()` function from module level in `base_agent.py`
- [ ] 4.2 Add `_should_route_via_sessionpool(session_pool, session_id) -> bool` function (module-level, replacing `_should_bypass_session_pool`):
  ```python
  def _should_route_via_sessionpool(session_pool, session_id) -> bool:
      # Guard 1: _in_turn_context catches child tasks of turn owner
      if _in_turn_context.get():
          return False
      if session_pool is None:
          return False
      session = session_pool.sessions.get_session(session_id)
      if session is None:
          return True  # New session → route via SessionPool
      # Guard 2: Turn-Owner Tracking — same task check
      current_task = asyncio.current_task()
      if current_task is not None and session._turn_owner_task is current_task:
          return False  # Already holding turn_lock → direct execution
      return True  # Route via SessionPool
  ```

## 5. BaseAgent.run_stream(): Simplify routing logic

- [ ] 5.1 In `run_stream()`, replace the bypass check (`not _should_bypass_session_pool()`) with:
  ```python
  if self.agent_pool is not None and self.agent_pool.session_pool is not None:
      effective_session_id = session_id or generate_session_id()
      session_pool = self.agent_pool.session_pool
      existing_session = session_pool.sessions.get_session(effective_session_id)
      if existing_session is None or existing_session.agent_name == self.name:
          if _should_route_via_sessionpool(session_pool, effective_session_id):
              # Route via SessionPool
              ...
              return
  # Direct execution
  ```
- [ ] 5.2 Inline `_run_stream_direct()` logic into the direct execution branch — extract a shared helper `_execute_direct(...)` that both `run_stream()` and `run()` can call, containing the session logging, `AgentRunContext` creation, `_current_run_ctx_var` setup, and `_run_stream_once()` call
- [ ] 5.3 Remove `_run_stream_direct()` method

## 6. BaseAgent.run(): Simplify routing logic

- [ ] 6.1 In `run()`, replace the bypass check with `_should_route_via_sessionpool()`, mirroring the same logic as `run_stream()`
- [ ] 6.2 The direct execution branch calls `self.run_stream()` (same as current fallback path at line 1657)

## 7. BaseAgent._run_stream_once(): Update signal emission guard

- [ ] 7.1 In the post-processing section of `_run_stream_once()` (around line 1241), replace `if not _should_bypass_session_pool():` with `if not _in_turn_context.get():` — skip `message_sent.emit()` when executing within a turn (signal will be emitted by the outer SessionPool path)

## 8. Tests: Add turn_owner tests (interleave with implementation)

*These tests should be written immediately after tasks 3-4 (TurnRunner + _in_turn_context).*

- [ ] 8.1 `test_turn_runner.py`: Add `test_turn_owner_set_during_run_turn` — verify `session._turn_owner_task is asyncio.current_task()` during `_run_turn_unlocked`
- [ ] 8.2 `test_turn_runner.py`: Add `test_turn_owner_cleared_after_run_turn` — verify `session._turn_owner_task` is None after turn completes
- [ ] 8.3 `test_turn_runner.py`: Add `test_in_turn_context_set_during_run_turn` — verify `_in_turn_context.get()` is True during `_run_turn_unlocked`
- [ ] 8.4 `test_turn_runner.py`: Add `test_in_turn_context_propagates_to_child_task` — create a child task inside a turn, verify `_in_turn_context.get()` is True in the child task
- [ ] 8.5 `test_turn_runner.py`: Remove `test_bypass_session_pool_set_during_run_turn`, `test_bypass_session_pool_cleared_after_run_turn`, `test_bypass_session_pool_external_call`, `test_bypass_session_pool_contextvar_true`, `test_bypass_session_pool_agui_stack_inspection`

## 9. Tests: Fix redflag tool calls test (after task 5)

- [ ] 9.1 `test_streaming_redflag_tool_calls.py`: Replace `_bypass_session_pool.set(True)` — create a session via `session_pool.create_session()`, then register the dynamic tool on the session's per-session agent (`session.agent.tools.register_tool()`), then call `run_stream()` which will find the existing session with the registered tool

## 10. Cleanup: Remove dead code and docs

- [ ] 10.1 Remove `docs/audit/agui-bypass-audit.md` — AG-UI bypass is no longer a special case
- [ ] 10.2 Update comments in `base_agent_adapter.py` that reference `_should_bypass_session_pool()` — clarify that AG-UI works through SessionPool via ProtocolEventConsumerMixin, not because of any bypass
- [ ] 10.3 Verify no remaining references: `grep -r "_bypass_session_pool\|_should_bypass_session_pool" src/ tests/ --include="*.py"` should return zero matches
- [ ] 10.4 Run `ruff check src/` and `mypy src/` to verify no type errors
- [ ] 10.5 Run full test suite: `uv run pytest`