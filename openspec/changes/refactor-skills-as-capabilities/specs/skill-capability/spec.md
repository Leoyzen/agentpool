## ADDED Requirements

### Requirement: SkillCapability wraps a Skill as an AbstractCapability
The system SHALL provide a `SkillCapability` class that extends `pydantic_ai.capabilities.AbstractCapability` and wraps a single `Skill` instance, enabling skills to participate in pydantic-ai's capability middleware chain.

#### Scenario: SkillCapability provides instructions
- **WHEN** `SkillCapability.get_instructions()` is called
- **THEN** it SHALL return the skill's instruction content as `AgentInstructions` (without XML wrapper — the `<available-skills>` wrapper is owned by `SkillsInstructionProvider`)

#### Scenario: SkillCapability provides pre-registered tools
- **WHEN** `SkillCapability.get_toolset()` is called at agent construction time and the skill has `tools` declared in its frontmatter
- **THEN** it SHALL return a `PrefixedToolset` wrapping a `FunctionToolset` containing the skill's native Python tools, prefixed with `{skill_name}__tool__`

#### Scenario: SkillCapability provides pre-registered MCP servers
- **WHEN** `SkillCapability.get_toolset()` is called at agent construction time and the skill has `mcp_servers` declared
- **THEN** it SHALL return a `PrefixedToolset` wrapping a `CombinedToolset` of MCP toolsets, prefixed with `{skill_name}__mcp__`

#### Scenario: SkillCapability with both tools and MCP
- **WHEN** `SkillCapability.get_toolset()` is called and the skill has both `tools` and `mcp_servers`
- **THEN** it SHALL return a `CombinedToolset` containing both the native tools `PrefixedToolset` and the MCP `PrefixedToolset`

#### Scenario: SkillCapability enforces allowed_tools
- **WHEN** `SkillCapability.get_wrapper_toolset(toolset)` is called and the skill has `allowed_tools` declared
- **THEN** it SHALL return a `FilteredToolset` whose filter function checks whether the current skill is active and, if so, only allows tools whose names match the `allowed_tools` list

#### Scenario: SkillCapability participates in middleware ordering
- **WHEN** `SkillCapability.get_ordering()` is called
- **THEN** it SHALL return `CapabilityOrdering(wrapped_by=[ProcessHistory, NativeTool])` so that the capability is positioned after MCP but before history processors and native tools

#### Scenario: SkillCapability with no tools or MCP returns None toolset
- **WHEN** `SkillCapability.get_toolset()` is called and the skill has neither `tools` nor `mcp_servers`
- **THEN** it SHALL return `None`, delegating entirely to `get_instructions()` for contribution

#### Scenario: SkillCapability.on_run_ended triggers cleanup
- **WHEN** an agent run completes (success or failure)
- **THEN** `SkillCapability.on_run_ended()` SHALL trigger `SkillMcpManager.cleanup(session_id)` to disconnect all skill-level MCP servers for the session (session_id obtained from `ctx.deps`)

### Requirement: Skill model supports mcp_servers and tools frontmatter fields
The `Skill` Pydantic model SHALL accept optional `mcp_servers` and `tools` fields in its YAML frontmatter parsing, and SHALL preserve backward compatibility for `allowed_tools` through a field validator.

#### Scenario: Skill with mcp_servers declared
- **WHEN** a SKILL.md file contains `mcp_servers:` with one or more server definitions in its YAML frontmatter
- **THEN** `Skill.from_skill_dir()` SHALL parse and validate them as `dict[str, SkillMcpServerConfig]`

#### Scenario: Skill with tools declared
- **WHEN** a SKILL.md file contains `tools:` with one or more tool definitions in its YAML frontmatter
- **THEN** `Skill.from_skill_dir()` SHALL parse and validate them as `list[SkillToolConfig]`

#### Scenario: Skill without mcp_servers or tools
- **WHEN** a SKILL.md file does not contain `mcp_servers` or `tools` in its frontmatter
- **THEN** `Skill.from_skill_dir()` SHALL parse successfully with both fields defaulting to `None`

