## 1. SessionState Model Extension

- [ ] 1.1 Add `resource_providers: list[ResourceProvider]` field to `SessionState` dataclass in `agentpool/sessions/models.py`
- [ ] 1.2 Add `close_resource_providers()` async method to `SessionState` for explicit provider cleanup
- [ ] 1.3 Add type hints and docstring for the new field and method

## 2. Eager SessionState Creation for ACP

- [ ] 2.1 Modify `ACPSessionManager.create_session()` in `agentpool_server/acp_server/session_manager.py` to eagerly create `SessionState` via `SessionPool.create_session()` for **top-level ACP sessions** before calling `initialize_mcp_servers()` (child sessions receive SessionState via `SessionController` and inherit providers through Task 3.1)
- [ ] 2.2 Ensure the eager `SessionState` has correct `agent_name` and `session_id`
- [ ] 2.3 **Note**: `SessionPool.create_session()` already delegates to `get_or_create_session()`, which is idempotent — calling it again during `run_stream()` returns the existing `SessionState`
- [ ] 2.4 Add unit test: `SessionState` exists before `initialize_mcp_servers()` is called for top-level sessions

## 3. Session Inheritance

- [ ] 3.1 Modify `SessionController._get_or_create_session_locked()` in `agentpool/orchestrator/core.py` to inherit `resource_providers` from the parent session (if `parent_session_id` is provided and exists in `self._sessions`) when creating a new `SessionState`
- [ ] 3.2 Ensure inheritance is shallow copy (`list(parent_state.resource_providers)`) to prevent accidental mutation propagation
- [ ] 3.3 Add unit test: child session inherits parent's resource providers

## 4. ACPSession Lead Agent Attachment

- [ ] 4.1 Modify `ACPSession.initialize_mcp_servers()` in `agentpool_server/acp_server/session.py` to permanently attach created providers to `ACPSession.agent.tools`
- [ ] 4.2 Register the same providers into the eager `SessionState.resource_providers` via `self.agent_pool.session_pool.sessions.get_or_create_session(self.session_id)`
- [ ] 4.3 Ensure idempotency: `initialize_mcp_servers()` called twice does not duplicate providers (check `session_mcp_providers` already populated)
- [ ] 4.4 Add unit test: lead agent has providers attached after `initialize_mcp_servers()`
- [ ] 4.5 Add unit test: `initialize_mcp_servers()` is idempotent

## 5. Per-Session Agent Attachment (Native Only)

- [ ] 5.1 Modify `SessionController.get_or_create_session_agent()` in `agentpool/orchestrator/core.py` to attach `SessionState.resource_providers` to newly created per-session agents
- [ ] 5.2 Ensure attachment ONLY happens when `session.is_per_session_agent=True` (existing field, already set at core.py:502 for native agents)
- [ ] 5.3 Do NOT attach session providers to shared agents (non-native types) to avoid cross-session contamination
- [ ] 5.4 Ensure attachment happens after `agent.__aenter__()` and before returning
- [ ] 5.5 Add idempotency check: verify provider not already in `agent.tools.external_providers` before adding (use `id()` or `__eq__`)
- [ ] 5.6 Add unit test: per-session agent has session providers attached
- [ ] 5.7 Add unit test: shared agent does NOT receive session providers

## 6. Idempotency in ToolManager

- [ ] 6.1 Modify `ToolManager.add_provider()` in `agentpool/tools/manager.py` to check for duplicate providers before appending
- [ ] 6.2 Use provider identity (e.g., `id()` or `__eq__`) for deduplication
- [ ] 6.3 Add unit test: `add_provider()` ignores duplicates silently

## 7. Cleanup Temporary Injection

- [ ] 7.1 Remove temporary `add_provider` / `remove_provider` loop for `session_mcp_providers` in `ACPSession.process_prompt()` (`agentpool_server/acp_server/session.py`)
- [ ] 7.2 Remove temporary injection for `session_pool_agent` in the same method
- [ ] 7.3 Keep `ACPSession.session_mcp_providers` field for lead agent attachment and cleanup tracking
- [ ] 7.4 Add unit test: `process_prompt()` no longer temporarily injects providers for per-session agents

