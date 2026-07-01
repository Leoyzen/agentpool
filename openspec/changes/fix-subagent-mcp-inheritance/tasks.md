## 1. Fix Subagent MCP Assignment

- [x] 1.1 In `orchestrator/core.py` subagent creation path (~line 1005): Remove `agent.mcp = parent_agent.mcp` and `agent._mcp_shared = True`. The agent's `mcp` attribute retains the value assigned by `messagenode.py:134-139` (`pool.mcp` for agents without agent-level servers).
- [x] 1.2 In `orchestrator/core.py` (~lines 1019-1025): Remove ONLY the `_mcp_shared` guard and the MCP `agent.tools.add_provider(...)` block for subagents (the `if not agent._mcp_shared:` block that adds `mcp_pool.get_aggregating_provider()`). Do NOT remove the skills providers that share the same `if self.pool is not None:` block (lines ~1026-1028: `skills_instruction_provider` and `skills_tools_provider`). Subagents get pool-level non-ACP MCP via `pool.mcp.as_capability()` in `get_agentlet()`, and pool-level ACP MCP via the aggregating provider added in the main session agent path (Pipeline 2 locations in section 4).
- [x] 1.3 In `orchestrator/core.py` (~lines 1030-1035): Remove the parent MCP provider inheritance loop (`for provider in parent_agent.tools.external_providers: if getattr(provider, "kind", None) == "mcp": ...`). This is Pipeline 3 — no longer needed.

## 2. Add MCPToolset Caching to MCPManager

- [x] 2.1 In `mcp_server/manager.py`: Add `_toolset_cache: dict[str, MCPToolset]` field to `MCPManager.__init__`. Initialize as empty dict.
- [x] 2.2 In `mcp_server/manager.py` `as_capability()` method (~lines 237-298): Change signature from `def as_capability(self)` to `async def as_capability(self)`. For each non-ACP server, check `_toolset_cache` by `server.client_id`. If not cached, create a new `MCPToolset` from the server config, enter it via `await self.exit_stack.enter_async_context(toolset)` (persistent connection, MCPManager holds ref-count 1), and cache it. Return `MCP(local=cached_toolset, allowed_tools=server.enabled_tools, id=server.name or server.client_id)`.
- [x] 2.3 In `agents/native_agent/agent.py` `get_agentlet()` (~line 817): Update call site from `mcp_capabilities = self.mcp.as_capability()` to `mcp_capabilities = await self.mcp.as_capability()` (since `as_capability` is now async and `get_agentlet` is already async).
- [x] 2.4 In `mcp_server/manager.py` `__aexit__`/`cleanup()`: The `exit_stack.aclose()` in the existing `cleanup()` method automatically exits all MCPToolsets entered via `enter_async_context()` in task 2.2. No additional code needed — verify this works correctly.
- [x] 2.5 Import `MCPToolset` from `pydantic_ai.mcp` in `manager.py`.

## 3. Split Aggregating Provider (ACP-only)

- [x] 3.1 In `mcp_server/manager.py` `get_aggregating_provider()`: Filter providers to only include those whose `client.config` is `AcpMCPServerConfig`. Non-ACP providers are excluded — they're handled by `as_capability()`.
- [x] 3.2 In `resource_providers/mcp_provider.py` `MCPResourceProvider.as_capability()` (~lines 79-90): No change needed — already returns `None` for non-ACP and `super().as_capability()` for ACP. This is correct.

## 4. Simplify Pipeline 2 Locations

- [x] 4.1 In `orchestrator/core.py` `_create_session_agent` main path (~lines 1073-1082): Replace `self._mcp_pool.get_aggregating_provider() if self._mcp_pool is not None else self.pool.mcp.get_aggregating_provider()` with `self.pool.mcp.get_aggregating_provider()`.
- [x] 4.2 In `orchestrator/core.py` `_create_session_agent` non-native path (~lines 1105-1113): Same simplification as 4.1.
- [x] 4.3 In `orchestrator/core.py` `_create_native_agent` (~lines 2141-2150): Replace `self.mcp_pool.get_aggregating_provider() if self.mcp_pool is not None else self.pool.mcp.get_aggregating_provider()` with `self.pool.mcp.get_aggregating_provider()`.
- [x] 4.4 In `orchestrator/core.py` `_reconstruct_acp_agent` (~lines 2190-2199): Same simplification as 4.3.

## 5. Remove Dedup Hack

- [x] 5.1 In `agents/native_agent/agent.py` `get_agentlet()` (~lines 746-760): Remove the `mcp_aggregating = self.mcp.aggregating_provider` reference and the `if provider is mcp_aggregating: continue` check. All providers in the aggregating provider are now ACP-only, so their `as_capability()` returns non-None `Toolset` capabilities.

## 6. Remove MCPConnectionPool