#### Scenario: allowed_tools accepts string format (backward compat)
- **WHEN** a SKILL.md file has `allowed-tools: "bash, read, grep"` (space-separated string, pre-existing format)
- **THEN** the `allowed_tools` field SHALL be stored as `str` on the model (type unchanged), with a helper method `parsed_allowed_tools() -> list[str]` for structured access

#### Scenario: allowed_tools accepts list format
- **WHEN** a SKILL.md file has `allowed-tools: ["bash", "read", "grep"]` (YAML list)
- **THEN** a `@field_validator` SHALL normalize it to the space-separated string format for storage, and `parsed_allowed_tools()` SHALL return the parsed list

#### Scenario: SkillMcpServerConfig supports stdio transport
- **WHEN** a `SkillMcpServerConfig` has `command` and `args` fields
- **THEN** it SHALL be treated as a stdio MCP server, launched as a subprocess on first tool call (lazy connection)

#### Scenario: SkillMcpServerConfig supports HTTP transport
- **WHEN** a `SkillMcpServerConfig` has a `url` field
- **THEN** it SHALL be treated as an HTTP MCP server, connected via streamable HTTP on first tool call (lazy connection)

#### Scenario: SkillToolConfig supports Python import paths
- **WHEN** a `SkillToolConfig` has `type: python` and `import_path: "module.path:function_name"`
- **THEN** the system SHALL dynamically import and register the function as a tool when `load_skill` activates the skill

### Requirement: mcp.json companion file
Skills MAY include an `mcp.json` file in their directory as an alternative to the `mcp_servers` YAML frontmatter field.

#### Scenario: mcp.json takes precedence over frontmatter
- **WHEN** a skill directory contains both `mcp_servers` in SKILL.md frontmatter AND an `mcp.json` file
- **THEN** the MCP server configuration from `mcp.json` SHALL be used, and the frontmatter `mcp_servers` SHALL be ignored

#### Scenario: mcp.json with mcpServers key
- **WHEN** `mcp.json` contains `{"mcpServers": {"server-name": {...}}}`
- **THEN** the `mcpServers` value SHALL be parsed as `dict[str, SkillMcpServerConfig]`

#### Scenario: mcp.json supports env var expansion
- **WHEN** `mcp.json` contains values with `${VAR_NAME}` patterns
- **THEN** those patterns SHALL be expanded from environment variables at load time

### Requirement: SkillsInstructionProvider aggregates SkillCapability instructions
The `SkillsInstructionProvider` SHALL remain the owner of the `<available-skills>` XML wrapper format, collecting instruction content from all `SkillCapability` instances.

#### Scenario: Metadata mode includes tool/MCP hints
- **WHEN** `SkillsInstructionProvider` is configured with `injection_mode="metadata"` and skills have tools or MCP servers
- **THEN** the generated `<available-skills>` XML SHALL include `<tools>` and `<mcp_servers>` elements listing available capabilities

#### Scenario: Off mode suppresses all skill content
- **WHEN** `SkillsInstructionProvider` is configured with `injection_mode="off"`
- **THEN** no skill instructions, tool hints, or MCP hints SHALL be injected into prompts

#### Scenario: Full mode includes complete instructions
- **WHEN** `SkillsInstructionProvider` is configured with `injection_mode="full"`
- **THEN** the generated XML SHALL include the skill's complete instruction body alongside tool/MCP hints

### Requirement: Agent.get_agentlet injects SkillCapability instances
The native agent's `get_agentlet()` method SHALL create `SkillCapability` instances for all discovered skills and inject them into the pydantic-ai capabilities chain at the correct position.

#### Scenario: Skills injected into capability chain
- **WHEN** an agent is initialized with skills configured (via `skills.paths` or session-level MCP skills)
- **THEN** `get_agentlet()` SHALL create one `SkillCapability` per discovered skill and append them to `tool_capabilities` after MCP capabilities but before ProcessHistory

#### Scenario: SkillCapability ordering is verified
- **WHEN** `get_agentlet()` assembles the capability chain
- **THEN** all `SkillCapability` instances SHALL appear after all `MCP` capability instances and before all `ProcessHistory` capability instances