## 8. Provider Lifecycle Management

- [ ] 8.1 Verify `Agent.__aexit__()` (or concrete per-session agent class) does not close session-scoped providers
- [ ] 8.2 Modify `SessionController.close_session()` in `agentpool/orchestrator/core.py` to call `SessionState.close_resource_providers()` for the root session after popping it from `self._sessions` (check `session.parent_session_id is None`)
- [ ] 8.3 Keep safety-net cleanup in `SessionController._close_session_unlocked()` for direct calls, ensuring it only runs when `parent_session_id is None`
- [ ] 8.4 Modify `ACPSession.close()` to remove providers from `self.agent.tools` and delegate provider cleanup to `SessionState` via `self.agent_pool.session_pool.sessions.get_session(self.session_id).close_resource_providers()` (handles legacy ACP close path)
- [ ] 8.5 Ensure child session cleanup does NOT close inherited providers (skip if `parent_session_id is not None`)
- [ ] 8.6 Add unit test: per-session agent exit does not close session providers
- [ ] 8.7 Add unit test: root session cleanup closes providers via BOTH `SessionController.close_session()` and `ACPSession.close()` paths; child session cleanup does not

## 9. Agent Switching

- [ ] 9.1 Modify `ACPSession.switch_active_agent()` in `agentpool_server/acp_server/session.py` to:
  - (a) Remove session providers from the old lead agent's `ToolManager` (if native), mirroring the sys_prompt cleanup at session.py:528-531
  - (b) Permanently attach session providers to the new lead agent's `ToolManager` (if native), mirroring `initialize_mcp_servers()`
  - (c) Do NOT create a per-session agent in `switch_active_agent()` — per-session agents continue to receive providers via `SessionState` during `get_or_create_session_agent()` in future turns
- [ ] 9.2 Add unit test: agent switch preserves session provider access for native agents on new lead agent
- [ ] 9.3 Add unit test: agent switch removes providers from old lead agent
- [ ] 9.4 Add unit test: agent switch to non-native agent does not attach session providers

## 10. Session Resumption

- [ ] 10.1 Modify `ACPSessionManager.create_session()` to store `mcp_servers` config in `SessionState.metadata` (or equivalent session metadata) so it survives session resumption
- [ ] 10.2 Modify `ACPSessionManager.resume_session()` to retrieve stored `mcp_servers` config from session metadata before calling `initialize_mcp_servers()`
- [ ] 10.3 Ensure resumed sessions eagerly create `SessionState` and populate `resource_providers`
- [ ] 10.4 Add unit test: resumed session restores MCP provider access

## 11. Integration & Regression Tests

- [ ] 11.1 Add integration test: ACP session with MCP servers → lead agent can see tools → subagent can see same tools
- [ ] 11.2 Add integration test: nested subagents (child of child) inherit providers
- [ ] 11.3 Add integration test: concurrent tool calls from multiple agents through shared provider
- [ ] 11.4 Verify pool-level MCP providers still work (backward compatibility)
- [ ] 11.5 Verify tool shadowing: session-level tools override pool-level tools with same name (already implemented in ToolManager.get_tools() via dict deduplication)
- [ ] 11.6 Run full test suite: `uv run pytest tests/acp_server/ tests/orchestrator/ tests/toolsets/ -v`
- [ ] 11.7 Run type check: `uv run mypy src/`

## 12. Documentation

- [ ] 12.1 Update `AGENTS.md` or relevant docs to document session-scoped MCP provider behavior
- [ ] 12.2 Add code comments in `SessionState` and `get_or_create_session_agent()` explaining the inheritance mechanism
- [ ] 12.3 Document that session-level MCP is only supported for native per-session agents
