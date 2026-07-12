## 1. Public API — `get_session_context()` accessor

- [ ] 1.1 Add `get_session_context(session_id: str) -> _SessionContext | None` method to `MCPManager` in `src/agentpool/mcp_server/manager.py`. Returns `_session_contexts.get(session_id)` without creating. Include docstring.
- [ ] 1.2 Add unit test in `tests/mcp_server/test_mcpmanager_caching.py`: verify `get_session_context()` returns existing context, returns `None` for unknown session, does not create phantom contexts.

## 2. Remove legacy fields from Agent

- [ ] 2.1 Remove `self._mcp_snapshot: McpConfigSnapshot | None = None` and `self._session_connection_pool: SessionConnectionPool | None = None` field declarations from `Agent.__init__()` in `src/agentpool/agents/native_agent/agent.py` (lines 333-334). Also remove the comment block at lines 330-332 ("MCP lifecycle snapshot — set externally..."). Remove `SessionConnectionPool` import if it becomes unused.

## 3. Migrate `session.py:initialize_mcp_servers()`

- [ ] 3.1 In `src/agentpool_server/acp_server/session.py` line 543: replace `existing = self.agent._mcp_snapshot` with reading from session context:
  ```python
  ctx = self.agent.mcp.get_session_context(self.session_id)
  existing = ctx.snapshot if ctx is not None else None
  ```
- [ ] 3.2 Remove line 552: `self.agent._mcp_snapshot = new_snapshot`. The `update_session_snapshot()` call at line 556 is the sole write path.
- [ ] 3.3 Remove the dead `_session_connection_pool` branch at lines 499-502 (the `if self.agent._session_connection_pool is not None:` block). Keep the `add_acp_transport()` call that follows it (already unconditional from `558c64472`).
- [ ] 3.4 Update docstring at lines 460-462 to remove references to `_mcp_snapshot` and `_session_connection_pool`. Replace with description of session context-based flow.

## 4. Migrate `agent.py:get_agentlet()` skill config registration

- [ ] 4.1 In `src/agentpool/agents/native_agent/agent.py` lines 914-930: replace `self._mcp_snapshot` reads/writes with session context access:
  ```python
  session_id = run_ctx.session_id if run_ctx else None
  if session_id is not None and skill_entries:
      from agentpool.mcp_server.config_snapshot import McpConfigSnapshot
      ctx = self.mcp.get_or_create_session(session_id)
      current = ctx.snapshot or McpConfigSnapshot()
      self.mcp.update_session_snapshot(session_id, current.with_skill_configs(tuple(skill_entries)))
  ```
  Remove the `if self._mcp_snapshot is None: self._mcp_snapshot = McpConfigSnapshot()` guard at lines 914-917.
- [ ] 4.2 Add code comment documenting the ordering constraint: `as_capability()` MUST run before skill config registration to prevent duplicate tools.
- [ ] 4.3 After step 4.1, check if `McpConfigSnapshot` top-level import in `agent.py` is now unused (the inline import in 4.1 handles runtime usage). Remove if unused.

## 5. Migrate `capability.py:SkillCapability._build_mcp_toolsets_from_pool()`

- [ ] 5.1 In `src/agentpool/skills/capability.py` lines 219-221: replace `agent._session_connection_pool` and `agent._mcp_snapshot` reads with session context access:
  ```python
  session_id = deps.run_ctx.session_id if deps.run_ctx is not None else "default"
  ctx = agent.mcp.get_session_context(session_id)
  session_pool = ctx.connection_pool if ctx is not None else None
  snapshot = ctx.snapshot if ctx is not None else None
  ```
- [ ] 5.2 Keep the existing fallback to `_build_mcp_toolsets_legacy_session()` when `session_pool is None or snapshot is None`. Do NOT make this the primary path — `SkillMcpManager` has better lifecycle management.

## 6. Migrate `session_controller.py` private dict access

- [ ] 6.1 In `src/agentpool/orchestrator/session_controller.py` line 491: replace `parent_agent.mcp._session_contexts.get(session.parent_session_id)` with `parent_agent.mcp.get_session_context(session.parent_session_id)`.
- [ ] 6.2 In `src/agentpool/orchestrator/session_controller.py` line 520: same replacement for the second `parent_agent.mcp._session_contexts.get(...)` call.

## 7. Update tests — migrate `_mcp_snapshot` assertions

