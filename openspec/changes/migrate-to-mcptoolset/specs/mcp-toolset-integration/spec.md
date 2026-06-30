## ADDED Requirements

### Requirement: MCPToolset as the MCP server abstraction

The system SHALL use `pydantic_ai.mcp.MCPToolset` as the sole abstraction for connecting to MCP servers. `MCPManager.as_capability()` SHALL construct `MCPToolset` instances directly from `ClientTransport` objects returned by `config.to_transport()`. The system SHALL NOT import or use any deprecated `MCPServer*` classes from `pydantic_ai.mcp`. The system SHALL remove all `to_pydantic_ai()` methods from `MCPServerConfig` subclasses.

#### Scenario: MCPManager constructs MCPToolset from StdioMCPServerConfig

- **WHEN** `as_capability()` processes a `StdioMCPServerConfig`
- **THEN** it SHALL call `config.to_transport()` to get a `StdioTransport`, then construct `MCPToolset(transport, id=..., include_instructions=True, ...)` and cache it

#### Scenario: MCPManager constructs MCPToolset from SSEMCPServerConfig

- **WHEN** `as_capability()` processes an `SSEMCPServerConfig`
- **THEN** it SHALL call `config.to_transport()` to get an `SSETransport`, then construct `MCPToolset(transport, ...)` and cache it

#### Scenario: MCPManager constructs MCPToolset from StreamableHTTPMCPServerConfig

- **WHEN** `as_capability()` processes a `StreamableHTTPMCPServerConfig`
- **THEN** it SHALL call `config.to_transport()` to get a `StreamableHttpTransport`, then construct `MCPToolset(transport, ...)` and cache it

#### Scenario: AcpMCPServerConfig skipped by as_capability

- **WHEN** `as_capability()` encounters an `AcpMCPServerConfig`
- **THEN** it SHALL skip it (ACP servers are managed separately by the ACP agent layer)

#### Scenario: to_pydantic_ai() methods removed

- **WHEN** the migration is complete
- **THEN** no `to_pydantic_ai()` method SHALL exist on any `MCPServerConfig` subclass

### Requirement: Shared `to_transport()` method for code reuse

The system SHALL provide a `to_transport(force_oauth: bool = False)` method on `BaseMCPServerConfig` subclasses that returns a FastMCP `ClientTransport`. Both `MCPManager.as_capability()` and `MCPClient._get_client()` SHALL use this shared method to avoid transport creation logic duplication.

#### Scenario: StdioMCPServerConfig produces StdioTransport

- **WHEN** `StdioMCPServerConfig.to_transport()` is called
- **THEN** it SHALL return a `StdioTransport(command, args, env)` with env vars resolved via `get_env_vars()`

#### Scenario: SSEMCPServerConfig produces SSETransport

- **WHEN** `SSEMCPServerConfig.to_transport()` is called
- **THEN** it SHALL return an `SSETransport(url, headers, auth)` with OAuth mapped from `auth.oauth=True` to `auth='oauth'`

#### Scenario: StreamableHTTPMCPServerConfig produces StreamableHttpTransport

- **WHEN** `StreamableHTTPMCPServerConfig.to_transport()` is called
- **THEN** it SHALL return a `StreamableHttpTransport(url, headers, auth)` with OAuth mapped from `auth.oauth=True` to `auth='oauth'`

#### Scenario: force_oauth overrides auth

- **WHEN** `config.to_transport(force_oauth=True)` is called for an SSE or HTTP config
- **THEN** the transport SHALL be constructed with `auth='oauth'` regardless of the config's `auth.oauth` setting

#### Scenario: MCPClient._get_client uses to_transport

- **WHEN** `MCPClient._get_client()` creates a FastMCP client from a config
- **THEN** it SHALL call `config.to_transport()` instead of inline match/case transport creation

### Requirement: MCPManager creates and caches MCPToolset instances

The `MCPManager` SHALL maintain a `_toolset_cache: dict[str, MCPToolset]` field. `as_capability()` SHALL check this cache before constructing a new `MCPToolset`. On a cache miss, the `MCPToolset` SHALL be created via `config.to_transport()` + `MCPToolset(...)` and stored. On a cache hit, the cached instance SHALL be reused, ensuring one underlying connection per server config.

#### Scenario: Same client_id reuses cached MCPToolset

- **WHEN** `as_capability()` is called twice for a server config with the same `client_id`
- **THEN** both calls SHALL return `MCP` capabilities whose underlying `MCPToolset` (accessed via the `local` attribute) is the same object instance. The `MCP` wrapper instances themselves MAY be distinct objects.

#### Scenario: Different client_ids produce distinct MCPToolsets

- **WHEN** `as_capability()` is called for two server configs with different `client_id`s
- **THEN** each SHALL return a `MCP` capability wrapping a distinct `MCPToolset` instance

#### Scenario: Cache cleared on disconnect with proper cleanup

- **WHEN** `MCPManager.disconnect_all()` is called
- **THEN** each cached `MCPToolset` SHALL be closed via `await toolset.__aexit__(None, None, None)` BEFORE the cache is cleared, preventing connection leaks

### Requirement: Elicitation handler uses correct FastMCP signature

The system SHALL create an `_make_elicitation_handler()` in `manager.py` that returns an async callable accepting 4 arguments: `(message: str, response_type: type[T] | None, params, context: RequestContext)`. The manager SHALL pass this handler to `MCPToolset(elicitation_handler=...)`.

#### Scenario: Elicitation handler has 4-arg signature

- **WHEN** the default elicitation handler is created via `_make_elicitation_handler()`
- **THEN** the returned callable SHALL accept 4 parameters: `message`, `response_type`, `params`, `context`

