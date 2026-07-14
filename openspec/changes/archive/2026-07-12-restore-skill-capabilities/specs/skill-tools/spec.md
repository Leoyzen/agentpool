## ADDED Requirements

### Requirement: load_skill tool SHALL resolve skills by bare name or URI

The `load_skill` tool SHALL accept a `skill_name` parameter that is either a bare skill name (e.g., `"ponytail"`) or a `skill://` URI in flat format (e.g., `"skill://ponytail"`). For bare names, the tool SHALL search all visible `SkillResource` capabilities via `list_skills()`. For URIs, the tool SHALL delegate to `SkillURIResolver.resolve()`.

#### Scenario: Load skill by bare name
- **WHEN** `load_skill(ctx, skill_name="ponytail")` is called
- **AND** "ponytail" exists in a visible `SkillResource` capability
- **THEN** the skill's instructions SHALL be returned as a string
- **AND** the skill's tools and MCP server status SHALL be included in the response

#### Scenario: Load skill by URI
- **WHEN** `load_skill(ctx, skill_name="skill://ponytail")` is called
- **AND** the URI resolver can resolve the skill via flat URI
- **THEN** the skill's instructions SHALL be returned as a string

#### Scenario: Skill not found
- **WHEN** `load_skill(ctx, skill_name="nonexistent")` is called
- **AND** no visible `SkillResource` has a skill named "nonexistent"
- **THEN** a `SkillNotFoundError` SHALL be raised with a descriptive message

### Requirement: load_skill SHALL dispatch via SkillResource protocol for MCP providers

The `load_skill` and `list_skills` tools SHALL use `isinstance(provider, SkillResource)` checks to discover MCP skill providers, NOT `getattr(provider, 'get_skills', None)` duck-typing. The tools SHALL call `list_skills()`, `read_skill()`, and `skill_exists()` methods as defined by the `SkillResource` protocol. This migration applies ONLY to the MCP provider iteration path. The local skills path (`ctx.pool.skills.list_skills()` returning `Skill` objects) SHALL remain unchanged — `ctx.pool` → `host_context` migration is deferred to a separate change.

#### Scenario: Skill discovery via protocol
- **WHEN** `list_skills(ctx)` is called
- **AND** the agent context has a `SkillManagerCap` and a `McpServerCap` both implementing `SkillResource`
- **THEN** both SHALL be queried via `list_skills()`
- **AND** their combined results SHALL be returned

#### Scenario: Non-SkillResource capabilities are skipped
- **WHEN** `list_skills(ctx)` is called
- **AND** the agent context has capabilities that do NOT implement `SkillResource`
- **THEN** those capabilities SHALL NOT be queried
- **AND** no `AttributeError` SHALL be raised

### Requirement: load_skill SHALL support argument substitution

When the `arguments` parameter is provided, `load_skill` SHALL substitute placeholders in the skill's instructions and reference files. Supported placeholders: `$1`, `$2`, ... for positional arguments; `$@` for all arguments joined; `$ARGUMENTS` as an alias for `$@`.

#### Scenario: Positional argument substitution
- **WHEN** `load_skill(ctx, skill_name="my-skill", arguments="alpha beta")` is called
- **AND** the skill's instructions contain `$1` and `$2`
- **THEN** `$1` SHALL be replaced with "alpha"
- **AND** `$2` SHALL be replaced with "beta"

#### Scenario: All arguments substitution
- **WHEN** `load_skill(ctx, skill_name="my-skill", arguments="alpha beta")` is called
- **AND** the skill's instructions contain `$@` or `$ARGUMENTS`
- **THEN** both SHALL be replaced with "alpha beta"

#### Scenario: No arguments provided
- **WHEN** `load_skill(ctx, skill_name="my-skill")` is called without `arguments`
- **AND** the skill's instructions contain `$1`
- **THEN** `$1` SHALL remain as-is (no substitution)

### Requirement: load_skill SHALL load reference files

When a skill has reference files (in its `references/` directory or declared in frontmatter), `load_skill` SHALL load the reference content when the skill is activated. Reference file paths SHALL be validated against path traversal attacks.

#### Scenario: Load skill with references
- **WHEN** `load_skill(ctx, skill_name="my-skill")` is called
- **AND** "my-skill" has a `references/guide.md` file
- **THEN** the reference content SHALL be included in the returned string

#### Scenario: Path traversal in reference path is blocked
- **WHEN** a skill's reference path contains `..` or encoded path traversal sequences
- **THEN** a `SecurityError` SHALL be raised
- **AND** no file outside the skill's directory SHALL be read

### Requirement: list_skills SHALL return all visible skills

The `list_skills` tool SHALL return a list of all skills visible to the current agent, including local filesystem skills and remote MCP-provided skills. Each entry SHALL include the skill name, description, and source (local or remote provider name).

#### Scenario: List skills from multiple sources
- **WHEN** `list_skills(ctx)` is called
- **AND** the agent has 3 local skills and 2 remote MCP skills
- **THEN** all 5 skills SHALL be returned
- **AND** each entry SHALL include `name`, `description`, and `source`

### Requirement: load_skill and list_skills SHALL be available to all agents

The `load_skill` and `list_skills` tools SHALL be injected into every agent's toolset, regardless of the agent creation path (SessionPool, AgentFactory, standalone `Agent.from_config()`, or child session). The injection SHALL occur via `_inject_pool_providers()` in addition to the existing SessionPool and AgentFactory paths.

#### Scenario: Standalone agent has load_skill
- **WHEN** an agent is created via `Agent.from_config()` without SessionPool
- **THEN** `load_skill` and `list_skills` SHALL be present in the agent's tool list

#### Scenario: Child session agent has load_skill
- **WHEN** a child session agent is created via `_inject_pool_providers()`
- **THEN** `load_skill` and `list_skills` SHALL be present in the agent's tool list
