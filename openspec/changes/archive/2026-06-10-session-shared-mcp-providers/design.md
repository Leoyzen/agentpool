## Context

### Current Architecture

AgentPool has a **three-tier MCP provider model**:

1. **Pool-level** (`AgentPool.mcp`): Servers from manifest `mcp_servers`. Added to all registered agents via `AgentPool.__aenter__()`. Already fixed for per-session agents by commit `31f0e2a73`.
2. **Agent-config level** (`NativeAgentConfig.mcp_servers`): Per-agent YAML config. Passed to `Agent.__init__()` via `all_mcp_servers`.
3. **Session-level** (`ACPSession.session_mcp_providers`): Dynamically created per ACP session for MCP-over-ACP servers. Currently stored on the `ACPSession` object and **temporarily injected** into the agent during `ACPSession.process_prompt()`.

The problem is in tier 3. The temporary injection pattern works for the lead agent but fails for:
- **Subagents**: `SubagentTools.task()` creates a child session with a new `session_id`. The child session's per-session agent is created by `SessionController.get_or_create_session_agent()`, which has no knowledge of the parent's `session_mcp_providers`.
- **Per-session agents**: Even within the same session, the temporary add/remove in `process_prompt()` means providers are gone after the turn ends.

### SessionState Lifecycle Ordering

A critical timing constraint: `SessionState` is created **lazily** during `run_stream()`, not during ACP session creation:

```
ACPSessionManager.create_session()
  ├─→ ACPSession.__init__(agent=shared_pool_agent)
  ├─→ session.initialize()
  ├─→ session.initialize_mcp_servers()     ← SessionState does NOT exist yet
  └─→ return session

[later]
ACPSession.process_prompt()
  ├─→ self.agent.run_stream(session_id=...)
        ├─→ SessionPool.create_session(session_id)  ← SessionState created HERE
        └─→ get_or_create_session_agent(session_id) ← per-session agent created HERE
```

This means `initialize_mcp_servers()` cannot directly register providers into `SessionState`. The design must handle this ordering gap.

### Why ContextVar Alone Is Insufficient

AgentPool already uses `_current_run_ctx_var` (a `ContextVar`) for per-run state (RFC-0021). A ContextVar could theoretically carry session MCP providers, but it has critical limitations:
- **Scope**: A ContextVar is task-local. Child sessions created by `create_child_session()` may run in different tasks.
- **Lifetime**: ContextVar values are garbage-collected when the task ends. Session-level providers need to outlive individual turns.
- **Discoverability**: `get_or_create_session_agent()` runs in SessionPool code, not inside the agent's run loop. It cannot easily access the agent's ContextVar.

Therefore, the solution must be **data-model-level** (SessionState) rather than **execution-context-level** (ContextVar).

## Goals / Non-Goals

**Goals:**
- Make session-level MCP providers visible to all agents in the session tree (lead, subagent, per-session).
- Eliminate the temporary add/remove anti-pattern for per-session agents.
- Ensure provider lifecycle is tied to the root session, not individual agent instances.
- Maintain backward compatibility with pool-level and agent-config-level MCP.

**Non-Goals:**
- Changing the MCP-over-ACP transport protocol (RFC-0033) or connection chain (RFC-0035).
- Supporting per-agent-isolated MCP sessions (each agent gets its own connection). This is intentionally out of scope; session-level providers are shared.
- Modifying the ACP client's `mcp/connect` behavior.
- Changing how pool-level MCP providers work.
- Adding session-level MCP support to non-native shared agents (ACP, ClaudeCode). Pool-level MCP remains available for these.

## Decisions

### Decision 1: Store providers in `SessionState` (not ContextVar)
**Rationale**: `SessionState` is the canonical per-session data container managed by `SessionPool`. It already supports parent-child inheritance (`parent_session_id`). Adding `resource_providers` there makes providers inheritable by child sessions without any extra propagation logic.

**Alternative considered**: Use a new `ContextVar` for session providers. Rejected because ContextVars are task-scoped and don't survive across `create_child_session()` boundaries.

### Decision 2: Attach providers in `get_or_create_session_agent()` (not in agent constructor)
**Rationale**: Per-session agents are created dynamically by `SessionController.get_or_create_session_agent()`. This is the single chokepoint where all per-session agents pass through. Attaching providers here ensures consistency without modifying `Agent.__init__()` or `NativeAgentConfig.get_agent()`.

**Alternative considered**: Pass `mcp_servers` into `cfg.get_agent()`. Rejected because it would couple agent config to session state and require changes to every agent type's constructor.

### Decision 3: Shared provider instances across agents in the same session
**Rationale**: `MCPResourceProvider` wraps a `fastmcp.Client` which uses JSON-RPC `request_id` for concurrent request routing. Multiple agents calling tools through the same provider instance is safe for ACP-transport (memory streams) and avoids redundant connections. This aligns with the existing design where `ACPSession.initialize_mcp_servers()` creates one provider per MCP server.

**Caveat**: stdio-based MCP providers may not be safe for concurrent interleaved writes. This is a pre-existing limitation, not introduced by this change.

**Alternative considered**: Clone providers for each agent. Rejected because it would create redundant MCP connections and violate the principle of one `connectionId` per ACP session.

