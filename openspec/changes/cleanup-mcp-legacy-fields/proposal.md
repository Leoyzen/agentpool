## Why

The Phase 1 MCP session lifecycle refactor (`610564e1e`) migrated `MCPManager.as_capability()` from a parameter-based API (`snapshot=`, `session_pool=`) to a `session_id`-based API, but left two legacy fields on the `Agent` class (`_mcp_snapshot`, `_session_connection_pool`) that are never set in the new API. This created dead code: `_session_connection_pool` is always `None`, causing `ACPSession.initialize_mcp_servers()` to skip `add_acp_transport()` registration — which broke ACP MCP tool inheritance for subagent sessions (fixed symptomatically in `558c64472` but the dead fields and legacy code paths remain). Completing the migration eliminates confusion, prevents future dead-code bugs, and consolidates all MCP session state on `MCPManager._session_contexts`.

This change **completes Phase 1's incomplete migration** — it is NOT Phase 2. Phase 1's design explicitly defined Phase 2 scope as: (1) remove per-agent MCPManager, (2) add allow/block list config for MCP servers, (3) consolidate skill MCP dual paths. This change does none of those. It only removes the dead fields Phase 1 left behind "for compat" and migrates all call sites to the `session_id` API that Phase 1 introduced.

## What Changes

- **Remove** `Agent._session_connection_pool` field — never set in the new API, always `None`
- **Remove** `Agent._mcp_snapshot` field — redundant with `MCPManager._session_contexts[session_id].snapshot`; dual-write caused the snapshot sync bug
- **Add** `MCPManager.get_session_context(session_id) -> _SessionContext | None` public accessor — replaces 4 direct `_session_contexts` private dict accesses
- **Migrate** `session.py:initialize_mcp_servers()` — read existing snapshot from session context instead of `agent._mcp_snapshot`; remove dead `_session_connection_pool` branch; stop writing to `agent._mcp_snapshot` (the `update_session_snapshot()` call is sufficient)
- **Migrate** `agent.py:get_agentlet()` — write skill configs to session context via `get_or_create_session()` + `update_session_snapshot()` instead of `agent._mcp_snapshot`
- **Migrate** `capability.py:SkillCapability._build_mcp_toolsets_from_pool()` — read snapshot and connection pool from session context via public accessor; keep legacy fallback as primary path for skills (`SkillMcpManager` has idle timeout + retry that `SessionConnectionPool` lacks)
- **Migrate** `session_controller.py` — replace `parent_agent.mcp._session_contexts.get(...)` with `parent_agent.mcp.get_session_context(...)`
- **BREAKING**: `Agent._mcp_snapshot` and `Agent._session_connection_pool` attributes removed. Custom Agent subclasses setting these fields will get `AttributeError` (previously silently ignored)

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `mcp-session-lifecycle`: Add requirement for public `get_session_context()` accessor; add requirement that `Agent` must not hold MCP snapshot state (single source of truth on `MCPManager`); add requirement that skill configs are written to session context snapshot, not Agent-local fields

## Impact

- **`src/agentpool/agents/native_agent/agent.py`** — Remove 2 field declarations, migrate skill config registration in `get_agentlet()` (lines 333-334, 914-930)
- **`src/agentpool_server/acp_server/session.py`** — Migrate snapshot read/write, remove dead branch (lines 460-462, 499-500, 543, 552)
- **`src/agentpool/skills/capability.py`** — Migrate to session context accessor (lines 220-221)
- **`src/agentpool/orchestrator/session_controller.py`** — Replace private dict access with public method (lines 491, 520)
- **`src/agentpool/mcp_server/manager.py`** — Add `get_session_context()` method
- **Tests** — 2 test files directly access `agent._mcp_snapshot` (`test_acp_session_mcp_registration.py`, `test_e2e_acp_inheritance_function_model.py`); 13 additional test files directly access `mcp._session_contexts` private dict for assertions — all migrate to `get_session_context()` public API; 1 additional file has `_session_connection_pool` in a function name (false positive). New integration tests verify mock MCP tool inheritance to subagents through the public accessor.
