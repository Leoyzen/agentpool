## Context

> **Note**: This change was largely superseded by `fix-subagent-mcp-inheritance`, which independently implemented most of the migration as part of its pipeline unification. The tasks and design below have been updated to reflect the current state of the codebase.

AgentPool's MCP integration previously used pydantic-ai's deprecated `MCPServer` class hierarchy (`MCPServerStdio`, `MCPServerSSE`, `MCPServerStreamableHTTP`). These classes were marked deprecated and would be removed in pydantic-ai v2. The replacement is `MCPToolset`, built on the FastMCP client, which supports the full MCP protocol including OAuth, elicitation, and sampling.

The codebase had two distinct MCP client paths:
1. **`MCPManager.as_capability()` path** (manager.py): Used by native agents. Previously called `config.to_pydantic_ai()` to get an `MCPServer` instance, wrapped it in `MCP(local=...)` capability. **No caching existed** — each `as_capability()` call created a fresh instance.
2. **`MCPClient._get_client()` path** (client.py): Used by `MCPResourceProvider` and skill MCP manager. Already used FastMCP `Client` with `StdioTransport`/`SSETransport`/`StreamableHttpTransport` internally, with its own transport creation match/case logic.

Both paths had their own transport creation logic. This migration unifies them via a shared `to_transport()` method on config classes and eliminates the deprecated `to_pydantic_ai()` method entirely.

### Current State (post `fix-subagent-mcp-inheritance`)

The `fix-subagent-mcp-inheritance` change already completed the bulk of this migration:
- `to_transport()` methods exist on all config classes (`StdioMCPServerConfig`, `SSEMCPServerConfig`, `StreamableHTTPMCPServerConfig`)
- `to_pydantic_ai()` methods are fully removed
- All `MCPServer*` imports eliminated from `src/`
- `_make_elicitation_handler()` exists with correct 4-arg FastMCP signature
- `_make_pydantic_ai_elicitation_callback()` is removed
- `as_capability()` is async, constructs `MCPToolset` directly, and uses `GlobalConnectionPool` / `SessionConnectionPool` for transport management
- `_make_timeout_logger()` lives in `manager.py`
- `client.py:_get_client()` calls `config.to_transport(force_oauth=...)`

**What remains**: The `_toolset_cache` — the core connection reuse mechanism. `as_capability()` currently creates a fresh `MCPToolset` on every call. The `as_capability()` signature has evolved: it now accepts optional `snapshot` and `session_pool` parameters and delegates transport acquisition to connection pools. The caching logic must be reconciled with this new architecture.

## Goals / Non-Goals

**Goals:**
- Replace all `MCPServer*` usage with `MCPToolset` + FastMCP transports
- **Remove `to_pydantic_ai()` from config classes** — manager constructs `MCPToolset` directly from `to_transport()`
- Unify transport creation via shared `to_transport()` method on config classes
- Add connection caching (one `MCPToolset` per `client_id`) to avoid creating duplicate connections
- Preserve `process_tool_call` timeout logging and `elicitation_handler` support
- Preserve `include_instructions=True` behavior (currently set implicitly by `MCP.__init__` when wrapping `MCPServer*`)
- Remove all imports of deprecated `MCPServer*` classes from `pydantic_ai.mcp`

**Non-Goals:**
- Changing `AcpMCPServerConfig` — ACP servers are skipped in `as_capability()` and handled separately by the ACP agent layer
- Adding new MCP features (sampling, roots) — just migrating existing functionality
- Changing YAML schema or user-facing APIs

## Decisions

### D1: Remove `to_pydantic_ai()`, manager constructs `MCPToolset` directly

**Decision**: Delete all `to_pydantic_ai()` methods from config classes. `MCPManager.as_capability()` calls `config.to_transport()` to get a `ClientTransport`, then constructs `MCPToolset` directly with all required parameters (`id`, `include_instructions`, `process_tool_call`, `init_timeout`, `read_timeout`, `elicitation_handler`).

