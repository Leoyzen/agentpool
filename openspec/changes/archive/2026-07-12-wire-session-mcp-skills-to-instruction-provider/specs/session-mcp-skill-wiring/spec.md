## ADDED Requirements

### Requirement: AgentPool supports dynamic skill provider registration
The `AgentPool` SHALL expose `register_skill_provider(provider)` and `unregister_skill_provider(provider)` methods that dynamically add or remove `ResourceProvider` instances to the pool's skill aggregator and URI resolver after `_setup_skills_provider()` has completed.

#### Scenario: Register a new skill provider after pool initialization
- **WHEN** `pool.register_skill_provider(provider)` is called with a `ResourceProvider` that has skills
- **THEN** `pool._skill_provider.get_skills()` SHALL include skills from the newly registered provider
- **AND** `pool._skill_resolver.resolve("skill-name")` SHALL find skills from the newly registered provider

#### Scenario: Unregister a skill provider
- **WHEN** `pool.unregister_skill_provider(provider)` is called
- **THEN** `pool._skill_provider.get_skills()` SHALL NOT include skills from that provider
- **AND** `pool._skill_resolver.resolve("skill-name")` SHALL NOT find skills from that provider

### Requirement: AggregatingResourceProvider supports dynamic provider addition and removal
`AggregatingResourceProvider` SHALL expose `add_provider(provider)` and `remove_provider(provider)` methods that mutate the internal provider list and emit the `skills_changed` signal.

#### Scenario: Add a provider to aggregator
- **WHEN** `aggregator.add_provider(provider)` is called
- **THEN** subsequent calls to `aggregator.get_skills()` SHALL include skills from the added provider
- **AND** the `skills_changed` signal SHALL be emitted

#### Scenario: Remove a provider from aggregator
- **WHEN** `aggregator.remove_provider(provider)` is called
- **THEN** subsequent calls to `aggregator.get_skills()` SHALL NOT include skills from the removed provider
- **AND** the `skills_changed` signal SHALL be emitted

### Requirement: SkillURIResolver supports dynamic provider registration
`SkillURIResolver` SHALL expose `register_provider(name, provider)` and `unregister_provider(name)` methods to add or remove providers after initialization.

#### Scenario: Register a provider to resolver after initialization
- **WHEN** `resolver.register_provider("new_provider", provider)` is called
- **THEN** `resolver.resolve("skill-name")` SHALL search the newly registered provider for matching skills

#### Scenario: Unregister a provider from resolver
- **WHEN** `resolver.unregister_provider("provider_name")` is called
- **THEN** `resolver.resolve("skill-name")` SHALL NOT search the unregistered provider for matching skills

### Requirement: ACPSession registers session-level MCP providers with the pool
`ACPSession.initialize_mcp_servers()` SHALL register each created `MCPResourceProvider` with the pool's skill aggregator via `pool.register_skill_provider()`.

#### Scenario: ACP session creates MCP providers that appear in skill listing
- **WHEN** an ACP client connects and sends `mcp_servers` containing a server that publishes `skill://` resources
- **THEN** `SkillsInstructionProvider._generate_skills_instruction()` SHALL include those skills in the `<available-skills>` XML
- **AND** `load_skill("skill-name")` SHALL resolve skills from the session-level MCP provider

### Requirement: Session teardown cleans up skill provider registration
When an `ACPSession` is closed or torn down, all session-level MCP providers SHALL be unregistered from the pool's skill aggregator.

#### Scenario: Session close unregisters providers
- **WHEN** an `ACPSession` that registered MCP providers is closed
- **THEN** `pool._skill_provider.get_skills()` SHALL NOT include skills from the closed session's providers
- **AND** `pool._skill_resolver` SHALL NOT resolve skills from the closed session's providers
