## 1. Fix session_manager.resume_session() to accept MCP servers

- [ ] 1.1 Add `mcp_servers` parameter to `ACPSessionManager.resume_session()` signature (type: `Sequence[McpServer] | None = None`, default `None` for backward compat with `handler.py` L276)
- [ ] 1.2 Pass `mcp_servers` to `ACPSession()` constructor instead of hardcoded `None` at line 229
- [ ] 1.3 Add `await session.initialize_mcp_servers()` after `session.initialize()` at line 237 — `ACPSession` separates construction from async MCP initialization (matching `create_session()` pattern at L173-174). The `initialize_mcp_servers()` method already guards against empty `mcp_servers` (returns early if `not self.mcp_servers`).
- [ ] 1.4 Add return value check and warning log for `session.agent.load_session()` at L242 — currently the return value is silently ignored. Add `if not await session.agent.load_session(session_id): logger.warning(...)` to preserve observability (mirrors the warning currently in `acp_agent.py` L643 that task 2.4 removes).

## 2. Fix AgentPoolACPAgent.resume_session()

- [ ] 2.1 Replace `session_manager.create_session()` call with `session_manager.resume_session()` (L623–634). Also remove the subsequent `session = self.session_manager.get_session(session_id)` line — `resume_session()` returns `ACPSession | None` directly, unlike `create_session()` which returns a `str`.
- [ ] 2.2 Pass `params.mcp_servers` to `session_manager.resume_session()`
- [ ] 2.3 When `resume_session()` returns `None`, return empty `ResumeSessionResponse()` (no fallback to create)
- [ ] 2.4 Remove the `session.agent.load_session()` call since `session_manager.resume_session()` already calls it

## 3. Fix AgentPoolACPAgent.load_session()

- [ ] 3.1 Replace `session_manager.create_session()` call with `session_manager.resume_session()` (L495–506). Also remove the subsequent `session = self.session_manager.get_session(session_id)` line — `resume_session()` returns `ACPSession | None` directly, unlike `create_session()` which returns a `str`.
- [ ] 3.2 Pass `params.mcp_servers` to `session_manager.resume_session()`
- [ ] 3.3 When `resume_session()` returns `None`, return empty `LoadSessionResponse()` (no fallback to create)
- [ ] 3.4 Keep the history replay logic (L518–528) since `load_session()` still replays to client
- [ ] 3.5 Remove the duplicate `session.agent.load_session()` call at L513 — `session_manager.resume_session()` already calls it at L242. (Mirrors task 2.4 for `resume_session()`.)

## 4. Add unit tests for the fixed code paths

- [ ] 4.1 Add test in `test_acp_session_resume.py`: verify `session_manager.resume_session()` is called (not `create_session()`) when session is not active — asserts `create_session.assert_not_awaited()`
- [ ] 4.2 Add test in `test_acp_session_resume.py`: verify MCP servers from `ResumeSessionRequest` are passed through to `session_manager.resume_session()`
- [ ] 4.2a Add test in `test_acp_session_resume.py`: verify `session.initialize_mcp_servers()` is called when MCP servers are provided during resume
- [ ] 4.3 Add test in `test_acp_session_resume.py`: verify `load_session()` also calls `session_manager.resume_session()` (not `create_session()`) when session is not active
- [ ] 4.4 Add test in `test_acp_session_resume.py`: verify `load_session()` still replays conversation history via `notifications.replay()` after restore
- [ ] 4.5 Add test in `test_acp_session_resume.py`: verify stored `SessionData` fields (status, agent_name, cwd, created_at) are preserved after resume — assert `session_store.save` is NOT called during resume

## 5. Add integration tests (prevent recurrence)

- [ ] 5.1 Create `tests/servers/acp_server/test_acp_resume_integration.py`: end-to-end test that creates a session with known conversation history, persists it via `session_manager.create_session()`, then resumes via `session_manager.resume_session()` and verifies conversation history is intact
- [ ] 5.2 Integration test: verify `SessionData` `created_at` timestamp is preserved after resume (not reset to current time)
- [ ] 5.3 Integration test: verify `SessionData` `status` field is preserved after resume (not overwritten to default)
- [ ] 5.4 Integration test: verify MCP connections are re-established from `mcp_servers` parameter when resuming with MCP servers — assert `session.initialize_mcp_servers()` is called and MCP connections are created
- [ ] 5.5 Integration test: verify `load_session()` path also preserves stored data — create, persist, then load, assert `SessionData` unchanged
- [ ] 5.6 Integration test: verify no duplicate `session.agent.load_session()` call — mock `session.agent.load_session` and assert `assert_awaited_once()` for both `resume_session()` and `load_session()` paths

## 6. Update existing tests

- [ ] 6.1 Review `test_acp_resume.py` — existing tests already mock `resume_session` and assert `create_session` not called; verify they still pass
- [ ] 6.2 Review `test_rpc.py` — verify `session/resume` routing test (L423-473) still works with updated handler
- [ ] 6.3 Review `test_acp_session_resume.py:test_resume_session_creates_session_if_not_found` (L123-149) — this test already expects `create_session` NOT to be called; verify it passes with the fix

## 7. Verification

- [ ] 7.1 Run `uv run pytest tests/servers/acp_server/ -k "resume or load" -v` — all tests pass
- [ ] 7.2 Run `uv run pytest tests/servers/acp_server/ -m "integration or unit" -v` — full test suite passes
- [ ] 7.3 Run `uv run ruff check src/agentpool_server/acp_server/` — no new lint errors
- [ ] 7.4 Run `uv run pytest tests/servers/acp_server/test_rpc.py -v` — routing regression test passes
