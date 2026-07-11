## ADDED Requirements

### Requirement: SkillResource Protocol SHALL define skill access methods

The `SkillResource` Protocol SHALL be a `@runtime_checkable` Protocol with three methods: `list_skills() -> Sequence[SkillEntry]`, `read_skill(uri: str) -> str | bytes | None`, and `skill_exists(uri: str) -> bool`. `skill_exists()` SHALL perform a cheap check (filesystem stat or in-memory lookup) before `read_skill()` performs a potentially expensive read.

#### Scenario: List available skills
- **WHEN** `list_skills()` is called on a `SkillResource` implementation
- **THEN** a sequence of `SkillEntry` objects SHALL be returned, each containing `uri`, `name`, and `description`

#### Scenario: Read skill content by URI
- **WHEN** `read_skill("skill://ponytail/SKILL.md")` is called
- **THEN** the skill content SHALL be returned as `str` or `bytes`
- **AND** if the skill does not exist, `None` SHALL be returned

#### Scenario: Cheap existence check before read
- **WHEN** `skill_exists("skill://ponytail/SKILL.md")` is called
- **THEN** a boolean SHALL be returned without reading the full skill content
- **AND** the check SHALL be faster than calling `read_skill()` and checking for `None`

### Requirement: McpResource Protocol SHALL define MCP tool and resource access

The `McpResource` Protocol SHALL be a `@runtime_checkable` Protocol with five methods: `list_tools() -> Sequence[ToolEntry]`, `call_tool(name: str, args: dict) -> ToolResult`, `list_resources() -> Sequence[ResourceEntry]`, `read_resource(uri: str) -> str | bytes | None`, and `resource_exists(uri: str) -> bool`. All methods SHALL delegate to an injected `MCPClient`.

#### Scenario: List MCP tools
- **WHEN** `list_tools()` is called on an `McpResource` implementation
- **THEN** the implementation SHALL call `self._client.list_tools()` and return a sequence of `ToolEntry` objects

#### Scenario: Call MCP tool
- **WHEN** `call_tool("create_issue", {"title": "bug"})` is called
- **THEN** the implementation SHALL call `self._client.call_tool("create_issue", {"title": "bug"})` and return a `ToolResult`

#### Scenario: Read MCP resource with existence check
- **WHEN** `read_resource("mcp://github/issues/123")` is called
- **THEN** the implementation SHALL first call `resource_exists(uri)` for a cheap check
- **AND** if the resource does not exist, SHALL return `None` without a network round-trip to `read_resource`

### Requirement: CommandResource Protocol SHALL define command access

The `CommandResource` Protocol SHALL be a `@runtime_checkable` Protocol with two methods: `list_commands() -> Sequence[CommandEntry]` and `async get_command(name: str, args: list[str]) -> str`. `get_command()` SHALL be `async` to support network-backed command sources (MCP prompts). Local implementations return immediately.

#### Scenario: List available commands
- **WHEN** `list_commands()` is called on a `CommandResource` implementation
- **THEN** a sequence of `CommandEntry` objects SHALL be returned, each containing `name`, `description`, and `arguments`

#### Scenario: Resolve command to prompt
- **WHEN** `get_command("ponytail", ["fix", "auth.ts"])` is called
- **THEN** the command SHALL be resolved to a string containing the skill instructions concatenated with user arguments

### Requirement: ChangeObservable Protocol SHALL define change notification stream

The `ChangeObservable` Protocol SHALL be a `@runtime_checkable` Protocol with one method: `on_change() -> AsyncIterator[ChangeEvent] | None`. If the capability does not support change notifications, `on_change()` SHALL return `None`.

#### Scenario: Capability with change notifications
- **WHEN** `on_change()` is called on a `ChangeObservable` that supports notifications
- **THEN** an `AsyncIterator[ChangeEvent]` SHALL be returned
- **AND** `ChangeEvent` instances SHALL be yielded when the capability's state changes

#### Scenario: Capability without change notifications
- **WHEN** `on_change()` is called on a `ChangeObservable` that does not support notifications
- **THEN** `None` SHALL be returned

### Requirement: ChangeEvent SHALL carry capability name and source URI

The `ChangeEvent` dataclass SHALL retain `capability_name: str` for backward compatibility. It SHALL add `source_uri: str = ""` for URI-level routing. The `kind` field SHALL be `str` (widened from `Literal["tools_changed", ...]`) to allow future event types without protocol changes.

#### Scenario: ChangeEvent with capability name
- **WHEN** a `ChangeEvent` is created with `capability_name="github-mcp"` and `kind="tools_changed"`
- **THEN** `event.capability_name` SHALL return `"github-mcp"`
- **AND** `event.kind` SHALL return `"tools_changed"`

#### Scenario: ChangeEvent with source URI
- **WHEN** a `ChangeEvent` is created with `source_uri="mcp://github"`
- **THEN** `event.source_uri` SHALL return `"mcp://github"`
