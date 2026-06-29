## ADDED Requirements

### Requirement: ToolInfoRegistry provides configurable tool metadata inference
The system SHALL provide a `ToolInfoRegistry` that maps tool names and input arguments to `RichToolInfo` (title, kind, content items, locations). The registry SHALL be initialized from YAML `tool_mappings` configuration with built-in rules as fallback. User-defined rules SHALL take priority over built-in rules (first-match-wins ordering).

#### Scenario: Built-in rules loaded by default
- **WHEN** no `tool_mappings` configuration is provided
- **THEN** the registry SHALL load built-in rules covering common tool names (`edit`, `write`, `read`, `bash`, `grep`, etc.)
- **AND** `derive("edit", {"file_path": "/a.py", "old_string": "x", "new_string": "y"})` SHALL return `RichToolInfo` with `kind="edit"` and a `DiffContentItem` in `content`

#### Scenario: User rule overrides built-in rule
- **WHEN** `tool_mappings` config contains a rule for tool name `"edit"` with `kind: "other"`
- **AND** the registry derives info for `("edit", {"file_path": "/a.py"})`
- **THEN** the user rule SHALL take precedence
- **AND** the returned `kind` SHALL be `"other"`

#### Scenario: Custom MCP tool mapping
- **WHEN** `tool_mappings` config contains a rule for tool name `"mcp__scratchpad__patch"` with diff content extraction
- **AND** the registry derives info for `("mcp__scratchpad__patch", {"file_path": "/a.py", "original": "x", "patched": "y"})`
- **THEN** the returned `RichToolInfo` SHALL contain a `DiffContentItem` with `old_text="x"` and `new_text="y"`

#### Scenario: Unmatched tool returns default RichToolInfo
- **WHEN** no rule matches the tool name
- **THEN** the registry SHALL return `RichToolInfo(title=<tool_name>, kind="other")` with empty `content` and `locations`

### Requirement: Field extraction supports multi-field fallback
The system SHALL support `FieldExtract` configuration that tries multiple field names in order, returning the first non-None value from the tool input dictionary. When all specified fields are absent, the `default` value SHALL be used if provided, otherwise `None`.

#### Scenario: First field found
- **WHEN** `FieldExtract(fields=["file_path", "path"])` is applied to `{"file_path": "/a.py"}`
- **THEN** the extracted value SHALL be `"/a.py"`

#### Scenario: Fallback to second field
- **WHEN** `FieldExtract(fields=["file_path", "path"])` is applied to `{"path": "/a.py"}`
- **THEN** the extracted value SHALL be `"/a.py"`

#### Scenario: All fields absent with default
- **WHEN** `FieldExtract(fields=["file_path", "path"], default=".")` is applied to `{}`
- **THEN** the extracted value SHALL be `"."`

#### Scenario: All fields absent without default
- **WHEN** `FieldExtract(fields=["file_path", "path"])` is applied to `{}`
- **THEN** the extracted value SHALL be `None`

### Requirement: Tool name matching supports MCP prefix and wildcards
The system SHALL match tool names case-insensitively. Rules MAY use wildcard patterns (`mcp__server__*`) to match all tools from a specific MCP server. The `*` wildcard SHALL match greedily — everything after the prefix, including additional `__` segments. Rules with `match_mcp_suffix: true` SHALL match both the literal tool name and the suffix of MCP-prefixed names (e.g., `edit` matches `mcp__any__edit`). The `match_mcp_suffix` flag is a per-rule boolean that applies to ALL tool names in the rule's `tool_names` list.

#### Scenario: Exact name match
- **WHEN** a rule specifies `tool_names: ["edit"]`
- **AND** the tool name is `"edit"`
- **THEN** the rule SHALL match

#### Scenario: Case-insensitive match
- **WHEN** a rule specifies `tool_names: ["Edit"]`
- **AND** the tool name is `"edit"`
- **THEN** the rule SHALL match

#### Scenario: MCP suffix match
- **WHEN** a rule specifies `tool_names: ["edit"]` with `match_mcp_suffix: true`
- **AND** the tool name is `"mcp__filesystem__edit"`
- **THEN** the rule SHALL match

#### Scenario: Wildcard match
- **WHEN** a rule specifies `tool_names: ["mcp__scratchpad__*"]`
- **AND** the tool name is `"mcp__scratchpad__patch"`
- **THEN** the rule SHALL match

#### Scenario: Wildcard does not cross server boundary
- **WHEN** a rule specifies `tool_names: ["mcp__scratchpad__*"]`
- **AND** the tool name is `"mcp__filesystem__patch"`
- **THEN** the rule SHALL NOT match

#### Scenario: Wildcard matches nested tool names
- **WHEN** a rule specifies `tool_names: ["mcp__scratchpad__*"]`
- **AND** the tool name is `"mcp__scratchpad__sub__tool"`
- **THEN** the rule SHALL match (greedy `*` matches `sub__tool`)