- [x] 6.1 In `orchestrator/core.py`: Remove module-level import `from agentpool.mcp_server.connection_pool import MCPConnectionPool` (line 57) and function-level import (line 1912).
- [x] 6.2 In `orchestrator/core.py` `SessionController.__init__` (~line 782): Remove `self._mcp_pool: MCPConnectionPool | None = None` attribute.
- [x] 6.3 In `orchestrator/core.py` `SessionPool.__init__` (~line 1919): Remove `self.mcp_pool = MCPConnectionPool(servers=_mcp_servers)` and `self.sessions._mcp_pool = self.mcp_pool` (line 1920).
- [x] 6.4 In `orchestrator/core.py` `SessionPool.start()` (~lines 1925-1926): Remove `await self.mcp_pool.start_cleanup_task()` and `await self.mcp_pool.initialize()`.
- [x] 6.5 In `orchestrator/core.py` `SessionPool.shutdown()` (~line 1941): Remove `await self.mcp_pool.shutdown()`.
- [x] 6.6 Delete `src/agentpool/mcp_server/connection_pool.py` entirely — no test files reference it, no other source files import it after core.py cleanup.
- [x] 6.7 Verify no remaining references to `MCPConnectionPool`, `mcp_pool`, or `_mcp_pool` exist anywhere in `src/` or `tests/`.

## 7. Tests

### 7.1 Rewrite subagent MCP inheritance tests

The existing 4 tests in `tests/orchestrator/test_sessionpool_subagent_mcp_inheritance.py` assert CURRENT (buggy) behavior and must be rewritten:

- [x] 7.1.1 Rewrite `test_child_session_agent_inherits_parent_mcp_providers` → assert subagent's `mcp` IS `pool.mcp` (identity check), NOT parent's MCPManager. Assert subagent does NOT inherit parent's `kind=='mcp'` external_providers (Pipeline 3 removed).
- [x] 7.1.2 Rewrite `test_child_session_agent_does_not_inherit_non_mcp_providers` → assert subagent does NOT inherit ANY parent providers (both MCP and non-MCP). Only pool-level tools via `pool.mcp.as_capability()` are available.
- [x] 7.1.3 Rewrite `test_child_session_agent_shares_base_agent_mcp` → assert `child_agent.mcp is pool.mcp` (the shared pool MCPManager), NOT `parent_agent.mcp`.
- [x] 7.1.4 Rewrite `test_child_session_is_not_per_session_agent` → keep `is_per_session_agent=False` assertion (still correct for cleanup safety), but verify subagent gets pool-level MCP tools through `pool.mcp.as_capability()` in `get_agentlet()`.

### 7.2 New tests for MCPToolset caching

- [x] 7.2.1 Add `test_mcpmanager_toolset_cache_shares_connection` — call `as_capability()` twice for the same server config, verify both return `MCP` capabilities referencing the SAME `MCPToolset` instance (identity check).
- [x] 7.2.2 Add `test_mcpmanager_toolset_cache_keyed_by_client_id` — two different servers produce different `MCPToolset` instances; same server config reuses the cached instance.

### 7.3 New tests for ACP-only aggregating provider

- [x] 7.3.1 Add `test_aggregating_provider_contains_only_acp_providers` — register both ACP and non-ACP servers in `MCPManager`, verify `get_aggregating_provider()` returns only ACP providers.
- [x] 7.3.2 Add `test_non_acp_providers_excluded_from_aggregating_provider` — verify non-ACP providers' tools are ONLY available via `as_capability()`, not via the aggregating provider's `FunctionTool`s.

### 7.4 New test for dedup hack removal

- [x] 7.4.1 Add `test_no_dedup_hack_in_get_agentlet` — verify ACP providers in the aggregating provider produce `Toolset` capabilities (not skipped). All providers in `self.tools.providers` are iterated without skipping.

### 7.5 New end-to-end integration test

- [x] 7.5.1 Add `test_engineer_librarian_mcp_tool_scoping` — simulate the `diag-agent-ng.yaml` scenario: engineer (has `expert-anno`) spawns librarian subagent. Verify librarian has `search_kb` (pool-level) but NOT `request_comment` (agent-level). Use mock MCP servers with distinct tool names.

## 8. Verification

- [x] 8.1 Run `uv run pytest tests/orchestrator/test_sessionpool_subagent_mcp_inheritance.py -v` — all tests pass
- [x] 8.2 Run `uv run pytest -m "not slow and not acp_snapshot"` — no regressions
- [x] 8.3 Run `uv run ruff check src/` — no new lint errors
- [x] 8.4 Run `uv run --no-group docs mypy src/` — no new type errors
- [x] 8.5 Manual verification: Run `agentpool serve-acp packages/xeno-agent/config/diag-agent-ng.yaml` and verify engineer agent has both `search_kb` (pool-level) and `request_comment` (agent-level), and librarian subagent has `search_kb` but NOT `request_comment`.