### Decision 4: Two-source model — ACPSession for lead agent, SessionState for child sessions
**Rationale**: The lead agent (`ACPSession.agent`) is the shared pool agent passed directly to `ACPSession.__init__()` — it never goes through `get_or_create_session_agent()`. A single-source model (SessionState only) would break the lead agent's MCP access. Therefore:
- `ACPSession` continues to manage `session_mcp_providers` for the lead agent, but attaches them **permanently** (not temporarily).
- `SessionState.resource_providers` serves child sessions and per-session agents, inherited via `SessionPool.create_session()`.
- `get_or_create_session_agent()` attaches `SessionState.resource_providers` to per-session native agents.

**Trade-off**: Shared agents (non-native types like ACPAgent or ClaudeCodeAgent) do NOT receive session-level MCP providers permanently. For these agents, only pool-level MCP is available. This is acceptable because:
1. Non-native agents have their own MCP mechanisms.
2. Permanently mutating shared agents would cause cross-session contamination.
3. The primary use case (native subagents) is fully covered.

### Decision 5: Eager SessionState creation
**Rationale**: To bridge the timing gap between `initialize_mcp_servers()` and lazy `SessionState` creation, we eagerly create `SessionState` in `ACPSessionManager.create_session()` before calling `initialize_mcp_servers()`. `SessionPool.create_session()` already delegates to `get_or_create_session()`, which is idempotent — if a `SessionState` already exists for the `session_id`, it returns the existing one. This allows providers to be registered into `SessionState` immediately without risk of duplicate creation when `run_stream()` later calls `create_session()` again.

**Alternative considered**: Deferred bridge — have `SessionPool.create_session()` copy providers from `ACPSession` when `SessionState` is eventually created. Rejected because it's more complex and error-prone (race conditions between provider creation and session creation).

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Lead agent provider attachment leaks** | High | `ACPSession.agent` is a shared pool agent. Permanent attachment means the agent carries session providers even after the ACP session ends. Mitigation: `ACPSession.close()` must remove providers from `self.agent.tools` during cleanup. |
| **Shared agent contamination** | High | If code accidentally attaches session providers to shared agents in `get_or_create_session_agent()`, those providers leak to all sessions. Mitigation: only attach when `is_per_session_agent=True` (existing field on `SessionState`, set at line 502 of `core.py`). |
| **Provider not thread-safe** | Medium | `fastmcp.Client` (ACP-transport) uses monotonic `request_id` and async-safe memory streams. stdio transport may not be safe for concurrent interleaved writes. Document this limitation. |
| **Double-close / child session leak** | Medium | Child sessions inherit provider references. If child cleanup calls `__aexit__()`, it disconnects for parent too. Mitigation: root session owns cleanup; child sessions only reference. Check `parent_session_id` in `SessionState` to identify root. |
| **Duplicate provider registration** | Medium | `ToolManager.add_provider()` blindly appends. Mitigation: add idempotency check (by provider identity, e.g., `id()` or `__eq__`) before appending. |
| **Agent switching loses providers** | Medium | `switch_active_agent()` replaces `self.agent`. New agent won't have providers. Mitigation: call `get_or_create_session_agent()` for the new native agent, and remove session providers from the old agent (mirroring existing sys_prompt cleanup at session.py:528-531). |
| **Old agent provider leak on switch** | Medium | When switching agents, the old agent retains session providers. If it's a shared pool agent, those providers persist. Mitigation: remove session providers from old agent in `switch_active_agent()`, similar to how `get_cwd_context` is removed today. |
| **Session resumption loses MCP** | Medium | Resumed sessions pass `mcp_servers=None`. Mitigation: re-initialize MCP servers on resume, or persist config in session metadata. |
| **Performance: provider lookup** | Low | `get_or_create_session_agent()` adds O(N) iteration. N = MCP servers (< 10 typically). Negligible impact. |

## Migration Plan

1. **Phase 1 (This change)**: Implement `SessionState.resource_providers` + inheritance + `get_or_create_session_agent()` attachment (per-session only). Eager SessionState creation. Remove temporary injection for per-session agents. Permanent attachment for lead agent.
2. **Phase 2 (Future)**: If per-agent-isolated MCP sessions are needed, add a `share_mcp: bool` flag to `SessionState` or agent config. Out of scope for now.

## Open Questions

1. **Should `SessionState` support non-MCP `ResourceProvider` types?** The proposal uses `ResourceProvider` as the type, which is the base class. This is correct — the mechanism is generic and not limited to MCP.
2. **How to handle provider cleanup on session force-close?** `SessionPool.close_session()` should iterate `resource_providers` and call `__aexit__()` or a close method. `MCPResourceProvider` has `__aenter__/__aexit__`. Need to ensure cleanup is idempotent.
3. **Does `AgentPool.__aexit__()` need to clean up session providers?** No — session providers are owned by `SessionState`, which is cleaned up by `SessionPool`. Pool-level providers are managed by `AgentPool.mcp`.
4. **What happens to lead agent providers when ACP session closes?** `ACPSession.close()` must call `self.agent.tools.remove_provider()` for each session provider. Need to verify this doesn't break pool-level providers.
5. **How to handle session resumption?** Resumed sessions (`mcp_servers=None`) need to re-initialize MCP servers. Should this be done in `ACPSessionManager.resume_session()` or `ACPSession.initialize()`?
