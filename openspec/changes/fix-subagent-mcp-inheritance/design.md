## Context

AgentPool has three levels of MCP providers:

1. **Pool-level** (from YAML `mcp_servers`): Added to the shared `base_agent` at pool startup. Always available to all sessions.
2. **Session-level** (from ACP `mcp-over-acp`): Added dynamically to a session's agent via `handler.py`. Scoped to the session.
3. **Agent-level** (from agent config): Per-agent MCP configuration.

Currently, `get_or_create_session_agent()` in `core.py` has an early return for child sessions (line 638): it returns the shared `base_agent` directly. This means child sessions only get pool-level MCP providers — not the parent's session-level ones (which are on the parent's per-session agent).

Previous attempt (`5749edaca`) copied parent's `kind=='mcp'` providers to `base_agent` before the early return. This worked for subagents but caused `CombinedToolset` `UserError` when another ACP session also added same-named MCP tools to `base_agent` — because `base_agent.tools.external_providers` is shared mutable state.

## Goals / Non-Goals

**Goals:**
- Child sessions (subagents) inherit parent's session-level MCP providers
- No mutation of the shared `base_agent.tools.external_providers`
- No new agent creation — avoid MCP subprocess duplication and lifecycle hazards
- Minimal code change (target: ~5 lines)
- Existing tests pass with updated assertions

**Non-Goals:**
- Non-MCP provider inheritance (only session-level MCP via agent sharing)
- Cross-pool MCP sharing
- Changing how pool-level MCP providers are managed

## Decisions

### Decision 1: Return parent's per-session agent for child sessions

**Chosen**: In `get_or_create_session_agent`, when `session.parent_session_id` is set, look up the parent session's agent (`parent_state.agent`) and return it instead of `base_agent`. If the parent uses the shared agent (MCP limit reached or non-native config), fall back to `base_agent`.

**Rationale**: The parent's per-session agent already has all session-level MCP providers registered. By sharing the agent, the child automatically inherits them — no copying, no dedup, no new agent creation. This is a ~5-line change with zero MCP lifecycle risk.

**Alternatives considered**:
- **Per-session agent for child + shared MCP manager**: Rejected due to MCP lifecycle hazards — `close_session()` calls `agent.__aexit__()` which would close the shared MCP manager, breaking all sessions. Also introduces MCP subprocess duplication and complex cleanup bypass logic.
- **Per-session agent for child + independent MCP**: Rejected because each child would spawn duplicate MCP subprocesses for pool-level servers, wasting resources and risking MCP process limit exhaustion.
- **Tool-name dedup on `base_agent`**: Rejected because MCP-over-ACP connections are session-scoped — different sessions need different connections for the same MCP server.

### Decision 2: Set `is_per_session_agent = False` for child sessions

**Chosen**: Child sessions retain `is_per_session_agent = False` (already the case at line 660). Cleanup responsibility stays with the parent session.

**Rationale**: Since child shares the parent's agent, `close_session()` on the child must NOT call `agent.__aexit__()`. The `is_per_session_agent = False` flag already ensures this at line 946.

### Decision 3: Remove dead `resource_providers` sync

**Chosen**: Remove the `session_state.resource_providers = list(...)` assignment at handler.py line 313-317. It was intended for child session inheritance but was never consumed.

**Rationale**: With agent sharing, inheritance happens through the agent reference, not through attribute copying. The `resource_providers` dynamic attribute is dead code.

## Risks / Trade-offs

- **[Risk] Parent agent being `base_agent`**: If parent fell back to shared agent (MCP limit reached or non-native config), child also gets `base_agent` without parent's session-level providers — same behavior as today, no regression.
- **[Risk] Parent closing before child**: `close_session()` closes child sessions when parent closes (line 907-916, unless `lifecycle_policy == "independent"`). Since child shares parent's agent and has `is_per_session_agent = False`, only parent's close triggers cleanup — correct behavior.
- **[Risk] Duplicate provider instances from handler**: If the child session's own `session_mcp_providers` include the same MCP servers as the parent, the handler's `provider not in` check (line 309, identity-based) won't catch different instances. However, in practice, child sessions inherit parent's MCP servers — they don't create duplicates.