#### Scenario: match_mcp_suffix applies to all names in rule
- **WHEN** a rule specifies `tool_names: ["edit", "write"]` with `match_mcp_suffix: true`
- **AND** the tool name is `"mcp__filesystem__write"`
- **THEN** the rule SHALL match (suffix matching applies to both `edit` and `write`)

### Requirement: Title template uses placeholder substitution
The system SHALL render title templates using `{field_name}` placeholder syntax. Placeholders SHALL be substituted with values extracted from the tool input using the same field extraction logic. Missing placeholders SHALL be replaced with an empty string.

#### Scenario: Single placeholder
- **WHEN** the title template is `"Edit {file_path}"` and input is `{"file_path": "/a.py"}`
- **THEN** the rendered title SHALL be `"Edit /a.py"`

#### Scenario: Multiple placeholders
- **WHEN** the title template is `"Search '{pattern}' in {path}"` and input is `{"pattern": "foo", "path": "/src"}`
- **THEN** the rendered title SHALL be `"Search 'foo' in /src"`

#### Scenario: Missing placeholder value
- **WHEN** the title template is `"Edit {file_path}"` and input is `{}`
- **THEN** the rendered title SHALL be `"Edit "`

### Requirement: Content mapping produces DiffContentItem from tool input
The system SHALL construct `DiffContentItem` instances from tool input when a `ContentMapping` with `kind: "diff"` is configured. The `path`, `old_text`, and `new_text` fields SHALL be extracted from the tool input using `FieldExtract` configuration. When `old_text` extraction yields `None`, the `DiffContentItem.old_text` SHALL be `None` (indicating a new file).

#### Scenario: Edit tool produces diff
- **WHEN** a content mapping has `kind: "diff"`, `path: {fields: ["file_path"]}`, `old_text: {fields: ["old_string"]}`, `new_text: {fields: ["new_string"]}`
- **AND** the tool input is `{"file_path": "/a.py", "old_string": "x", "new_string": "y"}`
- **THEN** the resulting `DiffContentItem` SHALL have `path="/a.py"`, `old_text="x"`, `new_text="y"`

#### Scenario: Write tool produces diff with None old_text
- **WHEN** a content mapping has `kind: "diff"`, `path: {fields: ["file_path"]}`, `old_text: null`, `new_text: {fields: ["content"]}`
- **AND** the tool input is `{"file_path": "/a.py", "content": "hello"}`
- **THEN** the resulting `DiffContentItem` SHALL have `path="/a.py"`, `old_text=None`, `new_text="hello"`

### Requirement: Location mapping produces LocationContentItem from tool input
The system SHALL construct `LocationContentItem` instances from tool input when a `LocationMapping` is configured. The `path` and optional `line` fields SHALL be extracted using `FieldExtract` configuration.

#### Scenario: Location with path only
- **WHEN** a location mapping has `path: {fields: ["file_path"]}`
- **AND** the tool input is `{"file_path": "/a.py"}`
- **THEN** the resulting `LocationContentItem` SHALL have `path="/a.py"` and `line=0`

#### Scenario: Location with path and line
- **WHEN** a location mapping has `path: {fields: ["file_path"]}`, `line: {fields: ["offset"]}`
- **AND** the tool input is `{"file_path": "/a.py", "offset": 10}`
- **THEN** the resulting `LocationContentItem` SHALL have `path="/a.py"` and `line=10`

### Requirement: tool_mappings YAML configuration field
The `AgentsManifest` SHALL accept a top-level `tool_mappings` field of type `list[ToolMappingConfig]`. When provided, the mappings SHALL be compiled into `ToolInfoRegistry` rules that take priority over built-in rules. When omitted, only built-in rules SHALL be used.

#### Scenario: Config with tool mappings
- **WHEN** the YAML config contains `tool_mappings` with one rule for `"mcp__scratchpad__patch"`
- **THEN** `AgentPool` SHALL initialize a `ToolInfoRegistry` with the user rule first, followed by built-in rules
- **AND** the registry SHALL be passed to `EventMapper` instances via `AgentRunContext`

#### Scenario: Config without tool mappings
- **WHEN** the YAML config does not contain `tool_mappings`
- **THEN** `AgentPool` SHALL initialize a `ToolInfoRegistry` with only built-in rules
- **AND** behavior SHALL be identical to the pre-change `derive_rich_tool_info()` function

### Requirement: derive_rich_tool_info delegates to registry
The `derive_rich_tool_info()` function SHALL delegate to the default `ToolInfoRegistry` instance. The function signature SHALL remain unchanged for backward compatibility. Code calling `derive_rich_tool_info(name, input_data)` SHALL continue to work without modification.

#### Scenario: Backward compatible call
- **WHEN** `derive_rich_tool_info("edit", {"file_path": "/a.py", "old_string": "x", "new_string": "y"})` is called
- **THEN** the result SHALL be identical to the pre-change behavior
- **AND** the result SHALL contain `kind="edit"` and a `DiffContentItem` in `content`
