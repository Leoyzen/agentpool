## Why

Subagents spawned via background task (`task` tool) lose access to MCP-over-ACP tools (e.g., `workspace-fs`, `mcp-scratchpad`) that were dynamically added to the parent session. This regression was introduced by commit `9a74fe726` which removed MCP provider inheritance for child sessions to fix a `CombinedToolset` duplicate-tool-name error. The removal was overly broad — it broke all subagent MCP access while only the shared-`base_agent` mutation path needed fixing.

## What Changes

- **Restore session-level MCP provider inheritance for child sessions**: When a parent session spawns a subagent (child session), the child's agent inherits the parent's `kind=='mcp'` resource providers with tool-name-level deduplication to prevent `CombinedToolset` conflicts.
- **Create per-session agents for child sessions** instead of sharing the pool-level `base_agent`. This avoids polluting the shared `base_agent.tools.external_providers` with session-scoped MCP providers.
- **Tool-name deduplication**: Before adding inherited MCP providers to the child agent, verify no existing tools on the agent share names with the inherited provider's tools. Skip providers with conflicts.

## Capabilities

### New Capabilities

- `subagent-mcp-inheritance`: Child sessions inherit parent session's dynamically-added MCP providers (kind='mcp') with tool-name dedup, without mutating the shared pool-level agent.

### Modified Capabilities

<!-- No existing spec-level requirements change -->

## Impact

- **Affected code**: `src/agentpool/orchestrator/core.py` (`get_or_create_session_agent`), `tests/orchestrator/test_sessionpool_subagent_mcp_inheritance.py`
- **No API changes**: Public API unchanged
- **No new dependencies**
- **Breaking changes**: None