**Rationale**: `to_pydantic_ai()` was the only consumer of deprecated `MCPServer*` classes. By removing it, the config layer no longer needs any pydantic-ai imports. The manager already knows the server config (name, timeout, display_name, enabled_tools) and can construct `MCPToolset` with full control over caching and parameter passing.

**Alternative considered**: Keep `to_pydantic_ai()` but change return type to `MCPToolset`. Rejected because the method name is misleading (returns `MCPToolset`, not a "pydantic-ai MCPServer"), and it adds an unnecessary abstraction layer between `to_transport()` and `MCPToolset` construction.

### D2: Extract `to_transport()` shared method

**Decision**: Add a `to_transport(force_oauth: bool = False)` method to `BaseMCPServerConfig` subclasses that returns a FastMCP `ClientTransport`. Both `MCPManager.as_capability()` and `MCPClient._get_client()` call this shared method.

**Rationale**: `MCPClient._get_client()` in `client.py` (lines 215-237) already has transport creation logic (match/case on config type → construct `StdioTransport`/`SSETransport`/`StreamableHttpTransport`). A shared `to_transport()` method avoids duplication.

**`force_oauth` parameter**: The current `client.py:__aenter__` (lines 140-156) has an OAuth fallback that retries with `force_oauth=True`. `to_transport(force_oauth=True)` forces `auth='oauth'` in the transport regardless of config. This keeps all auth logic in `to_transport()` and lets the retry path call `config.to_transport(force_oauth=True)`.

### D3: `MCPManager.as_capability()` creates `_toolset_cache` from scratch

**Decision**: Add a new `_toolset_cache: dict[str, MCPToolset]` field to `MCPManager`. `as_capability()` checks the cache before constructing a new `MCPToolset`. On cache miss, it calls `config.to_transport()` + constructs `MCPToolset` + stores in cache. On cache hit, the cached instance is reused.

**Rationale**: Currently `as_capability()` creates a fresh `MCPServer` on every call — no caching. This migration adds caching to avoid duplicate MCP connections for the same server config.

**Cache lifecycle**: `MCPToolset` is an async context manager (`__aenter__`/`__aexit__`) with no `aclose()` method. When `disconnect_all()` clears the cache, each cached `MCPToolset` must be closed via `await toolset.__aexit__(None, None, None)` before being discarded. Otherwise, underlying connections (subprocesses, HTTP sessions) leak.

```python
async def disconnect_all(self) -> None:
    for toolset in self._toolset_cache.values():
        await toolset.__aexit__(None, None, None)
    self._toolset_cache.clear()
    await self.cleanup()
    self.exit_stack = AsyncExitStack()
```

### D4: `MCP(url=...)` still requires a URL

**Decision**: Keep a synthetic URL derivation (similar to current code) when constructing `MCP(local=toolset)`. The `url` parameter on `MCP.__init__` is a required `str` — passing `None` is a type error and will fail at runtime.

**Rationale**: `MCP.__init__` signature is `url: str` (not `str | None`). The URL is used for native MCP support (model-side connections); when `native=False`, it's informational only but still required syntactically. The existing `match/case` URL derivation block should be preserved in the manager.

### D5: Elicitation handler uses correct FastMCP 4-arg signature

**Decision**: Create `_make_elicitation_handler()` in `manager.py` that returns an async callable with the correct FastMCP `ElicitationHandler` signature. The manager passes this handler to `MCPToolset(elicitation_handler=...)`.

**Rationale**: The FastMCP `ElicitationHandler` signature is:
```python
(message: str, response_type: type[T] | None, params: ElicitRequestURLParams | ElicitRequestFormParams, context: RequestContext) 
    -> T | dict[str, Any] | ElicitResult
```

