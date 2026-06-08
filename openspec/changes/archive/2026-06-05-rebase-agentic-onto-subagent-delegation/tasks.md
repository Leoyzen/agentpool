## 1. Pre-Rebase Validation

- [ ] 1.1 Run `pytest tests/acp_server/ tests/servers/acp_server/` on `feat/0042` and record baseline pass/fail
- [ ] 1.2 Run `pytest tests/acp_server/ tests/servers/acp_server/` on `develop/agentic` and record baseline pass/fail
- [ ] 1.3 Create backup branch: `git branch backup/feat-0042-before-rebase`

## 2. Rebase Execution

- [ ] 2.1 Start rebase: `git checkout develop/agentic && git rebase feat/0042`
- [ ] 2.2 Resolve conflict in `src/agentpool_server/acp_server/acp_agent.py`
  - Keep `_protocol_handler` field and SessionPool init logic
  - Keep `_catalog_provider` field and subagent catalog methods
  - Remove `_session_agents`, `_session_agent_locks`, `get_or_create_session_agent()`, `remove_session_agent()`, `cleanup_all_session_agents()`
- [ ] 2.3 Resolve conflict in `src/agentpool_server/acp_server/session.py`
  - Keep subagent delegation logic (`delegation` param, `_run_subagent_directly`, `_foreground_children`)
  - Keep develop's direct pool agent assignment (no per-session agent creation)
  - Keep develop's `client_supports_turn_complete` wiring
- [ ] 2.4 Resolve conflict in `src/agentpool_server/acp_server/event_converter.py`
  - Keep feat/0042's `SpawnSessionStart` → `ToolCallStart(kind="subagent")` conversion
  - Keep develop's `TurnCompleteUpdate` emission on `StreamCompleteEvent`
  - Keep develop's always-yield `UsageUpdate` behavior
  - Merge `reset()` changes from both branches
- [ ] 2.5 Resolve conflict in `src/agentpool_server/acp_server/session_manager.py`
  - Accept develop's `SessionPool`-based child session creation path
  - Keep feat/0042's `cancel_session()` method
- [ ] 2.6 Verify `src/agentpool_server/acp_server/handler.py` applies cleanly (new file, no conflict expected)

## 3. Post-Rebase Cleanup

- [ ] 3.1 Delete `tests/servers/acp_server/test_acp_per_session_agent_red_flags.py` (tests removed API)
- [ ] 3.2 Fix `tests/servers/acp_server/test_acp_session_manager_child_session.py` for SessionPool API
  - Replace `SessionManager` with `SessionPool`
  - Update `pool.sessions` references to `pool._session_pool`
- [ ] 3.3 Fix any opencode_server tests referencing `_session_agents` if they fail
- [ ] 3.4 Run `ruff check src/agentpool_server/acp_server/` and fix any lint errors
- [ ] 3.5 Run `ruff format src/agentpool_server/acp_server/` to normalize formatting

## 4. Test Validation

- [ ] 4.1 Run `pytest tests/acp_server/ -v` and fix failures
- [ ] 4.2 Run `pytest tests/servers/acp_server/ -v` and fix failures
- [ ] 4.3 Run snapshot tests: `pytest tests/test_acp_event_converter_snapshots.py -v`
  - Update `.ambr` snapshots if changes are intentional
- [ ] 4.4 Run full test suite: `pytest` (or `pytest -m unit` for quick check)
- [ ] 4.5 Verify no type errors: `mypy src/agentpool_server/acp_server/`

## 5. Functional Verification

- [ ] 5.1 Verify subagent catalog is still advertised after rebase
- [ ] 5.2 Verify foreground child cancellation still works in legacy mode (`use_session_pool=false`)
- [ ] 5.3 Verify `TurnCompleteUpdate` is emitted when `client_capabilities.turn_complete=True`
- [ ] 5.4 Verify subagent `ToolCallStart` events are still emitted in inline/tool_box mode

## 6. Finalization

- [ ] 6.1 Review rebase commit history: `git log --oneline --graph feat/0042..HEAD`
- [ ] 6.2 Ensure commit messages are preserved and meaningful
- [ ] 6.3 Update this change's status to complete in OpenSpec
