## MODIFIED Requirements

### Requirement: Subagents use pool-level MCPManager via messagenode assignment
The system SHALL NOT override the MCPManager assignment made by `messagenode.py` for subagents. When a subagent is created via `core.py` subagent creation path, the agent's `mcp` attribute retains the value assigned by `messagenode.py:134-139` (i.e., `pool.mcp` when the agent has no agent-level MCP servers, or a dedicated `MCPManager` when it does).

#### Scenario: Subagent inherits pool-level MCP servers via pool.mcp
- **GIVEN** a pool has MCP servers configured in YAML `mcp_servers` (e.g., `knowledge_base` with `search_kb`)
- **AND** an engineer agent with its own `mcp_servers` (e.g., `expert-anno`) spawns a librarian subagent via `task`
- **WHEN** the subagent's `MessageNode.__init__` runs
- **THEN** the subagent's `self.mcp` is `pool.mcp` (the shared MCPManager)
- **AND** the subagent's `self._mcp_shared` is `True`
- **AND** no code in `core.py` subagent creation path overrides `agent.mcp`

#### Scenario: Subagent with agent-level MCP servers does not inherit parent's servers
- **GIVEN** an engineer agent has agent-level `mcp_servers` configured (e.g., `expert-anno`)
- **AND** the engineer spawns a librarian subagent
- **WHEN** the subagent is created
- **THEN** the subagent's `mcp` is `pool.mcp`, NOT the engineer's dedicated MCPManager
- **AND** the subagent does NOT have access to `expert-anno`'s `request_comment` tool
- **AND** the subagent DOES have access to `knowledge_base`'s `search_kb` tool

### Requirement: MCPManager caches MCPToolset instances per server
The system SHALL cache `MCPToolset` instances in `MCPManager._toolset_cache` keyed by `server.client_id`. The `as_capability()` method SHALL be async (`async def`). When called, it SHALL return `MCP(local=cached_toolset)` for each non-ACP server, creating the `MCPToolset` only on first access. The MCPManager SHALL enter each cached `MCPToolset` via `await exit_stack.enter_async_context(toolset)` when first created in `as_capability()`, holding a persistent ref-count. The ref-counted `__aenter__`/`__aexit__` mechanism of `MCPToolset` handles concurrent agent usage safely — agents add their own ref when entering the `MCP` capability, and release it when exiting.

#### Scenario: Multiple agents share a single MCPToolset for the same server
- **GIVEN** a pool-level MCP server `knowledge_base` is configured
- **AND** the MCPManager has entered the cached MCPToolset (ref-count = 1, persistent connection)
- **AND** an engineer agent and 5 librarian subagents are all using `pool.mcp`
- **WHEN** each agent calls `get_agentlet()` which calls `await self.mcp.as_capability()`
- **THEN** all agents receive `MCP` capabilities referencing the SAME `MCPToolset` instance for `knowledge_base`
- **AND** only 1 MCP connection is opened for `knowledge_base` (not 7)

#### Scenario: MCPToolset ref-counting handles concurrent agent lifecycles
- **GIVEN** a cached `MCPToolset` for server `knowledge_base` has been entered by MCPManager (ref-count = 1)
- **WHEN** agent A enters the toolset via MCP capability (`__aenter__`, count 1→2)
- **AND** agent B enters the toolset (`__aenter__`, count 2→3)
- **AND** agent A exits (`__aexit__`, count 3→2, connection stays open)
- **AND** agent B exits (`__aexit__`, count 2→1, connection stays open — MCPManager holds persistent ref)
- **AND** pool shutdown calls `exit_stack.aclose()` (`__aexit__`, count 1→0, connection closes)
- **THEN** the connection lifecycle is correctly managed with no leaks or premature closes

### Requirement: Aggregating provider contains only ACP-transport providers
The system SHALL filter `MCPManager.get_aggregating_provider()` to return only providers whose server config is `AcpMCPServerConfig`. Non-ACP providers are handled exclusively by `as_capability()` and SHALL NOT appear in the aggregating provider.

#### Scenario: Non-ACP providers excluded from aggregating provider
- **GIVEN** a pool has both ACP and non-ACP MCP servers configured
- **WHEN** `pool.mcp.get_aggregating_provider()` is called
- **THEN** the returned `AggregatingResourceProvider` contains only ACP-transport providers
- **AND** non-ACP providers are NOT in the aggregating provider
- **AND** non-ACP providers' tools are only available via `as_capability()` → `MCP` capabilities

### Requirement: No parent MCP provider inheritance for subagents
The system SHALL NOT copy parent agent's `kind=='mcp'` external_providers to subagents. Subagents receive MCP tools exclusively through their own `MCPManager` (which is `pool.mcp` for agents without agent-level servers).

#### Scenario: Subagent does not inherit parent's agent-level MCP providers
- **GIVEN** an engineer agent has `expert-anno` MCP server added as an external provider
- **AND** the engineer spawns a librarian subagent
- **WHEN** the subagent is created in `core.py` subagent creation path
- **THEN** the subagent's `tools.external_providers` does NOT contain `expert-anno`
- **AND** the subagent's `tools.external_providers` does NOT contain any `kind=='mcp'` providers from the parent

### Requirement: No dedup hack in get_agentlet
The system SHALL NOT skip the MCP aggregating provider in `get_agentlet()`'s capability collection loop. Since the aggregating provider only contains ACP providers (which return non-None from `as_capability()`), there is no double-registration risk.

#### Scenario: ACP providers in aggregating provider produce FunctionTool capabilities
- **GIVEN** a pool has an ACP-transport MCP server (e.g., `workspace-fs`)
- **WHEN** `get_agentlet()` iterates `self.tools.providers` and encounters the aggregating provider
- **THEN** the aggregating provider's `as_capability()` returns a `Toolset` of `FunctionTool`s
- **AND** these `FunctionTool`s are added to the agent's capabilities
- **AND** no providers are skipped

### Requirement: MCPConnectionPool is removed
The system SHALL NOT use `MCPConnectionPool`. All pool-level MCP connection sharing is handled by `MCPManager` with `MCPToolset` caching. The `mcp_pool` attribute on `SessionPool` and all references to it are removed.

#### Scenario: SessionPool no longer creates MCPConnectionPool
- **GIVEN** a `SessionPool` is initialized
- **WHEN** `SessionPool.__init__` runs
- **THEN** no `MCPConnectionPool` is created
- **AND** `self.mcp_pool` attribute does not exist
- **AND** all Pipeline 2 locations use `self.pool.mcp.get_aggregating_provider()` directly
