## 1. TurnRunner Unification - Strategy Protocol

**STATUS: SKIPPED** — LegacyTurnRunner was dead code (never imported in production). TurnRunner already handles all agent types via `agent._run_stream_once()`. Strategy pattern would add indirection without value.

- [x] 1.1 ~Define `TurnExecutionStrategy` protocol~ — SKIPPED: unnecessary indirection
- [x] 1.2 ~Extract native turn logic~ — SKIPPED: already unified in TurnRunner
- [x] 1.3 ~Extract non-native turn logic~ — SKIPPED: already unified in TurnRunner
- [x] 1.4 ~Update strategy mapping~ — SKIPPED: no strategies needed
- [x] 1.5 ~Delegate to strategy~ — SKIPPED: inline logic is correct
- [x] 1.6 ~Verify shared infrastructure~ — VERIFIED: TurnRunner manages locks, queues, auto-resume, RunHandle, timings

## 2. TurnRunner Unification - Integration & Cleanup

- [x] 2.1 ~Update `SessionPool.__init__()`~ — N/A: no strategies to register
- [x] 2.2 Remove all imports of `LegacyTurnRunner` across the codebase
- [x] 2.3 Delete `orchestrator/legacy_runner.py`
- [x] 2.4 Update any tests referencing `LegacyTurnRunner` to use `TurnRunner`
- [x] 2.5 Run unit tests for `orchestrator/core.py` to verify unified runner behavior

## 3. InputProvider Session Binding

- [x] 3.1 Update `AgentContext.get_input_provider()` to read from `SessionState` first, then fall back to deprecated agent-level field
- [x] 3.2 Add `get_session_state()` helper on `AgentContext` that resolves session via `agent_pool.session_pool`
- [x] 3.3 Update `SessionController.get_or_create_session_agent()` to NOT mutate `base_agent._input_provider` on shared agents
- [x] 3.4 Ensure `input_provider` is stored on `SessionState` in `SessionController.receive_request()`
- [x] 3.5 ~Update `TurnRunner._run_turn_unlocked()`~ — N/A: input_provider flows through SessionState, not TurnRunner
- [x] 3.6 Mark `BaseAgent._input_provider` as deprecated with warning

## 4. Pydantic-AI Call Chain Optimization

- [x] 4.1 Update `create_approval_bridge_capability()` signature to accept `input_provider: InputProvider | None`
- [x] 4.2 Update bridge handler to use passed `input_provider` directly instead of `ctx.deps.get_input_provider()`
- [x] 4.3 Update `Agent.get_agentlet()` to resolve `input_provider` from `SessionState` (or kwargs) and pass to bridge
- [x] 4.4 Update `Agent.get_agentlet()` to pass `input_provider` into `AgentContext` construction for the agentlet
- [x] 4.5 Verify tool confirmation still works end-to-end for native agents

## 5. Simplify get_active_run_context()

- [x] 5.1 Refactor `BaseAgent.get_active_run_context()` to use two-level fallback only:
  - SessionPool mode: `SessionPool` lookup exclusively
  - Standalone mode: `_current_run_ctx_var` exclusively
- [x] 5.2 Remove `_active_run_ctx` fallback from `get_active_run_context()`
- [x] 5.3 Remove `_background_run_ctx` fallback from `get_active_run_context()`
- [x] 5.4 Update `BaseAgent.run_stream()` to not set `_active_run_ctx`
- [x] 5.5 Update `BaseAgent.run_in_background()` to use ContextVar or dedicated background context storage
- [x] 5.6 Audit all callers of `get_active_run_context()` and update if they relied on removed fallbacks

## 6. Testing & Verification

- [x] 6.1 Run full test suite: `uv run pytest` — **464 passed, 2 skipped**
- [x] 6.2 Verify native agent streaming tests pass with unified `TurnRunner`
- [x] 6.3 Verify ACP agent tests pass with unified `TurnRunner`
- [x] 6.4 Verify ClaudeCode agent tests pass with unified `TurnRunner`
- [x] 6.5 Verify tool confirmation tests pass with optimized call chain
- [x] 6.6 Verify `get_active_run_context()` behavior with new two-level fallback
- [x] 6.7 Verify concurrent session tests (shared agent, per-session providers)
- [x] 6.8 Run type checking: `uv run --no-group docs mypy src/` — pre-existing errors only
- [x] 6.9 Run linting: `uv run ruff check src/` — pre-existing errors only

## 7. Documentation & Finalization

- [x] 7.1 Update `AGENTS.md` or relevant docs to reflect unified TurnRunner architecture
- [x] 7.2 Update any docstrings mentioning `LegacyTurnRunner`
- [x] 7.3 Verify all OpenSpec artifacts are complete and consistent
- [x] 7.4 Archive the change with `openspec archive`
