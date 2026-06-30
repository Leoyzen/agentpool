## Why

PydanticAI has deprecated the `MCPServer` class hierarchy (`MCPServerStdio`, `MCPServerSSE`, `MCPServerStreamableHTTP`) in favor of `MCPToolset`, which is built on the FastMCP client and supports the full MCP protocol (tools, resources, sampling, elicitation, OAuth). The deprecated classes will be removed in pydantic-ai v2. AgentPool currently uses `MCPServer*` throughout its MCP integration layer, creating a technical debt that will become a breaking migration when v2 lands. Migrating now eliminates this debt and unlocks FastMCP's richer transport and auth capabilities.

## What Changes

- Add `to_transport(force_oauth: bool = False)` methods to `MCPServerConfig` subclasses returning FastMCP `ClientTransport` objects (`StdioTransport`, `SSETransport`, `StreamableHttpTransport`).
- **Remove all `to_pydantic_ai()` methods** from config classes — `MCPManager.as_capability()` constructs `MCPToolset` directly from `to_transport()`.
- Update `MCPManager.as_capability()` to construct and cache `MCPToolset` instances (instead of deprecated `MCPServer` instances) wrapped in pydantic-ai `MCP` capabilities.
- Refactor `MCPClient._get_client()` to use shared `to_transport()` methods on config classes (indirectly affects `MCPResourceProvider` which uses `MCPClient`).
- Replace `_make_pydantic_ai_elicitation_callback()` with `_make_elicitation_handler()` using the correct 4-arg FastMCP signature `(message, response_type, params, context)`.
- Remove all imports of deprecated `MCPServer*` classes from `pydantic_ai.mcp`.
- Update tests that mock or assert on `MCPServer*` types to use `MCPToolset` equivalents.

## Capabilities

### New Capabilities

- `mcp-toolset-integration`: Defines how AgentPool creates, caches, and lifecycle-manages `MCPToolset` instances from YAML config, including transport mapping, auth, elicitation, and connection reuse.

### Modified Capabilities

_(None — no existing spec-level behavior changes. This is an internal implementation migration.)_

## Impact

- **`agentpool_config/mcp_server.py`**: All 4 `to_pydantic_ai()` methods **deleted**. New `to_transport()` methods added to 3 non-ACP config classes. `_make_timeout_logger()` moved to manager. All `MCPServer*` imports removed.
- **`agentpool/mcp_server/manager.py`**: `as_capability()` rewritten to call `to_transport()` + construct `MCPToolset` directly. New `_toolset_cache` field. `_make_elicitation_handler()` replaces `_make_pydantic_ai_elicitation_callback()`. `_make_timeout_logger()` moved here.
- **`agentpool/mcp_server/client.py`**: `_get_client()` refactored to call `config.to_transport(force_oauth=...)` instead of inline match/case.
- **`agentpool/resource_providers/mcp_provider.py`**: No changes (does not call `to_pydantic_ai()` or `to_transport()`).
- **`agentpool/skills/skill_mcp_manager.py`**: No changes expected (uses `MCPClient`, not `to_pydantic_ai()`).
- **Tests**: Any test mocking `MCPServer*` or asserting on `to_pydantic_ai()` needs updating.
- **Dependencies**: `fastmcp` is already a transitive dependency via `pydantic-ai`; no new dependencies needed.
- **Compatibility**: No YAML config changes required — all changes are internal. Users' existing `mcp_servers:` YAML configs continue to work unchanged.
- **Breaking**: `to_pydantic_ai()` method removed from public API. Any external code calling `config.to_pydantic_ai()` must migrate to `config.to_transport()` + `MCPToolset` construction.