#### Scenario: No skills configured produces no SkillCapability
- **WHEN** an agent has no skills configured
- **THEN** `get_agentlet()` SHALL NOT create any `SkillCapability` instances

### Requirement: load_skill activates skill backends and injects instructions
The `load_skill` tool SHALL activate the skill's MCP servers and native tools (lazy connection), and inject the skill's instructions into the current run's prompt. Tools are pre-registered at construction time — `load_skill` does NOT add new tools mid-run.

#### Scenario: load_skill activates MCP backends
- **WHEN** `load_skill("my-skill")` is called and the skill has `mcp_servers` declared
- **THEN** `SkillMcpManager` SHALL lazily prepare the MCP servers for connection (actual connection on first tool call)

#### Scenario: load_skill activates native tools
- **WHEN** `load_skill("my-skill")` is called and the skill has `tools` declared
- **THEN** `SkillToolManager` SHALL import the declared tool functions (lazy — on first use)

#### Scenario: load_skill response includes tool/MCP descriptions
- **WHEN** `load_skill("my-skill")` completes successfully and the skill has tools or MCP servers
- **THEN** the response SHALL include `## Activated Tools` and `## Activated MCP Servers` sections listing available capabilities

#### Scenario: load_skill without tools/MCP preserves existing behavior
- **WHEN** `load_skill("existing-skill")` is called for a skill without `mcp_servers` or `tools`
- **THEN** the response SHALL be identical to the pre-refactor behavior (instructions + metadata only, no tool/MCP sections)

### Requirement: SkillMcpManager manages per-skill MCP lifecycle
The `SkillMcpManager` SHALL manage MCP server connections scoped to skill activation, with lazy connection, thread safety, and automatic cleanup.

#### Scenario: Lazy MCP server connection
- **WHEN** a skill with `mcp_servers` is activated via `load_skill`
- **THEN** the MCP server SHALL NOT be connected until the first tool call to that server

#### Scenario: MCP server cleanup on run end
- **WHEN** the agent run completes (success or failure)
- **THEN** all skill-level MCP server connections SHALL be terminated via `SkillCapability.on_run_ended()`

#### Scenario: Idle timeout for unused connections
- **WHEN** a skill-level MCP server connection has been idle for the configured timeout period (default 5 minutes)
- **THEN** the connection SHALL be terminated to free resources

#### Scenario: Thread safety for concurrent load_skill
- **WHEN** two concurrent sessions call `load_skill` for the same skill simultaneously
- **THEN** a per-server-name `asyncio.Lock` SHALL serialize connection attempts, with the second caller awaiting the first connection's result

#### Scenario: Retry with exponential backoff
- **WHEN** an MCP server connection fails
- **THEN** the system SHALL retry up to 3 times with exponential backoff before reporting failure

### Requirement: Backward compatibility with existing config
All existing YAML configuration options and SKILL.md formats for skills SHALL continue to work identically after this change.

#### Scenario: Existing skills.paths config works
- **WHEN** a YAML config specifies `skills.paths: ["./skills/"]` without `mcp_servers` or `tools` in the skill's SKILL.md
- **THEN** skills SHALL be discovered, loaded, and injected into prompts exactly as before

#### Scenario: Existing skills.instruction config works
- **WHEN** a YAML config specifies `skills.instruction.mode: "metadata"` and `skills.instruction.max_skills: 10`
- **THEN** skills SHALL be injected in metadata mode with a maximum of 10 skills, exactly as before

#### Scenario: Existing load_skill tool behavior preserved
- **WHEN** `load_skill("existing-skill")` is called for a skill without `mcp_servers` or `tools`
- **THEN** the response SHALL be identical to the pre-refactor behavior (instructions + metadata only)

#### Scenario: Existing allowed-tools string format works
- **WHEN** a SKILL.md file has `allowed-tools: "bash, read, grep"` (space-separated string, pre-existing format)
- **THEN** the skill SHALL parse successfully and `parsed_allowed_tools()` SHALL return `["bash", "read", "grep"]`
