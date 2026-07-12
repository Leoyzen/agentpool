## MODIFIED Requirements

### Requirement: SkillManagerCap SHALL extend CombinedToolsetCapability

`SkillManagerCap` SHALL inherit from `CombinedToolsetCapability` to reuse `on_change()` (merge streams) and `__aenter__`/`__aexit__` (lifecycle). It SHALL additionally implement `SkillResource`, `CommandResource`, and `ChangeObservable`. It SHALL also accept an optional `SkillToolManager` and eagerly import Python tools for skills with `tools` frontmatter. It SHALL fully override `get_toolset()` — it SHALL NOT call `super().get_toolset()` — to create `PrefixedToolset` wrappers for both Python tools and per-skill McpServerCap children.

#### Scenario: SkillManagerCap inherits tool merging
- **WHEN** `get_toolset()` is called on `SkillManagerCap`
- **THEN** it SHALL create `PrefixedToolset("{skill_name}__tool__")` for each skill with Python tools
- **AND** it SHALL create `PrefixedToolset("{skill_name}__mcp__")` for each per-skill McpServerCap child
- **AND** it SHALL include non-skill children's toolsets unprefixed
- **AND** all three categories SHALL be combined into a single `CombinedToolset`

#### Scenario: SkillManagerCap inherits lifecycle management
- **WHEN** `SkillManagerCap.__aenter__()` is called
- **THEN** all child capabilities in `_capabilities` (including per-skill McpServerCap instances) SHALL be entered
- **AND** when `__aexit__()` is called, all children SHALL be exited in reverse order

#### Scenario: SkillManagerCap imports Python tools eagerly
- **WHEN** `SkillManagerCap(local_skills={"my-skill": skill}, tool_manager=manager)` is constructed
- **AND** "my-skill" has `tools: [{import_path: "json:loads"}]` frontmatter
- **THEN** `tool_manager.import_tools(skill.tools)` SHALL be called during construction
- **AND** the imported tools SHALL be stored in `_skill_tools["my-skill"]` as `list[Tool]`

#### Scenario: SkillManagerCap get_toolset does not call super
- **WHEN** `get_toolset()` is overridden in `SkillManagerCap`
- **THEN** `super().get_toolset()` SHALL NOT be called
- **AND** the override SHALL manually iterate `_skill_tools`, `_skill_mcp_children`, and `_capabilities` (excluding skill children) to build the combined toolset

## ADDED Requirements

### Requirement: SkillManagerCap SHALL track per-skill McpServerCap children in _skill_mcp_children

For each skill in `local_skills` that declares `mcp_servers` frontmatter, `SkillManagerCap` SHALL create `McpServerCap` instances per declared MCP server, store them in `_skill_mcp_children: dict[str, list[McpServerCap]]` (keyed by skill name), and add them to `_capabilities` for lifecycle management. The `_skill_mcp_children` mapping is used by `get_toolset()` to apply the correct `{skill_name}__mcp__` prefix per skill.

#### Scenario: Per-skill McpServerCap tracked in mapping
- **WHEN** `SkillManagerCap` is constructed with a skill "my-skill" that declares `mcp_servers: { server1: { command: "uvx" } }`
- **THEN** a `McpServerCap` instance SHALL be created for "server1"
- **AND** it SHALL be stored in `_skill_mcp_children["my-skill"]`
- **AND** it SHALL also be added to `_capabilities` for lifecycle management

### Requirement: SkillManagerCap SHALL be registered with ExtensionRegistry at POOL scope

After `SkillManagerCap` is created in `_rebuild_skill_capabilities()`, it SHALL be registered with `ExtensionRegistry` at `ScopeLevel.POOL` so that `ExtensionRegistry.resolve_uri()` can discover it for `skill://{name}` URI resolution. Flat URIs without a provider segment eliminate the need for capability name matching.

#### Scenario: SkillManagerCap registered at pool scope
- **WHEN** `_rebuild_skill_capabilities()` creates a `SkillManagerCap`
- **THEN** `extension_registry.register(skill_manager_cap, ScopeLevel.POOL)` SHALL be called
- **AND** `ExtensionRegistry.get_skill_resources(scope)` SHALL return the `SkillManagerCap` for any scope

#### Scenario: skill:// URI resolves through ExtensionRegistry with flat URI
- **WHEN** `SkillURIResolver.resolve("skill://my-skill")` is called
- **AND** `SkillURIResolver` has `extension_registry` set
- **THEN** `ExtensionRegistry.resolve_uri()` SHALL extract `provider_name=None` from the flat URI
- **AND** it SHALL try all visible `SkillResource` capabilities without filtering
- **AND** `SkillManagerCap.read_skill("my-skill")` SHALL be called
- **AND** the skill content SHALL be returned

### Requirement: SkillManagerCap SHALL filter tools via composite allowed_tools filter

When one or more skills in `local_skills` declare `allowed_tools` frontmatter (non-empty list), `SkillManagerCap` SHALL override `get_wrapper_toolset()` to apply a `FilteredToolset` with a composite filter function. The filter SHALL: (a) allow all non-skill tools (tools not prefixed with any `{skill_name}__`), (b) for skill tools, strip the prefix and check the bare name against that skill's `allowed_tools` set. When `allowed_tools` is `None` (not declared), no filtering SHALL be applied. When `allowed_tools` is an explicit empty list `[]`, all skill tools SHALL be filtered out.

#### Scenario: allowed_tools filtering applied
- **WHEN** a skill "restricted" declares `allowed_tools: ["read", "list"]`
- **AND** `get_wrapper_toolset(toolset)` is called on `SkillManagerCap`
- **THEN** tools `restricted__tool__read` and `restricted__tool__list` SHALL remain accessible
- **AND** tool `restricted__tool__write` SHALL be filtered out

#### Scenario: allowed_tools not declared means no filter
- **WHEN** a skill does not declare `allowed_tools` in its frontmatter
- **THEN** no `FilteredToolset` SHALL be applied
- **AND** all tools SHALL be accessible

#### Scenario: allowed_tools empty list filters all skill tools
- **WHEN** a skill "blocked" declares `allowed_tools: []` (explicitly empty)
- **THEN** all tools prefixed with `blocked__` SHALL be filtered out
- **AND** non-skill tools SHALL remain accessible

#### Scenario: Multiple skills with different allowed_tools
- **WHEN** skill "alpha" declares `allowed_tools: ["read"]` and skill "beta" declares `allowed_tools: ["write"]`
- **THEN** `alpha__tool__read` SHALL be accessible, `alpha__tool__write` SHALL be filtered
- **AND** `beta__tool__write` SHALL be accessible, `beta__tool__read` SHALL be filtered
- **AND** non-skill tools SHALL remain accessible

### Requirement: SkillManagerCap for_run() SHALL propagate tool_manager

`SkillManagerCap.for_run()` SHALL pass `tool_manager=self._tool_manager` to the new `SkillManagerCap` instance. This ensures per-run copies have the same Python tools imported as the original. Without this, per-run copies would have `tool_manager=None` and no `_skill_tools`, causing Python tools to disappear.

#### Scenario: for_run preserves tool_manager
- **WHEN** `for_run(ctx)` is called on a `SkillManagerCap` with `tool_manager` set
- **THEN** the new `SkillManagerCap` instance SHALL have `tool_manager` set to the same value
- **AND** `_skill_tools` SHALL be populated with the same imported tools
