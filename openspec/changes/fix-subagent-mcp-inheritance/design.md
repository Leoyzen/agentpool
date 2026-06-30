## Context

AgentPool has three levels of MCP providers and three conflicting tool pipelines that deliver MCP tools to agents. The current implementation has a bug where subagents lose access to pool-level MCP servers, and connections are wasted due to lack of caching.

### Three MCP Levels

1. **Pool-level** (from YAML `mcp_servers`): Managed by `pool.mcp` (`MCPManager`), shared across all agents and sessions. Example: `knowledge_base` server providing `search_kb`.
2. **Session-level** (from ACP `mcp-over-acp`): Managed by `ACPSession.initialize_mcp_servers()`, scoped to a session. Example: `workspace-fs`, `agentic-scratchpad`.
3. **Agent-level** (from agent config `mcp_servers`): Managed by agent's own `MCPManager`, scoped to that agent only. Example: engineer's `expert-anno` providing `request_comment`.

### Three Conflicting Pipelines (Current)

**Pipeline 1** — `MCPManager.as_capability()` (`manager.py:237-298`): Creates pydantic-ai `MCP` capabilities with `allowed_tools` filtering, no name prefixing. Skips ACP transport servers. Called in `get_agentlet()` at `agent.py:817`. Each call creates fresh `MCP` capability instances (new `MCPToolset` → new `FastMCPClient` → new connection).

**Pipeline 2** — `AggregatingResourceProvider` → `FunctionTool`s: `agent.tools.add_provider(mcp_pool.get_aggregating_provider())` adds pool-level MCP providers as direct FunctionTools with prefixed names. Used at 5 locations in `core.py` (lines 1019-1025 subagent path, 1073-1082 main path, 1105-1113 non-native path, 2141-2150 native agent path, 2190-2199 ACP agent path). The subagent path (line 1019) is guarded by `_mcp_shared` flag.

**Pipeline 3** — Parent agent's MCP providers: `core.py:1030-1035` copies parent's `kind=='mcp'` external_providers to subagent. This causes subagents to inherit parent's agent-level MCPManager (e.g., engineer's `expert-anno`) instead of pool-level servers.

### The Subagent Bug

`messagenode.py:134-139` correctly assigns `self.mcp = agent_pool.mcp` when an agent has no agent-level MCP servers. But `core.py:1005` overrides this with `agent.mcp = parent_agent.mcp`, making subagents inherit the parent's MCPManager. When engineer (has own `mcp_servers`: expert-anno) spawns a librarian subagent, the librarian gets `expert-anno` instead of `knowledge_base`.

### pydantic-ai MCPToolset (Best Practice)

`MCPToolset` (`pydantic_ai/mcp.py:670`) is the current recommended class for MCP server connections in pydantic-ai (NOT deprecated — it replaced the old `MCPServer*` classes in V2). Key properties:

- **Ref-counted lifecycle**: `__aenter__`/`__aexit__` use `_running_count` with `anyio.Lock`. First entry opens the connection; last exit closes it. Nested enters are safe.
- **Pre-built client acceptance**: Constructor accepts a `fastmcp.Client` instance directly, storing it as `self.client`.
- **MCP capability wrapping**: `MCP(local=toolset)` accepts a pre-built `MCPToolset` instance without wrapping.
- **Tool caching**: `cache_tools=True` (default) caches tool lists, invalidated by `notifications/tools/list_changed` or last `__aexit__`.
- **Sharing pattern**: pydantic-ai docs explicitly recommend sharing `MCPToolset` across agents: "use `async with toolset` to manage the lifecycle of a specific toolset directly, for example if you'd like to share it across multiple agents."

**Caveat**: When passing a pre-built `fastmcp.Client`, the internal cache-invalidation message handler is NOT installed. Caches are only invalidated by session close. This is acceptable for pool-level servers where tool lists rarely change.

## Goals / Non-Goals

**Goals:**
- Subagents correctly access pool-level MCP servers (e.g., `search_kb`)
- Agent-level MCP servers remain scoped to their defining agent (not inherited by subagents)
- Connection reuse: one `MCPToolset` per server, shared across all agents
- Single MCP tool pipeline for non-ACP servers (Pipeline 1 only)
- ACP transport servers continue working via FunctionTool path
- Minimal breaking changes to public API

