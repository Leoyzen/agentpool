## 1. Core Implementation

- [ ] 1.1 In `get_or_create_session_agent()` (`core.py`, line 638): Replace `return base_agent` for child sessions with lookup of parent's per-session agent (`parent_state = self._sessions.get(session.parent_session_id); if parent_state and parent_state.is_per_session_agent: return parent_state.agent`)
- [ ] 1.2 Keep `is_per_session_agent = False` for child sessions (already set at line 660) — cleanup responsibility stays with parent
- [ ] 1.3 Remove dead `resource_providers` sync at handler.py line 313-317 (no consumer exists)

## 2. Tests

- [ ] 2.1 Update `test_child_session_does_not_inherit_parent_session_mcp_providers` → assert child session's agent IS the parent's per-session agent (identity check), and has parent's MCP providers
- [ ] 2.2 Update `test_child_session_does_not_inherit_any_session_providers` → assert child inherits parent's agent (which has MCP providers), base_agent is NOT mutated
- [ ] 2.3 Update `test_child_session_uses_shared_agent_without_inheritance` → assert child gets parent's agent when parent has per-session agent; falls back to base_agent when parent uses shared agent
- [ ] 2.4 Add new test: `test_child_session_falls_back_to_base_agent_when_parent_has_no_per_session_agent` — verify no regression when parent uses shared agent (MCP limit reached scenario)
- [ ] 2.5 Add new test: `test_child_session_close_does_not_exit_parent_agent` — verify `close_session(child)` does NOT call `agent.__aexit__()` when child shares parent's agent

## 3. Verification

- [ ] 3.1 Run `uv run pytest tests/orchestrator/test_sessionpool_subagent_mcp_inheritance.py -v` — all tests pass
- [ ] 3.2 Run `uv run pytest -m unit` — no regressions in unit tests
- [ ] 3.3 Run `uv run ruff check src/` — no new lint errors
