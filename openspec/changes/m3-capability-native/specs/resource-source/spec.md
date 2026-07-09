## ADDED Requirements

### Requirement: ResourceSource protocol defines read-only data access

A `ResourceSource` protocol SHALL provide unified read-only data access for MCP resources, skill content, and other data sources. It SHALL be orthogonal to `AbstractCapability` — the same object MAY implement both interfaces.

The protocol SHALL define four methods:
- `list() -> list[Resource]` — enumerate all available resources
- `read(uri: str) -> ResourceContent` — read resource content by URI
- `exists(uri: str) -> bool` — check if a resource exists
- `on_change() -> AsyncIterator[ResourceChange] | None` — subscribe to resource changes (None for static sources)

#### Scenario: List available resources

- **WHEN** `ResourceSource.list()` is called
- **THEN** a list of `Resource` dataclass instances SHALL be returned
- **AND** each `Resource` SHALL contain `uri`, `name`, `mime_type`, and `source` fields
- **AND** the list SHALL be empty (not raise) if no resources are available

#### Scenario: Read resource by URI

- **WHEN** `ResourceSource.read(uri)` is called with a valid URI
- **THEN** a `ResourceContent` dataclass SHALL be returned
- **AND** the `ResourceContent` SHALL contain `uri`, `content` (str or bytes), and `mime_type` fields

#### Scenario: Read non-existent resource raises

- **WHEN** `ResourceSource.read(uri)` is called with a URI that does not exist
- **THEN** a `ResourceNotFoundError` SHALL be raised

#### Scenario: Check resource existence

- **WHEN** `ResourceSource.exists(uri)` is called
- **THEN** a boolean SHALL be returned indicating whether the resource exists
- **AND** the method SHALL NOT raise for non-existent URIs

#### Scenario: Static source returns None for on_change

- **WHEN** a static `ResourceSource` (e.g., one backed by a fixed file set) is queried for change notifications
- **THEN** `on_change()` SHALL return `None`
- **AND** callers SHALL not attempt to iterate the return value

### Requirement: Resource and ResourceContent are frozen dataclasses

`Resource` and `ResourceContent` SHALL be `@dataclass(frozen=True)` instances. They SHALL be immutable after construction.

#### Scenario: Resource immutability

- **WHEN** code attempts to modify a field on a `Resource` instance after construction
- **THEN** a `FrozenInstanceError` SHALL be raised

#### Scenario: ResourceContent immutability

- **WHEN** code attempts to modify a field on a `ResourceContent` instance after construction
- **THEN** a `FrozenInstanceError` SHALL be raised

### Requirement: MCPCapability implements both AbstractCapability and ResourceSource

`MCPCapability` SHALL implement both `AbstractCapability` (providing MCP tools, hooks, instructions) and `ResourceSource` (providing MCP resources). The same object instance SHALL expose both interfaces.

#### Scenario: MCPCapability provides tools via Capability interface

- **WHEN** `MCPCapability.get_toolset()` is called
- **THEN** an `MCPToolset` SHALL be returned that auto-discovers tools from the MCP server
- **AND** the tools SHALL be usable by the agent without adapter wrapping

#### Scenario: MCPCapability provides resources via ResourceSource interface

- **WHEN** `MCPCapability.list()` is called
- **THEN** resources from the MCP server's `resources/list` endpoint SHALL be returned
- **AND** each resource URI SHALL use the `mcp://{server_name}/{path}` scheme

#### Scenario: MCPCapability reads resource by URI

- **WHEN** `MCPCapability.read("mcp://filesystem/readme.md")` is called
- **THEN** the MCP server's `resources/read` endpoint SHALL be invoked with the stripped path
- **AND** a `ResourceContent` with the file content SHALL be returned

#### Scenario: isinstance check for ResourceSource

- **WHEN** `isinstance(mcp_capability, ResourceSource)` is evaluated
- **THEN** the result SHALL be `True`
- **AND** `isinstance(mcp_capability, AbstractCapability)` SHALL also be `True`

### Requirement: SkillResourceSource provides skill content as resources

`SkillCapability` SHALL also implement `ResourceSource`, exposing SKILL.md file content as resources with `skill://{skill_name}` URI scheme.

#### Scenario: List available skills as resources

- **WHEN** `SkillCapability.list()` is called
- **THEN** all discovered SKILL.md files SHALL be returned as `Resource` instances
- **AND** each resource URI SHALL use the `skill://{skill_name}` scheme

#### Scenario: Read skill content by URI

- **WHEN** `SkillCapability.read("skill://my-skill")` is called
- **THEN** the SKILL.md file content SHALL be returned as `ResourceContent`
- **AND** the `mime_type` SHALL be `"text/markdown"`

### Requirement: AggregatedResourceSource composes multiple sources at compile time

`AggregatedResourceSource` SHALL compose multiple `ResourceSource` instances into a unified interface. It SHALL be created by `AgentFactory` at compile time, scoped to the agent's authorized resources — NOT a global registry.

#### Scenario: Aggregated list merges all sources

- **WHEN** `AggregatedResourceSource.list()` is called
- **THEN** resources from all composed sources SHALL be returned in a single list
- **AND** each resource SHALL retain its original URI scheme

#### Scenario: Aggregated read routes to correct source

- **WHEN** `AggregatedResourceSource.read("mcp://filesystem/readme.md")` is called
- **THEN** the MCP source's `read()` method SHALL be invoked
- **AND** the result SHALL be returned transparently

#### Scenario: Aggregated read with unknown URI raises

- **WHEN** `AggregatedResourceSource.read("unknown://resource")` is called
- **AND** no composed source recognizes the URI
- **THEN** a `ResourceNotFoundError` SHALL be raised

#### Scenario: Aggregated exists checks all sources

- **WHEN** `AggregatedResourceSource.exists(uri)` is called
- **THEN** the method SHALL return `True` if ANY composed source recognizes the URI
- **AND** the method SHALL return `False` if NO composed source recognizes the URI
