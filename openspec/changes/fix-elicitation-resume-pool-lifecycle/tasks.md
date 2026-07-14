## 1. Add resume parameters to pool turn path

- [x] 1.1 Add `cached_elicitation_responses: dict[str, Any] | None = None`, `deferred_tool_results: Any = None`, `message_history: list[ModelMessage] | None = None` parameters to `session_pool._run_stream_run_turn()` and `SessionPool._create_run_handle()` (`session_pool.py`)
- [x] 1.2 Forward these parameters through `session_pool.run_stream()` → `_run_stream_run_turn()` → `_create_run_handle()` (NOT through `session_controller.receive_request()` — that is a separate fire-and-forget entry point)
- [x] 1.3 In `_create_run_handle()`, set `cached_elicitation_responses` on the new `AgentRunContext` if provided; initialize `RunHandle._message_history` from `message_history` if provided (existing `list[ModelMessage]` field, defaults to `[]`)
- [x] 1.4 Forward `deferred_tool_results` through `RunHandle.start()` → `_execute_turn()` → `agent.create_turn()` → `NativeTurn` via `**pydantic_ai_kwargs`. NOTE: `agent.create_turn()` currently does NOT accept `**kwargs` — must add `**pydantic_ai_kwargs` forwarding to `agent.create_turn()` signature
- [x] 1.5 Specify how `resume_session()` calls `run_stream()` with no new prompt — pass empty string or skip prompt injection when `message_history` is provided

## 1b. Wire _create_run_handle() with full infrastructure

- [x] 1b.1 Add `_host_context` setting to `_create_run_handle()`: set `run_handle._host_context = pool.get_context()` (matching `SessionController._start_run_handle()` pattern)
- [x] 1b.2 Add `_agent_registry` building to `_create_run_handle()`: build from pool's manifest agent names (matching `_start_run_handle()` pattern)
- [x] 1b.3 Add staleness check to `_create_run_handle()`: if `session.current_run_id` is already set and the existing run is not `DONE`, raise `SessionBusyError` (do not silently overwrite)
- [x] 1b.4 Write test: `_create_run_handle()` sets `_host_context` and `_agent_registry` on RunHandle
- [x] 1b.5 Write test: `_create_run_handle()` raises `SessionBusyError` when `current_run_id` already set

## 1c. Add _request_lock guard to _run_stream_run_turn()

- [x] 1c.1 Acquire `session._request_lock` in `_run_stream_run_turn()` before checking `current_run_id` and calling `_create_run_handle()`
- [x] 1c.2 Write test: concurrent `run_stream()` and `receive_request()` on same session creates only ONE RunHandle

## 2. Rewrite _resume_native_agent() to use pool path

- [x] 2.1 Write TDD test: `_resume_native_agent()` calls `session_pool.run_stream()` (not `agent.run_stream(_skip_pool=True)`)
- [x] 2.2 Write TDD test: `cached_elicitation_responses` is passed as parameter and set on `AgentRunContext` by `_create_run_handle()`
- [x] 2.3 Write TDD test: `deferred_tool_results` is forwarded to `agentlet.iter()` when non-empty
- [x] 2.4 Write TDD test: `message_history` from checkpoint (as `list[ModelMessage]`) initializes `RunHandle._message_history`
- [x] 2.5 Write TDD test: no persistent resume state left on `SessionState` after resume
- [x] 2.6 Implement: Rewrite `_resume_native_agent()` to build `cached_elicitation_responses`, extract `list[ModelMessage]` from checkpoint (NOT wrapped in `MessageHistory`), call `session_pool.run_stream()` with all three parameters
- [x] 2.7 Remove `_skip_pool=True` workaround and related comments
- [x] 2.8 Verify tests pass

## 3. Event converter RunErrorEvent → refusal

- [x] 3.1 Verify existing fix: `event_converter.py` uses `stop_reason="refusal"` for `RunErrorEvent` (already implemented)
- [x] 3.2 Verify existing test asserts `stop_reason == "refusal"` (already updated)
- [x] 3.3 Run event converter test suite to confirm no regressions

## 4. E2E tests for full resume lifecycle

- [x] 4.1 Update e2e test to verify: RunHandle created, events reach EventBus via pool path
- [x] 4.2 Add test: second elicitation during resume is durable (verify `checkpoint_manager is not None`, `host_context is not None`)
- [x] 4.3 Add test: crash during resume is recoverable
- [x] 4.4 Add test: cancellation during resumed turn handled correctly
- [x] 4.5 Add test: normal turns unaffected by resume parameters
- [x] 4.6 Add test: concurrent `resume_session()` and `receive_request()` — only ONE RunHandle created
- [x] 4.7 Run full test suite to confirm no regressions

## 5. Journal and ACP consumer verification

- [x] 5.1 Verify: resumed turn's journal starts fresh (MemoryJournal default in `_create_run_handle()`)
- [x] 5.2 Verify: durable journal does not conflict with original checkpoint (resumed turn uses fresh MemoryJournal per Decision 7)
- [x] 5.3 Verify: ACP event consumer active when resumed turn starts
- [x] 5.4 Add tests if needed

## 6. Cleanup and documentation

- [x] 6.1 Update AGENTS.md with resume path architecture
- [x] 6.2 Remove stale comments about Path B workaround
- [x] 6.3 Verify all existing tests pass
