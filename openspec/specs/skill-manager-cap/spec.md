## ADDED Requirements

### Requirement: SkillManagerCap SHALL extend CombinedToolsetCapability

`SkillManagerCap` SHALL inherit from `CombinedToolsetCapability` to reuse `get_toolset()` (merge children), `on_change()` (merge streams), and `__aenter__`/`__aexit__` (lifecycle). It SHALL additionally implement `SkillResource`, `CommandResource`, and `ChangeObservable`.

#### Scenario: SkillManagerCap inherits tool merging
- **WHEN** `get_toolset()` is called on `SkillManagerCap`
- **THEN** it SHALL merge toolsets from all child capabilities (MCP servers declared by skills)
- **AND** tools from local skill Python functions SHALL be included

#### Scenario: SkillManagerCap inherits lifecycle management
- **WHEN** `SkillManagerCap.__aenter__()` is called
- **THEN** all child capabilities SHALL be entered
- **AND** when `__aexit__()` is called, all children SHALL be exited in reverse order

### Requirement: SkillManagerCap SHALL hold local skills as Skill objects directly

`SkillManagerCap` SHALL hold local skills as `dict[str, Skill]` (keyed by skill name), NOT wrapped in individual capability instances. This eliminates one layer of indirection.

#### Scenario: Local skill stored directly
- **WHEN** `SkillManagerCap(local_skills=[skill1, skill2])` is constructed
- **THEN** `self._local_skills` SHALL be `{"skill1": skill1, "skill2": skill2}`
- **AND** no `SkillCapability` instances SHALL be created

### Requirement: SkillManagerCap SHALL aggregate SkillResource from local and remote sources

`list_skills()` SHALL return skills from both local filesystem (`self._local_skills`) and remote MCP servers (child `McpServerCap` instances implementing `SkillResource`). `read_skill(uri)` SHALL route by URI: `skill://local/` to local skills, `skill://<mcp-server>/` to the corresponding `McpServerCap`.

#### Scenario: List skills from both local and remote
- **WHEN** `list_skills()` is called
- **AND** `SkillManagerCap` has 3 local skills and 2 child `McpServerCap` instances with skills
- **THEN** all 5 skills (3 local + 2 remote) SHALL be returned in a single list

#### Scenario: Read local skill
- **WHEN** `read_skill("skill://ponytail/SKILL.md")` is called
- **AND** "ponytail" is in `self._local_skills`
- **THEN** the skill content SHALL be read from the local filesystem

#### Scenario: Read remote skill from MCP
- **WHEN** `read_skill("skill://github-mcp/code-review")` is called
- **AND** "github-mcp" is a child `McpServerCap`
- **THEN** `McpServerCap.read_skill("skill://github-mcp/code-review")` SHALL be called

### Requirement: SkillManagerCap SHALL aggregate CommandResource

`list_commands()` SHALL return commands from local skills (each skill becomes a `CommandEntry` with `name=skill.name`, `description=skill.description`) and from child `McpServerCap` instances implementing `CommandResource`. `get_command(name, args)` SHALL route by name: local skills first, then remote.

#### Scenario: List commands from both local and remote
- **WHEN** `list_commands()` is called
- **AND** `SkillManagerCap` has 3 local skills and 1 child `McpServerCap` with 2 MCP prompts
- **THEN** 5 `CommandEntry` objects SHALL be returned (3 local + 2 remote)

#### Scenario: Local command takes precedence over remote
- **WHEN** `get_command("ponytail", [])` is called
- **AND** "ponytail" exists both as a local skill and as an MCP prompt
- **THEN** the local skill's content SHALL be returned
- **AND** the MCP prompt SHALL NOT be called

### Requirement: SkillManagerCap SHALL provide metadata-only instructions by default

`get_instructions()` SHALL return an `<available-skills>` XML block containing skill names and descriptions (~100 tokens per skill), NOT full skill instructions. This implements progressive disclosure: metadata at compilation, full instructions on demand.

#### Scenario: Metadata-only instructions
- **WHEN** `get_instructions()` is called
- **THEN** an XML block SHALL be returned with format `<available-skills><skill name="..." description="..."/>...</available-skills>`
- **AND** each skill SHALL contribute approximately 100 tokens or fewer

### Requirement: SkillManagerCap SHALL support optional matcher_fn for dynamic skill injection

When `matcher_fn` is provided, `SkillManagerCap` SHALL implement `before_model_request()` to call `matcher_fn(context)` and inject full instructions for matched skills only (typically 2-3). When `matcher_fn` is `None`, all skills' full instructions SHALL be injected (backward compatibility with existing `SkillCapability` behavior).

#### Scenario: Dynamic skill injection with matcher
- **WHEN** `matcher_fn` is provided
- **AND** `before_model_request(context)` is called
- **THEN** `matcher_fn(context)` SHALL be called to select relevant skills
- **AND** only matched skills' full instructions SHALL be injected into the prompt

#### Scenario: All skills injected without matcher (backward compat)
- **WHEN** `matcher_fn` is `None`
- **AND** `before_model_request(context)` is called
- **THEN** all visible skills' full instructions SHALL be injected

### Requirement: SkillManagerCap SHALL support per-skill always_active flag

Skills with `always_active: true` in their config SHALL bypass the `matcher_fn` and always have their full instructions injected, regardless of the matcher's selection.

#### Scenario: Always-active skill bypasses matcher
- **WHEN** `matcher_fn` selects only skill "A"
- **AND** skill "B" has `always_active: true`
- **THEN** both skill "A" and skill "B" instructions SHALL be injected

### Requirement: SkillManagerCap SHALL query child McpServerCap instances for remote skills

`SkillManagerCap` SHALL query child `McpServerCap` instances (via `self._children`) for `SkillResource` and `CommandResource` methods. This is an internal interface — child `McpServerCap` instances are NOT registered in `ExtensionRegistry` individually. The registry only sees `SkillManagerCap` as the single `SkillResource` + `CommandResource`.

#### Scenario: ExtensionRegistry sees only SkillManagerCap
- **WHEN** `ExtensionRegistry.get_skill_resources(scope)` is called
- **AND** the scope has a `SkillManagerCap` with 3 child `McpServerCap` instances
- **THEN** only `SkillManagerCap` SHALL be returned
- **AND** the 3 `McpServerCap` instances SHALL NOT be returned individually