**Non-Goals:**
- Changing ACP session server lifecycle (they remain per-session via `ACPSession`)
- Changing YAML config format
- Adding new MCP transport types
- Modifying pydantic-ai's `MCPToolset` class

## Decisions

### Decision 1: Remove `agent.mcp = parent_agent.mcp` override

**Chosen**: Delete `core.py:1005` (`agent.mcp = parent_agent.mcp`) and the associated `agent._mcp_shared = True` (line 1006).

**Rationale**: `messagenode.py:134-139` already correctly assigns `self.mcp = pool.mcp` for agents without agent-level MCP servers. The override at line 1005 breaks this by sharing the parent's MCPManager. Removing it lets subagents use `pool.mcp` naturally.

**Risk**: Subagents that relied on inheriting parent's agent-level MCP servers will lose access. This is correct behavior — agent-level servers should not leak to subagents. If a subagent needs an MCP server, it should be configured at pool level.

### Decision 2: Add `MCPToolset` caching to `MCPManager`

**Chosen**: Add `_toolset_cache: dict[str, MCPToolset]` to `MCPManager`. In `as_capability()`, create or reuse `MCPToolset` instances per server:

```python
async def as_capability(self) -> list[MCP]:
    capabilities = []
    for server in self.servers:
        if not server.enabled or isinstance(server, AcpMCPServerConfig):
            continue
        cache_key = server.client_id
        if cache_key not in self._toolset_cache:
            pydantic_server = server.to_pydantic_ai(...)
            toolset = MCPToolset(
                client=pydantic_server,  # or fastmcp.Client
                id=server.name or server.client_id,
            )
            # Enter via exit_stack for persistent connection (MCPManager holds ref-count 1)
            # This keeps the connection open between agent runs.
            # pydantic-ai agents add their own ref when entering MCP(local=toolset).
            await self.exit_stack.enter_async_context(toolset)
            self._toolset_cache[cache_key] = toolset
        toolset = self._toolset_cache[cache_key]
        cap = MCP(
            local=toolset,
            allowed_tools=server.enabled_tools,
            id=server.name or server.client_id,
        )
        capabilities.append(cap)
    return capabilities
```

**CRITICAL**: `as_capability()` must be changed from sync (`def`) to async (`async def`). The `enter_async_context()` method on `AsyncExitStack` is async — calling it without `await` from a sync method creates a coroutine that is never executed, leaving the toolset unentered. The sole call site is `agent.py:817` in `get_agentlet()` (which is already async): change `mcp_capabilities = self.mcp.as_capability()` to `mcp_capabilities = await self.mcp.as_capability()`.

**Rationale**: `MCPToolset`'s ref-counted `__aenter__`/`__aexit__` makes sharing safe. The MCPManager holds ref-count 1 (persistent connection) via `await exit_stack.enter_async_context(toolset)`. When pydantic-ai agents enter the same `MCPToolset` via `MCP(local=toolset)`, ref-count goes to 2+. When agents exit, ref-count drops back to 1 (connection stays open). Pool shutdown exits via `exit_stack.aclose()`, dropping ref-count to 0 (connection closes). This reduces connections from 12 to 2 for the engineer + 5 librarian scenario.

**Cache key**: `server.client_id` — unique per server config. Two agents referencing the same pool-level server share the same `MCPToolset`.

**Lifecycle**: The MCPManager enters each MCPToolset via `await exit_stack.enter_async_context(toolset)` when first created in `as_capability()` (lazy initialization). `as_capability()` must be `async def` (changed from sync). The MCPManager's `cleanup()` method calls `exit_stack.aclose()` which exits all toolsets. Since `pool.mcp` is shared and lives as long as the pool, connections persist across agent lifecycles. pydantic-ai agents add/remove their own refs via the `MCP` capability's `__aenter__`/`__aexit__`.

**Cache invalidation caveat**: When using a pre-built `fastmcp.Client`, the `list_changed` notification handler is NOT installed. Tool list changes require pool restart. This is acceptable for pool-level config servers (rarely change). For session-level ACP servers, the existing `MCPResourceProvider` path handles invalidation.

