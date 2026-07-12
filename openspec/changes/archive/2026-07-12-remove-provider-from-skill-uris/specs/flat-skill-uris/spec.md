## ADDED Requirements

### Requirement: Skill URIs use flat naming without provider segment
The system SHALL identify skills using URIs of the format `skill://{name}` or bare `{name}`, without a provider segment. The `SkillURIResolver` SHALL resolve skill names by searching all registered providers in priority order, with the first match returned.

#### Scenario: Resolve a flat skill URI
- **WHEN** `SkillURIResolver.resolve("skill://code-review")` is called
- **THEN** the resolver SHALL search all registered providers for a skill named `code-review`
- **AND** return the first matching skill found

#### Scenario: Resolve a bare skill name
- **WHEN** `SkillURIResolver.resolve("code-review")` is called with a bare name (no `skill://` prefix)
- **THEN** the resolver SHALL search all registered providers for a skill named `code-review`
- **AND** return the first matching skill found

#### Scenario: Resolve a flat skill URI with reference path
- **WHEN** `SkillURIResolver.resolve("skill://code-review/references/guide.md")` is called
- **THEN** the resolver SHALL parse `code-review` as the skill name and `references/guide.md` as the reference path
- **AND** search all registered providers for the skill

#### Scenario: Resolve a flat skill URI with multi-segment reference path
- **WHEN** `SkillURIResolver.resolve("skill://code-review/a/b/c/guide.md")` is called
- **THEN** the resolver SHALL parse `code-review` as the skill name and `a/b/c/guide.md` as the reference path

#### Scenario: Resolve a flat skill URI where name is the only path element
- **WHEN** `ResolvedSkillURI.parse("skill://code-review")` is called
- **THEN** the parser SHALL handle the `urlparse` decomposition where `netloc="code-review"` and `path=""` by treating `netloc` as the skill name
- **AND** the result SHALL have `skill_name="code-review"` and `reference_path=None`

#### Scenario: Fuzzy matching works across all providers
- **WHEN** `SkillURIResolver.resolve("code_review")` is called with an underscore name
- **THEN** the resolver SHALL search all providers for exact match `code_review`
- **AND** if no exact match, SHALL generate alternative `code-review` and search all providers again
- **AND** return the first matching skill found

#### Scenario: Skill not found
- **WHEN** `SkillURIResolver.resolve("nonexistent-skill")` is called and no provider has that skill
- **THEN** the resolver SHALL raise `SkillNotFoundError`

### Requirement: Skill disambiguation uses provider priority order
The `SkillURIResolver` SHALL search providers in priority order when resolving skill names. Local skills (`LocalResourceProvider`) SHALL take priority over MCP skills (`MCPResourceProvider`). Within the same provider type, the first registered provider SHALL take priority.

#### Scenario: Local skill overrides MCP skill with same name
- **WHEN** a local provider has `code-review` and an MCP provider also has `code-review`
- **THEN** `SkillURIResolver.resolve("code-review")` SHALL return the local skill

#### Scenario: First MCP provider wins when no local match
- **WHEN** no local provider has `debugging` but two MCP providers both have it
- **THEN** `SkillURIResolver.resolve("debugging")` SHALL return the skill from the first registered MCP provider

#### Scenario: Collision is logged when a skill is shadowed
- **WHEN** a lower-priority provider has a skill that is shadowed by a higher-priority match
- **THEN** the resolver SHALL emit a `logger.debug()` message indicating the collision
- **AND** the higher-priority match SHALL be returned

### Requirement: Skill.safe_uri returns flat URI
The `Skill.safe_uri` property SHALL return `skill://{name}` for local filesystem skills, without a hardcoded `"local"` provider prefix. For virtual (MCP) skills, it SHALL return the skill path URI as-is.

#### Scenario: Local skill safe URI
- **WHEN** a local skill named `python-expert` is loaded from `~/.claude/skills/python-expert/`
- **THEN** `skill.safe_uri` SHALL return `"skill://python-expert"`

#### Scenario: MCP skill safe URI preserves existing path
- **WHEN** an MCP skill has `skill_path=PurePosixPath("skill://systematic-debugging")`
- **THEN** `skill.safe_uri` SHALL return `"skill://systematic-debugging"`

### Requirement: ResolvedSkillURI no longer includes provider field
The `ResolvedSkillURI` dataclass SHALL NOT include a `provider` field. The `parse()` method SHALL treat the first path segment of `skill://` URIs as the skill name, not as a provider.

#### Scenario: Parse skill URI
- **WHEN** `ResolvedSkillURI.parse("skill://code-review")` is called
- **THEN** the result SHALL have `skill_name="code-review"` and `reference_path=None`

#### Scenario: Parse skill URI with reference path
- **WHEN** `ResolvedSkillURI.parse("skill://code-review/references/guide.md")` is called
- **THEN** the result SHALL have `skill_name="code-review"` and `reference_path="references/guide.md"`

### Requirement: MCP resource skills have flat skill paths
When `MCPResourceProvider` discovers resource-based skills, the constructed `Skill` SHALL have `skill_path=PurePosixPath(f"skill://{skill_name}")` without the MCP server name prefix.

#### Scenario: MCP resource skill construction
- **WHEN** an MCP server exposes a `skill://systematic-debugging/SKILL.md` resource
- **THEN** the created `Skill` SHALL have `skill_path=PurePosixPath("skill://systematic-debugging")`

### Requirement: SkillCommand.resolved_skill_uri returns flat URI
The `SkillCommand.resolved_skill_uri` property SHALL return `skill://{name}` without a hardcoded `"local"` fallback.

#### Scenario: SkillCommand URI generation
- **WHEN** a `SkillCommand` is created for skill `code-review` without an explicit `skill_uri`
- **THEN** `command.resolved_skill_uri` SHALL return `"skill://code-review"`

### Requirement: Command registry does not use provider metadata for URIs
The `CommandRegistry._sync_from_skill_provider()` method SHALL NOT read `skill.metadata["provider"]` to construct skill URIs.

#### Scenario: Command registry sync without provider
- **WHEN** `CommandRegistry._sync_from_skill_provider()` syncs skills from the aggregating provider
- **THEN** each `SkillCommand` SHALL have a skill URI derived from the skill name alone, without provider information

## REMOVED Requirements

### Requirement: Provider name validation in URI resolver
**Reason**: Provider name validation for URI parsing (`PROVIDER_NAME_PATTERN`, `MAX_PROVIDER_NAME_LENGTH`) is no longer needed for URI format validation since provider is removed from URIs. However, the validation functions (`_validate_provider_name()`, `_is_valid_provider_name()`) and their constants are retained for dict key safety in `register_provider()` and `unregister_provider()`.
**Migration**: No code removal needed. The validation infrastructure stays as-is for `register_provider()` dict key safety. Remove only the `provider` field from `ResolvedSkillURI`.

### Requirement: Provider-based routing in SkillURIResolver
**Reason**: The resolver no longer routes by provider name. All resolution searches all providers in priority order.
**Migration**: Remove the provider-lookup branch in `resolve()`. The existing provider-less search (lines 347-362) becomes the sole resolution path.

### Requirement: Provider metadata on MCP skills
**Reason**: The `"provider"` key in `skill.metadata` is no longer needed for URI construction.
**Migration**: Remove `"provider": self.name` from skill metadata in `mcp_provider.py` (lines 487, 569).
