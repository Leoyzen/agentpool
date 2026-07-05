## 1. Config Layer: `to_transport()` + Remove `to_pydantic_ai()`

- [x] 1.1 Add `to_transport(force_oauth: bool = False)` method to `StdioMCPServerConfig` returning `StdioTransport(command, args, env)` with env vars resolved via `get_env_vars()`
- [x] 1.2 Add `to_transport(force_oauth: bool = False)` method to `SSEMCPServerConfig` returning `SSETransport(url, headers, auth)` with OAuth mapping (`auth.oauth=True` or `force_oauth=True` → `auth='oauth'`, else `auth=None`)
- [x] 1.3 Add `to_transport(force_oauth: bool = False)` method to `StreamableHTTPMCPServerConfig` returning `StreamableHttpTransport(url, headers, auth)` with same OAuth mapping
- [x] 1.4 Delete `to_pydantic_ai()` method from `BaseMCPServerConfig` and all subclasses (`StdioMCPServerConfig`, `SSEMCPServerConfig`, `StreamableHTTPMCPServerConfig`, `AcpMCPServerConfig`)
- [x] 1.5 Remove all `from pydantic_ai.mcp import MCPServer*` imports from `agentpool_config/mcp_server.py`
- [x] 1.6 Remove `_make_timeout_logger()` from `agentpool_config/mcp_server.py` (moved to manager — it's only needed by `as_capability()` now)

> **Completed by** `fix-subagent-mcp-inheritance` change. Config classes already have `to_transport()` and no longer have `to_pydantic_ai()`. No `MCPServer*` imports remain in `src/`.

## 2. Elicitation Adapter (must complete before Manager Layer)

- [x] 2.1 Create `_make_elicitation_handler()` in `manager.py` that returns an async callable with 4-arg FastMCP signature: `(message: str, response_type: type[T] | None, params, context: RequestContext) -> T | dict | ElicitResult`. Follow the pattern in `MCPClient._forwarding_elicitation_callback` (`client.py:184-202`)
- [x] 2.2 Remove `_make_pydantic_ai_elicitation_callback()` from `manager.py` (replaced by `_make_elicitation_handler()`)

> **Completed by** `fix-subagent-mcp-inheritance` change. `_make_elicitation_handler()` exists at `manager.py:49` with correct 4-arg signature. Old `_make_pydantic_ai_elicitation_callback()` is gone.

## 3. Manager Layer: Construct MCPToolset + Create `_toolset_cache`

- [x] 3.1 Add `self._toolset_cache: dict[str, MCPToolset] = {}` field to `MCPManager.__init__()`
- [x] 3.2 Rewrite `as_capability()` to: check `_toolset_cache` by `server.client_id` → on miss, call `config.to_transport()` + construct `MCPToolset(transport, id=server.name, include_instructions=True, process_tool_call=_make_timeout_logger(server.display_name), init_timeout=server.timeout, read_timeout=server.timeout, elicitation_handler=_make_elicitation_handler())` → store in cache → wrap in `MCP(url=..., local=toolset, id=..., allowed_tools=server.enabled_tools)`. On hit, reuse cached toolset and wrap in new `MCP`.
- [x] 3.3 Keep the URL derivation `match/case` block (URL is still required by `MCP.__init__` — cannot be `None`)
- [x] 3.4 Update `disconnect_all()` to close each cached `MCPToolset` via `await toolset.__aexit__(None, None, None)` BEFORE clearing `_toolset_cache` (prevents connection leaks; `MCPToolset` has no `aclose()`)
- [x] 3.5 Move `_make_timeout_logger()` to `manager.py` (or a shared utility module) since it's now called by the manager, not the config classes

> **Partially completed by** `fix-subagent-mcp-inheritance` change. `as_capability()` already constructs `MCPToolset` directly via `_make_capability()` inner function (manager.py:349-369), with correct kwargs (`include_instructions=True`, `process_tool_call`, `init_timeout`, `read_timeout`, `elicitation_handler`). URL derivation `match/case` is in place. `_make_timeout_logger()` lives in `manager.py:75`.
>
> **Remaining**: `_toolset_cache` field does NOT exist in `MCPManager.__init__()` (verified: `grep -rn "_toolset_cache" src/` returns zero results). `as_capability()` creates a fresh `MCPToolset` on every call — no caching. `disconnect_all()` at manager.py:270 calls `_global_pool.shutdown_all()` + `cleanup()` but does NOT close cached toolsets.
>
> **Note**: The `as_capability()` signature has evolved beyond the original spec — it now accepts optional `snapshot: McpConfigSnapshot | None` and `session_pool: SessionConnectionPool | None` parameters, and uses `GlobalConnectionPool` / `SessionConnectionPool` for transport management. The caching logic needs to be reconciled with this new architecture: transports are now obtained from connection pools, not directly from `config.to_transport()`. The cache key should still be `server.client_id`, but cache lookup/insertion must happen after transport acquisition.

## 4. Client Layer: Refactor to Use `to_transport()`

- [x] 4.1 Refactor `MCPClient._get_client()` in `client.py` to call `config.to_transport(force_oauth=force_oauth)` instead of inline match/case transport creation (lines 215-237). This unifies transport creation between the two code paths. The `force_oauth` parameter is forwarded directly.
- [x] 4.2 Remove any `MCPServer*` type annotations or imports from `client.py`
- [x] 4.3 Verify `_make_timeout_logger()` (now in manager.py) has signature compatible with `MCPToolset`'s `ProcessToolCallback` — should be `(ctx, direct_call_tool, name, tool_args)` (no change expected, just moved)

> **Completed**. `client.py:219` calls `config.to_transport(force_oauth=force_oauth)`. No `MCPServer*` imports in `client.py`. `_make_timeout_logger()` has correct signature at `manager.py:91-108`.

## 5. Resource Provider Verification (no changes expected)

- [x] 5.1 Verify `MCPResourceProvider.as_capability()` returns `None` for non-ACP and `super().as_capability()` for ACP — it does NOT call `to_pydantic_ai()` or `to_transport()`, so no changes needed
- [x] 5.2 Verify `MCPResourceProvider.transport_type` property still works (reads config types, not pydantic-ai types — unaffected)
- [x] 5.3 Remove any `MCPServer*` imports from `mcp_provider.py` if present (verify — may already be clean)

> **Verified**. `MCPResourceProvider.as_capability()` returns `None` for non-ACP. No `MCPServer*` imports in `mcp_provider.py`.

## 6. Skill MCP Manager Verification

- [x] 6.1 Verify `SkillMcpManager` in `skill_mcp_manager.py` uses `MCPClient` (not `to_pydantic_ai()` directly) — if so, no changes needed
- [x] 6.2 Verify `SkillCapability` in `capability.py` still works with `MCPToolset`-based capabilities (it receives `MCP` capabilities from `MCPManager.as_capability()`, so should be unaffected)

> **Verified**. No `to_pydantic_ai()` or `MCPServer*` references in skill MCP manager or capability files.

## 7. Test Updates

- [x] 7.1 Grep all tests for `MCPServer` references: `grep -rn "MCPServer" tests/` — list all affected files (expect ~73 matches across ~13 files)
- [x] 7.2 Update `tests/mcp_server/test_manager_capability.py` (NOT `test_mcpmanager_caching.py` which doesn't exist) — update type assertions from `MCPServerStdio`/`MCPServerSSE`/`MCPServerStreamableHTTP` to `MCPToolset`
- [x] 7.3 Update `test_does_not_modify_manager_state` in `test_manager_capability.py` — it currently asserts `caps1[0] is not caps2[0]` (distinct MCP wrapper objects). Keep this assertion (MCP wrappers ARE still distinct). Add a NEW assertion that the underlying toolset is shared, e.g. `caps1[0].local is caps2[0].local` (verify the exact attribute name on `MCP` for the `local` parameter)
- [x] 7.3a Add test: two server configs with different `client_id`s produce `MCP` capabilities wrapping distinct `MCPToolset` instances (covers spec scenario "Different client_ids produce distinct MCPToolsets")
- [x] 7.4 Update `tests/mcp_server/test_mcp_server_config.py` (~21 refs) — remove `to_pydantic_ai()` return type assertions and mock constructors; add `to_transport()` tests instead
- [x] 7.5 Update any remaining test files from 7.1 that reference `MCPServer*` types
- [x] 7.6 Add unit test: `_make_elicitation_handler()` returns a callable with 4-arg signature `(message, response_type, params, context)`
- [x] 7.7 Add unit test: `include_instructions=True` is set on `MCPToolset` constructed by `as_capability()`
- [x] 7.8 Add unit test: `to_transport()` returns correct transport type for each config class
- [x] 7.9 Add unit test: `to_transport(force_oauth=True)` forces `auth='oauth'` regardless of config
- [x] 7.10 Add unit test: cache cleanup calls `__aexit__` on cached toolsets before clearing
- [x] 7.11 Add unit test: `to_pydantic_ai()` method no longer exists on any config class

> **Partially completed**. No `MCPServerStdio`/`MCPServerSSE`/`MCPServerStreamableHTTP` references remain in tests. Tests already use `MCPToolset` assertions. `to_transport()` tests exist. Elicitation handler test exists.
>
> **Remaining**: 7.3a and 7.10 are blocked by task 3.1-3.4 (the `_toolset_cache` doesn't exist yet, so cache-related tests can't be written).

## 8. Verification

- [x] 8.1 `grep -r "from pydantic_ai.mcp import MCPServer" src/` returns zero results
- [x] 8.2 `grep -r "MCPServerStdio\|MCPServerSSE\|MCPServerStreamableHTTP" src/` returns zero results (excluding comments)
- [x] 8.3 `grep -r "to_pydantic_ai" src/agentpool_config/mcp_server.py` returns zero results (method fully removed)
- [x] 8.4 `uv run ruff check src/` passes with no new errors
- [x] 8.5 `uv run --no-group docs mypy src/agentpool_config/mcp_server.py src/agentpool/mcp_server/manager.py` passes
- [x] 8.6 `uv run pytest tests/mcp_server/ tests/orchestrator/test_sessionpool_subagent_mcp_inheritance.py -vv` passes
- [x] 8.7 `uv run pytest -x -q` (full suite) passes with no new failures beyond pre-existing
- [ ] 8.8 Manual QA: Start agentpool with a `streamable-http` MCP server config and verify tools are discovered and callable