- [ ] 7.1 Update `tests/servers/acp_server/test_acp_session_mcp_registration.py`: replace `agent._mcp_snapshot` assertions with `agent.mcp.get_session_context(sid).snapshot`.
- [ ] 7.2 Update `tests/mcp_server/test_e2e_acp_inheritance_function_model.py`: replace `agent._mcp_snapshot` references with session context accessor. Also replace private `mcp_manager._session_contexts.get()` calls with `get_session_context()`.
- [ ] 7.3 Search for all remaining test references to `_mcp_snapshot` and `_session_connection_pool` via `grep -rn "_mcp_snapshot\|_session_connection_pool" tests/` and update them.

## 8. Migrate test files — `_session_contexts` private dict → `get_session_context()`

Migrate all test files that directly access `mcp._session_contexts` private dict for assertions. Replace with `get_session_context()` public API. This ensures tests validate behavior through the public interface, not internal implementation details.

- [ ] 8.1 Update `tests/mcp_server/test_session_lifecycle.py` (7 sites): replace `_session_contexts` existence/non-existence checks with `get_session_context()` returning context or `None`.
- [ ] 8.2 Update `tests/mcp_server/test_session_wiring_integration.py` (7 sites): replace `_session_contexts` assertions with `get_session_context()`.
- [ ] 8.3 Update `tests/mcp_server/test_e2e_session_controller.py` (9 sites): replace `_session_contexts` access with `get_session_context()`.
- [ ] 8.4 Update `tests/mcp_server/test_e2e_connection_tracking.py` (4 sites): replace `_session_contexts` access with `get_session_context()`.
- [ ] 8.5 Update `tests/mcp_server/test_e2e_mcp_capability.py` (2 sites): replace `_session_contexts` emptiness checks with `get_session_context()` returning `None`.
- [ ] 8.6 Update `tests/mcp_server/test_stale_mcp_connection.py` (6 sites): replace `_session_contexts` access with `get_session_context()`.
- [ ] 8.7 Update `tests/mcp_server/test_review_fixes.py` (7 sites): replace `_session_contexts` access with `get_session_context()`.
- [ ] 8.8 Update `tests/mcp_server/test_review_fixes_r3.py` (3 sites): replace `_session_contexts` access with `get_session_context()`.
- [ ] 8.9 Update `tests/mcp_server/test_session_close_integration.py` (6 sites): replace `_session_contexts` access with `get_session_context()`.
- [ ] 8.10 Update `tests/mcp_server/test_child_session_acp_fix.py` (1 site): replace `_session_contexts` assertion with `get_session_context()`.
- [ ] 8.11 Update `tests/servers/acp_server/test_resume_session_lifecycle.py` (2 sites): replace `_session_contexts` access with `get_session_context()`.
- [ ] 8.12 Update `tests/servers/acp_server/test_e2e_session_lifecycle.py` (3 sites): replace `_session_contexts` access with `get_session_context()`.
- [ ] 8.13 Update `tests/servers/acp_server/test_resume_reconnect.py` (1 site): replace `_session_contexts` access with `get_session_context()`.
- [ ] 8.14 Run `grep -rn "_session_contexts" tests/` and verify zero results (all private dict access migrated).

## 9. Add integration test — mock tool inheritance to subagents

Add integration tests that verify a pool-level mock MCP tool (following the `search_kb` pattern) is inherited by and callable from subagent sessions through the `get_session_context()` accessor.

- [ ] 9.1 Add `test_subagent_inherits_pool_mcp_tool_via_get_session_context` in `tests/mcp_server/test_e2e_acp_inheritance_function_model.py`: Create a pool-level MCP server with a mock tool (e.g., `search_kb`). Spawn a child session (librarian pattern). Verify `get_session_context(child_sid)` returns a context whose snapshot contains the pool-level MCP config. Verify `as_capability(child_sid)` produces a toolset that includes the mock tool.
- [ ] 9.2 Add `test_get_session_context_returns_none_after_cleanup` in `tests/mcp_server/test_mcpmanager_caching.py`: Verify that after `cleanup_session()`, `get_session_context()` returns `None` (not a stale context). This validates the cleanup path works correctly through the public API.

## 10. Final verification

- [ ] 10.1 Run `grep -rn "_mcp_snapshot\|_session_connection_pool" src/ tests/` and verify zero results (all references removed).
- [ ] 10.2 Run `grep -rn "_session_contexts" tests/` and verify zero results (all test private dict access migrated to `get_session_context()`). Note: `_session_contexts` in `src/` (inside `MCPManager`) is expected — only tests need migration.
- [ ] 10.3 Run `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` — verify clean.
- [ ] 10.4 Run `uv run pytest -m "not slow and not acp_snapshot"` — verify all tests pass.
- [ ] 10.5 Run `uv run pytest tests/mcp_server/ tests/servers/acp_server/ tests/orchestrator/test_sessionpool_subagent_mcp_inheritance.py -x -v` — verify full MCP + subagent inheritance suite passes.
