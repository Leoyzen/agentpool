## Why

MCP-over-ACP services are currently only visible to the lead agent handling an ACP session. When a subagent is spawned (via `SubagentTools.task()` or Team execution), the child session gets a fresh agent instance that has no access to the parent's session-level MCP providers. This breaks the expectation that an ACP client's configured MCP servers should be available throughout the entire session tree — not just the root turn.

The root cause is that `ACPSession.session_mcp_providers` are stored on the `ACPSession` object itself and are only temporarily injected into the agent during `ACPSession.process_prompt()`. There is no mechanism to propagate these providers to child sessions or per-session agents created by `SessionController.get_or_create_session_agent()`.

This change introduces a **two-source model**: `ACPSession` manages providers for the lead agent, while `SessionState` propagates providers to child sessions and per-session agents. This preserves existing ACP behavior while solving the subagent visibility problem.

## What Changes

- **Extend `SessionState`** with a `resource_providers: list[ResourceProvider]` field that holds session-scoped MCP providers.
- **Modify `SessionPool.create_session()`** to inherit `resource_providers` from parent sessions when `parent_session_id` is provided.
- **Modify `ACPSession.initialize_mcp_servers()`** to register created `MCPResourceProvider` instances into the corresponding `SessionState` (via eager creation or deferred bridge), and **permanently attach** them to `ACPSession.agent.tools`.
- **Modify `SessionController.get_or_create_session_agent()`** to attach `SessionState.resource_providers` to newly created **per-session** native agents only. Shared agents (non-native types) do NOT receive permanent attachment to avoid cross-session contamination.
- **Remove the temporary add/remove pattern** in `ACPSession.process_prompt()` for per-session agents — session-level providers are now permanently attached via `SessionState`, eliminating the lifecycle mismatch.
- **Add idempotency protection** to prevent duplicate provider registration when `get_or_create_session_agent()` is called multiple times.
- **Ensure provider lifecycle** is tied to the root session. Child sessions inherit references, not ownership. Only the root session cleans up providers.
- **Handle agent switching**: `ACPSession.switch_active_agent()` directly attaches session providers to the new lead agent's `ToolManager` and removes them from the old lead agent. Per-session agents continue to receive providers via `SessionState` in future turns.
- **Ensure pool-level cleanup** does not close session-scoped providers when per-session agents exit — their lifecycle is tied to the session, not the agent.

## Capabilities

### New Capabilities
- `session-shared-mcp-providers`: Session-scoped resource provider inheritance across the session tree, enabling MCP-over-ACP and other session-level providers to be visible to all agents (lead, subagent, per-session) within the same session hierarchy.

### Modified Capabilities
<!-- No existing spec-level requirements are changing; this is a new capability that builds on existing session and MCP infrastructure. -->

## Impact

- `agentpool/sessions/models.py` — `SessionState` dataclass extended
- `agentpool/orchestrator/core.py` — `SessionPool.create_session()` inheritance + `SessionController.get_or_create_session_agent()` provider attachment (per-session agents only)
- `agentpool_server/acp_server/session.py` — `ACPSession.initialize_mcp_servers()` permanent attachment to lead agent + registration to `SessionState`; `process_prompt()` cleanup of temporary injection for per-session agents; `switch_active_agent()` provider re-attachment
- `agentpool_server/acp_server/session_manager.py` — eager `SessionState` creation before `initialize_mcp_servers()`, or bridge mechanism
- `agentpool/agents/base_agent.py` — verify per-session agent `__aexit__` does not close session-scoped providers; add idempotency check to `ToolManager.add_provider()`
- `agentpool/tools/manager.py` — idempotency check before appending to `external_providers`
- Tests: `tests/acp_server/test_session.py`, `tests/orchestrator/test_session_pool.py`, `tests/toolsets/test_subagent_tools.py`

## Key Constraints

1. **Lead agent (`ACPSession.agent`) is a shared pool agent** — it is passed directly to `ACPSession.__init__()` and never goes through `get_or_create_session_agent()`. Session providers for the lead agent must be attached by `ACPSession` itself.
2. **SessionState does not exist when `initialize_mcp_servers()` runs** — `SessionState` is created lazily during `run_stream()`. Either eagerly create it in `ACPSessionManager.create_session()` or bridge providers later.
3. **Shared agents must not be permanently mutated** — attaching session providers to a shared `ACPAgent` or `ClaudeCodeAgent` would leak those providers into all sessions using that agent.
4. **Provider cleanup must be reference-counted** — child sessions inherit provider references. Only the root session (the one that called `initialize_mcp_servers()`) should close providers.
