## ADDED Requirements

### Requirement: Skill Python tools SHALL be registered as prefixed agent tools

When a skill declares `tools` frontmatter with `import_path` entries, `SkillManagerCap` SHALL eagerly import each tool via `SkillToolManager.import_tools()` at construction time and register them as agent tools with the prefix `{skill_name}__tool__`. The prefix ensures tool name isolation across skills. Imported tools SHALL be stored in `_skill_tools: dict[str, list[Tool]]` keyed by skill name.

#### Scenario: Skill with Python tools registered
- **WHEN** a skill named "my-skill" declares `tools: [{import_path: "json:loads"}]` in its frontmatter
- **AND** `SkillManagerCap` is constructed with this skill and a `SkillToolManager`
- **THEN** the agent's toolset SHALL include a tool named `my-skill__tool__loads`
- **AND** the tool SHALL be callable with the same parameters as `json.loads`

#### Scenario: Skill without tools does not register tools
- **WHEN** a skill named "plain-skill" has no `tools` frontmatter
- **AND** `SkillManagerCap` is constructed with this skill
- **THEN** no `plain-skill__tool__*` tools SHALL be registered

#### Scenario: Multiple skills with tools are isolated by prefix
- **WHEN** skill "alpha" declares `tools: [{import_path: "os:getcwd"}]`
- **AND** skill "beta" declares `tools: [{import_path: "os:getcwd"}]`
- **THEN** both `alpha__tool__getcwd` and `beta__tool__getcwd` SHALL be registered
- **AND** calling `alpha__tool__getcwd` SHALL NOT affect `beta__tool__getcwd`

### Requirement: Skill MCP servers SHALL be registered as prefixed agent tools

When a skill declares `mcp_servers` frontmatter, `SkillManagerCap` SHALL create a `McpServerCap` instance for each declared MCP server, store it in `_skill_mcp_children[skill_name]`, and add it to `_capabilities` for lifecycle management. Tools from these MCP servers SHALL be prefixed with `{skill_name}__mcp__` in the assembled toolset via the `get_toolset()` override.

#### Scenario: Skill with MCP server registered
- **WHEN** a skill named "my-skill" declares `mcp_servers: { server1: { command: "uvx", args: ["some-server"] } }`
- **AND** `SkillManagerCap` is constructed with this skill
- **THEN** a `McpServerCap` instance SHALL be created for "server1"
- **AND** it SHALL be stored in `_skill_mcp_children["my-skill"]`
- **AND** it SHALL be added to `_capabilities` for lifecycle management
- **AND** tools from "server1" SHALL be prefixed with `my-skill__mcp__`

#### Scenario: MCP server lifecycle managed by SkillManagerCap
- **WHEN** `SkillManagerCap.__aenter__()` is called
- **THEN** all per-skill `McpServerCap` children in `_capabilities` SHALL be entered
- **AND** when `__aexit__()` is called, all per-skill `McpServerCap` children SHALL be exited in reverse order

### Requirement: Skill allowed_tools SHALL filter registered tools

When a skill declares `allowed_tools` frontmatter as a non-empty list, `SkillManagerCap` SHALL filter the agent's toolset so that only tools matching the `allowed_tools` patterns are accessible for that skill. The filter SHALL apply to tools prefixed with `{skill_name}__` and compare against the bare tool name (after stripping the prefix). `allowed_tools` being `None` (not declared) means no filtering. `allowed_tools: []` (explicitly empty) means all skill tools are filtered out.

#### Scenario: allowed_tools filters skill tools
- **WHEN** a skill named "restricted" declares `allowed_tools: ["read", "list"]`
- **AND** the skill provides tools `restricted__tool__read`, `restricted__tool__write`, `restricted__tool__list`
- **THEN** `restricted__tool__read` and `restricted__tool__list` SHALL be accessible
- **AND** `restricted__tool__write` SHALL NOT be accessible

#### Scenario: No allowed_tools means all tools accessible
- **WHEN** a skill does not declare `allowed_tools` in its frontmatter
- **THEN** all tools prefixed with `{skill_name}__` SHALL be accessible without filtering

#### Scenario: Empty allowed_tools filters all skill tools
- **WHEN** a skill declares `allowed_tools: []` (explicitly empty list)
- **THEN** all tools prefixed with `{skill_name}__` SHALL be filtered out
- **AND** non-skill tools SHALL remain accessible

### Requirement: Skill tool registration SHALL survive pool rebuild

When `_rebuild_skill_capabilities()` is called (e.g., due to skill filesystem changes), the new `SkillManagerCap` instance SHALL re-import all Python tools and re-create all `McpServerCap` children from the updated skill set. No stale tool references SHALL remain.

#### Scenario: Pool rebuild with added skill
- **WHEN** `_rebuild_skill_capabilities()` is called after a new skill with `tools` is discovered
- **THEN** the new `SkillManagerCap` SHALL include tools from the newly discovered skill
- **AND** tools from previously registered skills SHALL still be present

### Requirement: Skill tool registration SHALL survive for_run() copy

When `SkillManagerCap.for_run()` is called, the new instance SHALL receive the same `tool_manager` reference so that `_skill_tools` is populated with the same imported Python tools. Per-skill `McpServerCap` children SHALL be propagated via `for_run()` on each child (which returns `self` by default for `McpServerCap`).

#### Scenario: for_run preserves Python tools
- **WHEN** `for_run(ctx)` is called on a `SkillManagerCap` with `_skill_tools` populated
- **THEN** the new instance SHALL have `tool_manager` set
- **AND** `_skill_tools` SHALL contain the same imported tools
