## Why

AgentPool's MCP tool pipeline has three conflicting paths that cause subagents to lose access to pool-level MCP servers (e.g., `search_kb` from `knowledge_base`). The root cause is `core.py:1005` where `agent.mcp = parent_agent.mcp` overrides the correct `pool.mcp` assignment from `messagenode.py:134-139`, causing subagents to inherit the parent's agent-level MCPManager instead of the pool-level one.

Additionally, `MCPManager.as_capability()` creates fresh `MCP` capability instances (each spawning a new `MCPToolset` and `FastMCPClient`) on every call — wasting connections. With engineer + 5 librarian subagents and 2 servers, this produces 12 connections instead of the optimal 2.

pydantic-ai's `MCPToolset` (the current recommended class, NOT deprecated) provides ref-counted `__aenter__`/`__aexit__` via `_running_count`, making it safe to share a single instance across multiple agents. The `MCP` capability accepts pre-built `MCPToolset` instances via its `local=` parameter, enabling connection reuse without any pydantic-ai changes.

## What Changes

- **Fix subagent MCP assignment**: Remove `agent.mcp = parent_agent.mcp` (`core.py:1005`). Subagents correctly use `pool.mcp` (shared MCPManager) as assigned by `messagenode.py:134-139`.
- **Add `MCPToolset` caching to `MCPManager`**: Cache `MCPToolset` instances per server in `_toolset_cache`. `as_capability()` (changed to `async def`) returns `MCP(local=cached_toolset)` instead of creating new capabilities each call. The MCPManager enters each toolset via `await exit_stack.enter_async_context()` for persistent connections. Ref-counted lifecycle handles concurrent agents safely.
- **Split aggregating provider by transport**: `get_aggregating_provider()` returns only ACP-transport providers. Non-ACP providers are handled exclusively by `as_capability()`. This eliminates the dedup hack in `get_agentlet()`.
- **Remove parent MCP provider inheritance (Pipeline 3)**: Delete `core.py:1030-1035` where parent's `kind=='mcp'` external_providers are copied to subagents. Subagents get pool-level MCP via `pool.mcp`, not via parent inheritance.
- **Remove unconditional Pipeline 2 for non-ACP servers**: The 4 locations (`core.py:1073-1082, 1105-1113, 2141-2150, 2190-2199`) now only add ACP providers (from the split aggregating provider). The `_mcp_shared` guard and `MCPConnectionPool` fallback are no longer needed.
- **Remove dedup hack**: `agent.py:749-760` (`if provider is mcp_aggregating: continue`) is no longer needed since the aggregating provider only contains ACP providers, which don't overlap with `as_capability()` output.
- **Remove `MCPConnectionPool`**: `MCPManager` with `MCPToolset` caching provides the same connection reuse. Delete `MCPConnectionPool`, its `initialize()` method, and all `mcp_pool` references.

## Capabilities

### Modified Capabilities

- `subagent-mcp-inheritance`: Child sessions inherit pool-level MCP providers via the shared `pool.mcp` MCPManager (assigned in `messagenode.py`), not via parent agent's MCPManager. Agent-level MCP servers remain scoped to the defining agent. Pool-level MCP tools are available to all agents including subagents.

## Impact

- **Affected code**:
  - `src/agentpool/orchestrator/core.py` — Remove `agent.mcp = parent_agent.mcp` (line 1005), remove Pipeline 3 (lines 1030-1035), simplify 4 Pipeline 2 locations, remove `MCPConnectionPool` creation (line 1919) and `initialize()` call
  - `src/agentpool/agents/native_agent/agent.py` — Remove dedup hack (lines 749-760), add `pool.mcp.as_capability()` call when agent shares pool MCPManager
  - `src/agentpool/mcp_server/manager.py` — Add `_toolset_cache` and modify `as_capability()` to use cached `MCPToolset` instances, modify `get_aggregating_provider()` to filter ACP-only
  - `src/agentpool/mcp_server/connection_pool.py` — Deprecate/remove (subsumed by `MCPManager` with caching)
  - `src/agentpool/messaging/messagenode.py` — No change (already correct)
- **No API changes**: Public API and YAML config format unchanged
- **No new dependencies**: Uses existing `pydantic_ai.mcp.MCPToolset`
- **Breaking changes**: None for users. Internal `MCPConnectionPool` removal is a private API change.
- **Connection reduction**: Engineer + 5 librarian subagents with 2 servers: 12 connections → 2 connections
