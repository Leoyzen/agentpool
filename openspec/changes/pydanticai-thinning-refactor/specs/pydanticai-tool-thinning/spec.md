## ADDED Requirements

### Requirement: AgentPool Tool is a thin wrapper over pydantic_ai.tools.Tool
AgentPool's `Tool` dataclass SHALL be a thin wrapper over `pydantic_ai.tools.Tool`. The `Tool.to_pydantic_ai()` method SHALL produce a 1:1 mapping without complex conversion logic. Redundant metadata fields that PydanticAI's `Tool` already provides SHALL be removed.

#### Scenario: Tool converts to PydanticAI Tool with 1:1 mapping
- **WHEN** `Tool.to_pydantic_ai()` is called on an AgentPool `Tool` instance
- **THEN** it produces a `pydantic_ai.tools.Tool` with function, name, description, and parameters mapped directly
- **AND** no complex conversion logic or edge-case handling is present in the method
- **AND** the method body is fewer than 20 lines

#### Scenario: Tool uses PydanticAI requires_approval natively
- **WHEN** a tool is configured with `requires_confirmation: true` and no deferred execution
- **THEN** the resulting `pydantic_ai.tools.Tool` has `requires_approval=True` set directly
- **AND** no `ApprovalRequiredToolset` wrapper is applied for non-deferred confirmation

#### Scenario: Deferred tools still use ApprovalRequiredToolset
- **WHEN** a tool is configured with `deferred: true` and a deferred strategy (`block`/`continue`/`stream`)
- **THEN** the tool uses `ApprovalRequiredToolset` wrapping (preserved for deferred execution scenarios)
- **AND** PydanticAI's `requires_approval` is not used for deferred tools

### Requirement: ToolKind taxonomy is removed
The `ToolKind` enum and associated taxonomy (`read`/`edit`/`delete`/`execute`/etc.) SHALL be removed. Tool categorization for config validation SHALL use string-based tool name patterns instead of a formal taxonomy.

#### Scenario: Config validation without ToolKind
- **WHEN** a YAML config defines tool permissions (e.g., `allowed_tools: ["bash", "read"]`)
- **THEN** validation uses string matching on tool names, not `ToolKind` enum values
- **AND** no `ToolKind` import or reference exists in the codebase

#### Scenario: Tool instance has no kind field
- **WHEN** a `Tool` instance is created
- **THEN** it does not have a `kind` field or `ToolKind` attribute
- **AND** tool metadata does not include kind categorization

### Requirement: ToolResult structured_content is removed
`ToolResult.structured_content` field SHALL be removed. Structured tool returns SHALL use PydanticAI's native `ToolReturn` structured return mechanism directly.

#### Scenario: Tool returns structured data
- **WHEN** a tool returns structured data (e.g., a Pydantic model)
- **THEN** the tool returns a `pydantic_ai.messages.ToolReturn` with structured content set via PydanticAI's native mechanism
- **AND** no `ToolResult.structured_content` field is present on AgentPool's `ToolResult`

## REMOVED Requirements

### Requirement: ToolKind taxonomy for tool categorization
**Reason**: `ToolKind` was a categorization system for tool permissions, but it's only used in config validation, not at runtime. String-based tool name patterns provide the same validation capability with less overhead.
**Migration**: Replace `kind: read` config entries with tool name patterns (e.g., `allowed_tools: ["read", "grep"]`).

### Requirement: ToolResult.structured_content for machine-readable returns
**Reason**: PydanticAI's `ToolReturn` already supports structured returns natively. Maintaining a separate `structured_content` field on AgentPool's `ToolResult` is redundant.
**Migration**: Use `pydantic_ai.messages.ToolReturn` with structured content directly.
