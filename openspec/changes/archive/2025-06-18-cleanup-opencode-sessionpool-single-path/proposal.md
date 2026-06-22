## Why

The OpenCode server currently maintains **two parallel execution paths** and **two parallel state systems**: SessionPool-based execution (the intended path) and legacy direct-agent execution via `ServerState` in-memory dictionaries. Feature flags (`use_session_pool_for_commands`, `use_session_pool_for_skills`, `use_session_pool_for_init`, `use_session_pool_for_summarize`, `use_session_pool_for_mcp`, `use_session_pool_for_messages`, `use_session_pool_for_status`) control which path is used, but both paths are kept alive. This creates maintenance burden, confusion about which path is authoritative, and risk of state divergence between `ServerState.sessions` and `SessionController._sessions`. The `sessionpool-only-execution` spec already mandates that SessionPool be the sole execution path — this change completes that mandate for OpenCode by removing all legacy fallbacks.

**Scope note**: This change removes the feature flags and their conditional code paths. It does NOT remove `state.messages` or `state.session_status` dictionaries from `ServerState` — that is deferred to a follow-up change after consumers are migrated.

## What Changes

- **BREAKING**: Remove all `OpenCodeConfig` feature flags (`use_session_pool_for_commands`, `use_session_pool_for_skills`, `use_session_pool_for_init`, `use_session_pool_for_summarize`, `use_session_pool_for_mcp`, `use_session_pool_for_messages`, `use_session_pool_for_status`) — SessionPool is always the single path. Existing YAML configs that set these fields will fail validation; add a migration note.
- Remove `_use_session_pool_for_messages()` and `_use_session_pool_for_status()` helper functions in `session_pool_integration.py`
- Remove `set_session_status()` and `get_session_status()` fallback paths that use `getattr(state, "session_status", None)` (dead code in production since `state.session_status` is never declared as a field)
- Remove `append_message_to_session()` and `get_messages_for_session()` fallback paths that branch to `state.messages` when feature flag is disabled
- Route all auxiliary paths (init, summarize, commands, skills, MCP prompts) through `SessionPool.receive_request()` unconditionally
- Preserve the summarize compaction pipeline (already present in both branches; flag removal collapses the two identical blocks)
- Keep `state.messages` dict on `ServerState` for subagent streaming fast-path and checkpoint restoration — restrict to streaming buffer use only, not authoritative source of truth
- Keep `state.reverted_messages` dict — revert/unrevert flow depends on it; migration to SessionPool storage is deferred to follow-up
- Keep event types unchanged (no event schema modifications)

## Capabilities

### Modified Capabilities
- `sessionpool-only-execution`: Extend to cover all OpenCode auxiliary paths (commands, skills, init, summarize, MCP prompts) without feature flags. Remove per-category flag gating from the spec's requirement.
- `unified-session-lifecycle`: Extend to mandate that OpenCode session CRUD uses SessionPool exclusively — no `state.session_status` fallback. Keep `state.messages` as streaming buffer (not authoritative source).

## Impact

- `src/agentpool_config/session_pool.py`: Remove `OpenCodeConfig` feature flags (`use_session_pool_for_*`, `should_use_session_pool_for()`)
- `src/agentpool_server/opencode_server/session_pool_integration.py`: Remove `_use_session_pool_for_messages()`, `_use_session_pool_for_status()`, simplify `get_messages_for_session()`, `append_message_to_session()`, `set_session_status()`, `get_session_status()` to always use SessionPool path
- `src/agentpool_server/opencode_server/routes/session_routes.py`: Remove feature flag branching in auxiliary paths (commands, skills, init, summarize, MCP). Both branches already have identical compaction logic.
- `src/agentpool_server/opencode_server/routes/message_routes.py`: No direct changes needed — calls integration functions that are simplified in session_pool_integration.py
- `src/agentpool_server/opencode_server/routes/global_routes.py`: No direct changes needed — init/summarize routing is in session_routes.py
- `scripts/qa_test_server.py`: Remove `should_use_session_pool_for` mock
- `tests/`: Update tests that mock `should_use_session_pool_for` or test feature flag behavior