### Decision 3: Split aggregating provider — ACP-only

**Chosen**: Modify `MCPManager.get_aggregating_provider()` to return an `AggregatingResourceProvider` containing only ACP-transport providers. Non-ACP providers are handled exclusively by `as_capability()`.

**Rationale**: Currently the aggregating provider contains BOTH ACP and non-ACP providers. This requires the dedup hack in `get_agentlet()` (lines 749-760) to skip it, preventing double-registration of non-ACP tools. By splitting:
- ACP providers → aggregating provider → `FunctionTool`s (Pipeline 2, for ACP only)
- Non-ACP providers → `as_capability()` → pydantic-ai `MCP` capabilities (Pipeline 1)

The dedup hack becomes unnecessary.

**Implementation**: In `get_aggregating_provider()`, filter providers by transport type:

```python
def get_aggregating_provider(self) -> AggregatingResourceProvider:
    acp_providers = [
        p for p in self._providers
        if isinstance(p.client.config, AcpMCPServerConfig)
    ]
    return AggregatingResourceProvider(providers=acp_providers)
```

### Decision 4: Remove Pipeline 3 (parent MCP provider inheritance)

**Chosen**: Delete `core.py:1030-1035` where parent's `kind=='mcp'` external_providers are copied to subagent.

**Rationale**: With Decision 1, subagents use `pool.mcp` (shared MCPManager). Pool-level MCP tools come through `as_capability()` in `get_agentlet()`. Agent-level MCP servers remain on the parent's MCPManager and are correctly NOT inherited. Pipeline 3 is redundant and causes tool name conflicts.

### Decision 5: Simplify Pipeline 2 locations

**Chosen**: The 4 unconditional Pipeline 2 locations (`core.py:1073-1082, 1105-1113, 2141-2150, 2190-2199`) now add only ACP providers (from the split aggregating provider). Remove the `MCPConnectionPool` fallback pattern:

```python
# BEFORE:
agent.tools.add_provider(
    self._mcp_pool.get_aggregating_provider()
    if self._mcp_pool is not None
    else self.pool.mcp.get_aggregating_provider()
)

# AFTER:
agent.tools.add_provider(self.pool.mcp.get_aggregating_provider())
```

The subagent path (lines 1019-1025) with the `_mcp_shared` guard is removed — but only the MCP provider block. Skills providers (`skills_instruction_provider`, `skills_tools_provider`) that share the same `if self.pool is not None:` block are kept.

### Decision 6: Remove dedup hack in `get_agentlet()`

**Chosen**: Remove `agent.py:749-760` (`if provider is mcp_aggregating: continue`).

**Rationale**: With Decision 3, the aggregating provider only contains ACP providers. ACP providers' `as_capability()` returns a `Toolset` of `FunctionTool`s (not `None`), so they pass through the normal capability collection loop. Non-ACP providers are no longer in the aggregating provider, so there's no double-registration risk.

### Decision 7: Add `pool.mcp.as_capability()` in `get_agentlet()`

**Chosen**: In `get_agentlet()` at `agent.py:816-818`, keep the existing `self.mcp.as_capability()` call. When the agent shares `pool.mcp` (`_mcp_shared = True`), this returns pool-level MCP capabilities. When the agent has its own MCPManager, this returns agent-level capabilities.

**Rationale**: With Decision 1, subagents correctly have `self.mcp = pool.mcp`. So `await self.mcp.as_capability()` in `get_agentlet()` returns pool-level MCP capabilities for subagents. The call site at `agent.py:817` must be updated to use `await` since `as_capability()` is now async (see Decision 2). No additional `pool.mcp.as_capability()` call is needed — the existing call handles both cases.

### Decision 8: Remove `MCPConnectionPool`

**Chosen**: Remove `MCPConnectionPool` class, its `initialize()` method, and all references to `mcp_pool` in `core.py`.

**Rationale**: `MCPConnectionPool` was added to share MCP subprocess connections across sessions. With `MCPToolset` caching in `MCPManager` (Decision 2), the same connection reuse is achieved. `MCPConnectionPool` becomes redundant.

