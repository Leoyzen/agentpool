## ADDED Requirements

### Requirement: ExtensionRegistry SHALL be populated with SkillManagerCap during pool initialization

During `AgentPool.__aenter__()`, after `_rebuild_skill_capabilities()` creates a `SkillManagerCap`, the pool SHALL register it with `ExtensionRegistry` at `ScopeLevel.POOL`. This ensures `resolve_uri()` can discover the `SkillManagerCap` for `skill://{name}` URI resolution across all sessions. Flat URIs (`skill://{name}` without provider segment) SHALL be resolved by iterating all visible `SkillResource` capabilities without name filtering.

#### Scenario: SkillManagerCap registered during pool startup
- **WHEN** `AgentPool.__aenter__()` completes
- **THEN** `ExtensionRegistry.get_skill_resources(scope)` SHALL return the `SkillManagerCap` for any scope
- **AND** `ExtensionRegistry.resolve_uri("skill://my-skill", scope)` SHALL find the skill via the `SkillManagerCap` without provider name matching

#### Scenario: SkillManagerCap re-registered after rebuild
- **WHEN** `_rebuild_skill_capabilities()` is called again (e.g., due to skill filesystem changes)
- **THEN** the old `SkillManagerCap` SHALL be unregistered from `ExtensionRegistry`
- **AND** the new `SkillManagerCap` SHALL be registered at `ScopeLevel.POOL`