This is **4 arguments** `(message, response_type, params, context)`, NOT 2 arguments `(ctx, params)`. The existing `_make_pydantic_ai_elicitation_callback()` in `manager.py` returns a 2-arg callback. The new adapter must accept all 4 arguments.

**Reference**: `MCPClient._forwarding_elicitation_callback` in `client.py:184-202` already uses the correct 4-arg FastMCP signature. The adapter should follow that pattern.

### D6: `timeout` → `init_timeout` + `read_timeout`

**Decision**: Map the config `timeout` field to both `MCPToolset.init_timeout` and `MCPToolset.read_timeout`. The config currently has only a `timeout` field (used for both). Continue using `self.timeout` for both.

**Rationale**: `MCPServerStdio.timeout` was the connection init timeout. `MCPToolset` splits this into `init_timeout` (connection) and `read_timeout` (per-read). The config's `timeout` field maps to both.

### D7: `include_instructions=True` must be explicitly set

**Decision**: Set `include_instructions=True` when constructing `MCPToolset` in `as_capability()`.

**Rationale**: The current flow is: `to_pydantic_ai()` returns `MCPServerStdio` (NOT an `AbstractToolset`) → `MCP.__init__` wraps it as `MCPToolset(local, include_instructions=True)`. The new flow constructs `MCPToolset` directly (which IS an `AbstractToolset`) → `MCP.__init__` passes it through unchanged. Without explicitly setting `include_instructions=True`, the value defaults to `False`, causing MCP server instructions to silently disappear from the agent's system prompt.

### D8: `process_tool_call` preserved as-is

**Decision**: The `_make_timeout_logger(display_name)` callback is passed directly to `MCPToolset(process_tool_call=...)` by the manager.

**Rationale**: `MCPToolset` supports `process_tool_call` with the same `ProcessToolCallback` signature `(ctx, direct_call_tool, name, tool_args)` as the deprecated `MCPServer*` classes.

## Risks / Trade-offs

- **[FastMCP transport stability]** FastMCP is actively evolving; transport APIs may change between versions. → Mitigation: Pin `fastmcp` version in `pyproject.toml` and run full test suite.

- **[Elicitation handler signature]** The 4-arg FastMCP signature `(message, response_type, params, context)` differs significantly from the old 2-arg `(context, params)`. → Mitigation: Follow the pattern in `MCPClient._forwarding_elicitation_callback` (`client.py:184-202`) which already uses the correct signature. Test elicitation end-to-end.

- **[OAuth auth flow]** `MCPServerAuthSettings` has `redirect_port`, `redirect_path`, `scope`, `persist` fields that are currently ignored. → Mitigation: Map `auth.oauth=True` to `auth='oauth'` in the transport constructor (triggers FastMCP's built-in OAuth flow). Note that custom redirect/scope settings remain unsupported — same as current behavior.

- **[Cache lifecycle]** `MCPToolset` has no `aclose()` — must use `__aexit__` for cleanup. → Mitigation: `disconnect_all()` calls `await toolset.__aexit__(None, None, None)` for each cached toolset before clearing.

- **[Existing test contradiction]** The existing test `test_does_not_modify_manager_state` in `tests/mcp_server/test_manager_capability.py` asserts `caps1[0] is not caps2[0]` (distinct objects). With caching, `MCP` wrappers are still distinct but the underlying `MCPToolset` is shared. → Mitigation: Keep the distinct assertion, add a new assertion for shared toolset identity.

- **[Test volume]** 73 test references to `MCPServer` across 13 files. → Mitigation: Grep and update systematically. Heaviest files: `test_mcp_server_config.py` (21 refs) and `test_manager_capability.py` (21 refs).

- **[Breaking `to_pydantic_ai()` removal]** Any external code calling `config.to_pydantic_ai()` will break. → Mitigation: Grep `src/` for all callers. The only known caller is `MCPManager.as_capability()`. If external plugins use it, they should migrate to `to_transport()` + `MCPToolset` directly.
