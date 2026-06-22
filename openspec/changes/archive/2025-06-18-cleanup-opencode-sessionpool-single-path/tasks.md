## 1. Remove Feature Flags from Config

- [ ] 1.1 Remove `use_session_pool_for_commands`, `use_session_pool_for_skills`, `use_session_pool_for_init`, `use_session_pool_for_summarize`, `use_session_pool_for_mcp`, `use_session_pool_for_messages`, `use_session_pool_for_status` fields from `OpenCodeConfig` in `src/agentpool_config/session_pool.py`
- [ ] 1.2 Remove `should_use_session_pool_for()` method from `OpenCodeConfig`
- [ ] 1.3 Remove `use_session_pool` master switch field from `OpenCodeConfig` (orphaned after method removal — zero consumers)
- [ ] 1.4 Remove `_env_flag()` helper from `session_pool.py` (only used for these flags)
- [ ] 1.4 Run `uv run pytest tests/unit/test_config.py -x -q` to verify config parsing still works

## 2. Simplify session_pool_integration.py

- [ ] 2.1 Remove `_use_session_pool_for_messages()` helper function
- [ ] 2.2 Remove `_use_session_pool_for_status()` helper function
- [ ] 2.3 Simplify `get_messages_for_session()`: remove `state.messages` fallback for non-subagent sessions, keep subagent in-memory fast-path for streaming ToolPart updates
- [ ] 2.4 Simplify `append_message_to_session()`: remove `state.messages` mirroring fallback when feature flag is disabled (always use SessionPool path)
- [ ] 2.5 Simplify `set_session_status()`: remove `getattr(state, "session_status", None)` fallback — always use `SessionStatusBridge`
- [ ] 2.6 Simplify `get_session_status()`: remove `getattr(state, "session_status", {})` fallback — always use `OpenCodeSessionPoolIntegration`
- [ ] 2.7 Run `uv run pytest tests/servers/opencode_server/ -x -q` to verify integration

## 3. Route Auxiliary Paths Through SessionPool Unconditionally

- [ ] 3.1 In `session_routes.py`, remove feature flag branching for commands path — always use SessionPool
- [ ] 3.2 In `session_routes.py`, remove feature flag branching for skills path — always use SessionPool
- [ ] 3.3 In `session_routes.py`, remove feature flag branching for init path — always use SessionPool
- [ ] 3.4 In `session_routes.py`, remove feature flag branching for summarize path — always use SessionPool, **preserve** `compact_conversation()` + `set_messages_for_session()` after SessionPool summarization
- [ ] 3.5 In `session_routes.py`, remove feature flag branching for MCP prompt path — always use SessionPool
- [ ] 3.6 Verify `message_routes.py` and `global_routes.py` work correctly after `session_pool_integration.py` simplification (these files have no direct feature flag checks — they consume the integration functions)
- [ ] 3.7 Run `uv run pytest tests/servers/opencode_server/ -x -q` after all route changes

## 4. Update External Scripts

- [ ] 4.1 In `scripts/qa_test_server.py`, remove `should_use_session_pool_for` mock (line 41)
- [ ] 4.2 Verify `scripts/qa_test_server.py` starts without errors

## 5. Update Tests

- [ ] 5.1 Remove test cases that test feature flag behavior (e.g., `test_use_session_pool_for_*`)
- [ ] 5.2 Update tests that dynamically inject `state.session_status = {}` — use `OpenCodeSessionPoolIntegration` mock or `SessionStatusBridge` mock instead
- [ ] 5.3 Update tests that mock `_use_session_pool_for_messages` or `_use_session_pool_for_status` — remove mocks
- [ ] 5.4 Run full test suite: `uv run pytest -m "not slow and not acp_snapshot"` and fix failures
- [ ] 5.5 Run OpenCode-specific tests: `uv run pytest tests/servers/opencode_server/ -x -q` to verify no regressions

## 6. Final Verification

- [ ] 6.1 Run `uv run ruff check src/` to ensure no lint issues
- [ ] 6.2 Run `uv run --no-group docs mypy src/` for type checking
- [ ] 6.3 Verify no references to removed symbols in non-test source: `grep -r "should_use_session_pool_for\|_use_session_pool_for_messages\|_use_session_pool_for_status\|use_session_pool_for_" src/agentpool_server/ src/agentpool_config/ --include="*.py"`
- [ ] 6.4 Verify `getattr(state, "session_status"` pattern is fully removed from source code