#### Scenario: Elicitation handler follows existing client.py pattern

- **WHEN** the elicitation handler is invoked
- **THEN** it SHALL follow the same pattern as `MCPClient._forwarding_elicitation_callback` in `client.py:184-202`, which already uses the correct 4-arg FastMCP signature

### Requirement: Timeout mapping preserves semantics

The system SHALL map the config `timeout` field to both `MCPToolset.init_timeout` (connection initialization timeout) and `MCPToolset.read_timeout` (per-response read timeout), matching the current behavior where `timeout` is used for both.

#### Scenario: Timeout maps to init_timeout and read_timeout

- **WHEN** a `StdioMCPServerConfig` with `timeout=30` is used to construct an `MCPToolset`
- **THEN** the `MCPToolset` SHALL have `init_timeout=30` and `read_timeout=30`

### Requirement: include_instructions=True preserves behavior

The system SHALL set `include_instructions=True` when constructing `MCPToolset` in `as_capability()`. This preserves the current behavior where `MCP.__init__` implicitly wraps `MCPServer*` instances with `include_instructions=True`.

#### Scenario: MCPToolset has include_instructions=True

- **WHEN** `as_capability()` constructs an `MCPToolset` for any non-ACP config
- **THEN** the `MCPToolset` SHALL have `include_instructions=True` to preserve MCP server instructions in the agent's system prompt

### Requirement: MCP capability URL remains required

The system SHALL continue deriving a synthetic URL for the `MCP(url=..., local=toolset)` constructor. The `url` parameter is a required `str` on `MCP.__init__` and cannot be `None`.

#### Scenario: Synthetic URL derived for stdio server

- **WHEN** `as_capability()` creates an `MCP` capability for a `StdioMCPServerConfig`
- **THEN** the URL SHALL be `f"mcp://stdio/{server.client_id}"` (or similar synthetic value)

#### Scenario: Real URL used for HTTP server

- **WHEN** `as_capability()` creates an `MCP` capability for a `StreamableHTTPMCPServerConfig`
- **THEN** the URL SHALL be `str(server.url)`

### Requirement: Process tool call callback preserved

The system SHALL pass the `process_tool_call` callback (for timeout logging) to `MCPToolset` with the same `ProcessToolCallback` signature `(ctx, direct_call_tool, name, tool_args)` as the deprecated `MCPServer*` classes. The manager SHALL call `_make_timeout_logger(display_name)` and pass the result to `MCPToolset(process_tool_call=...)`.

#### Scenario: Timeout logger attached

- **WHEN** `as_capability()` constructs an `MCPToolset` for any non-ACP config
- **THEN** the `_make_timeout_logger(config.display_name)` callback SHALL be passed as `process_tool_call` to the `MCPToolset` constructor

### Requirement: OAuth auth mapping

The system SHALL map `MCPServerAuthSettings.oauth=True` to `auth='oauth'` in the FastMCP transport constructor, enabling the OAuth flow for SSE and StreamableHTTP transports. Custom redirect/scope settings (`redirect_port`, `redirect_path`, `scope`, `persist`) remain unsupported — same as current behavior.

#### Scenario: OAuth enabled for HTTP transport

- **WHEN** a `StreamableHTTPMCPServerConfig` with `auth.oauth=True` is converted via `to_transport()`
- **THEN** the `StreamableHttpTransport` SHALL be constructed with `auth='oauth'`

#### Scenario: No auth by default

- **WHEN** a `StreamableHTTPMCPServerConfig` with default auth settings (`oauth=False`) is converted via `to_transport()`
- **THEN** the `StreamableHttpTransport` SHALL be constructed with `auth=None`

### Requirement: No deprecated MCPServer imports

The system SHALL NOT import `MCPServer`, `MCPServerStdio`, `MCPServerSSE`, or `MCPServerStreamableHTTP` from `pydantic_ai.mcp` anywhere in the `src/` tree after migration.

#### Scenario: No MCPServer imports in source

- **WHEN** the `src/` directory is grepped for `from pydantic_ai.mcp import MCPServer`
- **THEN** zero matches SHALL be found (excluding string literals or comments)

### Requirement: YAML config backward compatibility

The YAML `mcp_servers` configuration schema SHALL remain unchanged. All existing YAML configs with `type: stdio`, `type: sse`, `type: streamable-http`, or string shorthand (`"uvx mcp-server-git"`) SHALL continue to work without modification.

#### Scenario: String shorthand still works

- **WHEN** a YAML config contains `mcp_servers: ["uvx mcp-server-git"]`
- **THEN** the system SHALL create a `StdioMCPServerConfig` and produce a working `MCPToolset`

#### Scenario: HTTP URL config still works

- **WHEN** a YAML config contains an MCP server with `url: "http://localhost:8000/mcp"`
- **THEN** the system SHALL create a `StreamableHTTPMCPServerConfig` and produce a working `MCPToolset`

### Requirement: MCPResourceProvider unaffected

The `MCPResourceProvider.as_capability()` method SHALL continue returning `None` for non-ACP servers and `super().as_capability()` for ACP servers. It does NOT call `to_pydantic_ai()` or `to_transport()` and is unaffected by this migration.

#### Scenario: Non-ACP provider returns None

- **WHEN** `MCPResourceProvider.as_capability()` is called for a non-ACP server
- **THEN** it SHALL return `None` (no change from current behavior)

#### Scenario: ACP provider uses base class

- **WHEN** `MCPResourceProvider.as_capability()` is called for an ACP server
- **THEN** it SHALL return `super().as_capability()` (no change from current behavior)