**Migration**: All `self._mcp_pool.get_aggregating_provider()` calls become `self.pool.mcp.get_aggregating_provider()`. The `MCPConnectionPool` creation at `core.py:1919` and `initialize()` call are removed.

## Risks / Trade-offs

- **[Risk] Agent-level MCP servers not inherited by subagents**: This is correct behavior. If a subagent needs an MCP server, configure it at pool level. Agent-level servers are scoped to the defining agent.
- **[Risk] `MCPToolset` cache invalidation**: Pool-level config servers rarely change tool lists. If they do, a pool restart clears the cache. Session-level ACP servers use the existing `MCPResourceProvider` path with its own invalidation.
- **[Risk] `MCPToolset` ref-counting bugs**: If `__aenter__`/`__aexit__` calls are unbalanced, ref-counting breaks. pydantic-ai's `CombinedToolset` and `Agent` both use `AsyncExitStack` which guarantees balanced calls.
- **[Trade-off] Pool-level `MCPManager` lifecycle**: The `MCPToolset` cache lives as long as `pool.mcp`. This is correct — pool-level connections should persist for the pool's lifetime.
- **[Trade-off] No per-session MCP for non-ACP**: Non-ACP MCP servers are always pool-level. Session-scoped non-ACP servers are not supported (and never were — ACP is the session-scoped transport).
- **[Risk] Dynamic MCP server addition/removal**: The `_toolset_cache` does not invalidate when servers are added/removed at runtime. Pool-level config servers are static (loaded from YAML at startup), so this is a known limitation. A pool restart clears the cache.
- **[Risk] MCP server restart/connection failure**: If an MCP server crashes and restarts, the cached `MCPToolset` has a stale connection. The `MCPToolset` ref-counting does not handle connection failures. Agents will get errors until the pool is restarted. This is a pre-existing limitation — the old `MCPResourceProvider` path had the same issue.
- **[Risk] Pool shutdown with running agents**: If `pool.mcp.__aexit__` runs while agents are still active, `exit_stack.aclose()` exits cached toolsets (ref-count drops to 0), closing connections out from under running agents. This is a shutdown race condition, pre-existing in the current architecture (MCPResourceProviders are also closed during pool shutdown).

## Connection Count Comparison

Scenario: engineer agent + 5 librarian subagents, 2 MCP servers (knowledge_base + expert-anno)

| Approach | Connections |
|----------|-------------|
| Current (3 pipelines, no cache) | 12 |
| Unified, no cache | 7 |
| Unified + MCPToolset cache | **2** |

## Key Code Locations (Post-Change)

| Location | File | Change |
|----------|------|--------|
| MCP fork point | `messagenode.py:127-139` | No change (already correct) |
| Subagent creation | `core.py:998-1006` | Remove `agent.mcp = parent_agent.mcp` and `_mcp_shared = True` |
| Pipeline 2 (subagent) | `core.py:1019-1025` | Remove entirely (subagents get pool.mcp naturally) |
| Pipeline 3 | `core.py:1030-1035` | Remove entirely |
| Pipeline 2 (main) | `core.py:1073-1082, 1105-1113` | Simplify to `self.pool.mcp.get_aggregating_provider()` |
| Pipeline 2 (native) | `core.py:2141-2150` | Simplify to `self.pool.mcp.get_aggregating_provider()` |
| Pipeline 2 (ACP) | `core.py:2190-2199` | Simplify to `self.pool.mcp.get_aggregating_provider()` |
| Dedup hack | `agent.py:749-760` | Remove entirely |
| MCP capabilities | `agent.py:816-818` | Update call to `await self.mcp.as_capability()` (now async) |
| MCPManager.as_capability | `manager.py:237-298` | Change to `async def`, add `_toolset_cache`, `await exit_stack.enter_async_context(toolset)`, return `MCP(local=cached_toolset)` |
| get_aggregating_provider | `manager.py` | Filter ACP-only providers |
| MCPConnectionPool | `connection_pool.py` | Remove (delete file) |
| MCPConnectionPool creation | `core.py:1919` | Remove |
| MCPConnectionPool initialize | `core.py:1925-1926` | Remove |
