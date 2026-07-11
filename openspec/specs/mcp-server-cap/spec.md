## ADDED Requirements

### Requirement: McpServerCap SHALL implement all Resource Protocols via MCPClient delegation

`McpServerCap` SHALL implement `McpResource`, `SkillResource`, `CommandResource`, and `ChangeObservable`. All methods SHALL delegate to an injected `MCPClient`. The client SHALL be obtained from `SessionConnectionPool.get_client(config)` via lazy initialization on first access.

#### Scenario: McpServerCap delegates list_tools to MCPClient
- **WHEN** `list_tools()` is called on `McpServerCap`
- **THEN** `self._client.list_tools()` SHALL be called
- **AND** the result SHALL be mapped to `Sequence[ToolEntry]`

#### Scenario: McpServerCap delegates call_tool to MCPClient
- **WHEN** `call_tool("create_issue", {"title": "bug"})` is called
- **THEN** `self._client.call_tool("create_issue", {"title": "bug"})` SHALL be called
- **AND** the result SHALL be mapped to `ToolResult`

#### Scenario: McpServerCap provides skill content via MCP resources
- **WHEN** `read_skill("skill://github/ponytail")` is called
- **THEN** `self._client.list_resources()` SHALL be called to check if the resource exists
- **AND** if it exists, `self._client.read_resource(uri)` SHALL be called to read content

#### Scenario: McpServerCap provides commands via MCP prompts
- **WHEN** `list_commands()` is called
- **THEN** `self._client.list_prompts()` SHALL be called
- **AND** results SHALL be mapped to `Sequence[CommandEntry]`

### Requirement: McpServerCap SHALL lazily initialize MCPClient on first access

`McpServerCap` SHALL NOT connect to the MCP server at construction time. The `MCPClient` SHALL be created on first access via `_ensure_client()`, which SHALL call `SessionConnectionPool.get_client(config)`. Subsequent accesses SHALL reuse the same client.

#### Scenario: Lazy client initialization
- **WHEN** `McpServerCap.__init__()` is called
- **THEN** no network connection SHALL be established
- **AND** `self._client` SHALL be `None`

#### Scenario: First access triggers connection
- **WHEN** `list_tools()` is called for the first time
- **THEN** `_ensure_client()` SHALL call `session_pool.get_client(config)`
- **AND** the returned `MCPClient` SHALL be cached for subsequent calls

#### Scenario: Lazy mode defers connection to first tool call
- **WHEN** `config.lazy` is `True`
- **AND** `get_toolset()` is called during compilation
- **THEN** the tool list SHALL come from config's `tools` field (static)
- **AND** no connection SHALL be established
- **AND** the connection SHALL be established on first `call_tool()` invocation

### Requirement: McpServerCap SHALL map MCP notifications to ChangeEvents

`McpServerCap` SHALL subscribe to MCP server notifications. `tools/list_changed` SHALL produce `ChangeEvent(kind="tools_changed")`. `resources/list_changed` SHALL produce `ChangeEvent(kind="resources_changed")`. `prompts/list_changed` SHALL produce `ChangeEvent(kind="prompts_changed")`.

#### Scenario: Tool list changed notification
- **WHEN** the MCP server sends `notifications/tools/list_changed`
- **THEN** `McpServerCap.on_change()` stream SHALL yield `ChangeEvent(kind="tools_changed", capability_name=self._name)`

#### Scenario: No change notifications supported
- **WHEN** the MCP server does not support notifications
- **THEN** `on_change()` SHALL return `None`

### Requirement: McpServerCap SHALL accept SessionConnectionPool as constructor parameter

`McpServerCap.__init__()` SHALL accept a `session_pool: SessionConnectionPool` parameter (or `None` for pool-level capabilities). The pool SHALL be used for client creation but SHALL NOT be owned by `McpServerCap` — lifecycle is managed externally.

#### Scenario: Constructor with session pool
- **WHEN** `McpServerCap(config=..., session_pool=pool)` is constructed
- **THEN** `self._session_pool` SHALL reference `pool`
- **AND** `pool` SHALL NOT be closed when `McpServerCap.__aexit__()` is called

#### Scenario: Constructor without session pool
- **WHEN** `McpServerCap(config=..., session_pool=None)` is constructed
- **THEN** `_ensure_client()` SHALL raise a descriptive error when called without a pool
