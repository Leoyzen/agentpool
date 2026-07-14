## ADDED Requirements

### Requirement: Model configuration uses three layers

Model configuration SHALL be organized in three layers: (1) providers — infrastructure-level provider settings (API keys, base URLs, timeouts), (2) aliases — named model references that map short names to concrete model strings or fallback chains, (3) per-agent selection — individual agent model and parameter settings. Each layer builds on the previous: per-agent selection references aliases, aliases reference providers.

#### Scenario: Three-layer model config in YAML

- **WHEN** a config defines `host.models.providers`, `host.models.aliases`, and `agents.coder.model`
- **THEN** the provider config SHALL be parsed as infrastructure-level settings
- **AND** aliases SHALL be parsed as named references to provider-backed models
- **AND** the agent's `model` field SHALL resolve through aliases to a concrete provider-backed model

#### Scenario: Flat model string bypasses aliases

- **WHEN** an agent's `model` field is set to `"openai:gpt-4o"` (a direct model string, not an alias)
- **THEN** the model SHALL be resolved directly without alias lookup
- **AND** provider settings SHALL still be applied from the providers layer

### Requirement: Provider config holds infrastructure-level settings

Provider config SHALL define API keys, base URLs, timeouts, and other connection-level settings for each model provider (openai, anthropic, google, etc.). Provider config SHALL be shared across all agents within a Host. Provider config SHALL be part of HostConfig, not AgentManifest.

#### Scenario: Provider config is shared across agents

- **WHEN** two agents in the same Host both use `openai:` models
- **THEN** both agents SHALL use the same provider configuration (API key, base URL, timeout)
- **AND** a single HTTP client pool SHALL be shared via ModelCache

#### Scenario: Provider config is infrastructure-level

- **WHEN** a config change modifies only provider settings (e.g., API key rotation)
- **THEN** the change SHALL be classified as a HostConfig change
- **AND** HostRegistry SHALL trigger a full Host reload, not just agent recompile

### Requirement: Model aliases map short names to concrete models or fallback chains

Model aliases SHALL map a short name (e.g., `"smart"`) to either a concrete model string (e.g., `"openai:gpt-4o"`) or a fallback chain (e.g., `["openai:gpt-4o", "anthropic:claude-sonnet-4-0"]`). Aliases SHALL be resolvable at compile time. An alias referencing another alias SHALL be resolved transitively (with cycle detection).

#### Scenario: Simple alias resolution

- **WHEN** an agent's `model` is set to `"smart"` and alias `"smart"` maps to `"openai:gpt-4o"`
- **THEN** the agent SHALL use `openai:gpt-4o` as its model
- **AND** the resolved model string SHALL be passed to pydantic-ai for Model instantiation

#### Scenario: Fallback chain alias

- **WHEN** an agent's `model` is set to `"resilient"` and alias `"resilient"` maps to a fallback chain `["openai:gpt-4o", "anthropic:claude-sonnet-4-0"]`
- **THEN** the agent SHALL use a fallback model that tries each provider in order
- **AND** if the primary model fails, the next model in the chain SHALL be used

#### Scenario: Transitive alias resolution with cycle detection

- **WHEN** alias `"a"` maps to `"b"` and alias `"b"` maps to `"a"` (circular reference)
- **THEN** a `ModelAliasCycleError` SHALL be raised during resolution
- **AND** the error message SHALL include the cycle path

### Requirement: ModelCache shares Model instances across agents

ModelCache SHALL cache pydantic-ai `Model` instances keyed by resolved model string. When multiple agents use the same model string (after alias resolution), they SHALL share the same `Model` instance. ModelCache SHALL be scoped per-Host (not global).

#### Scenario: Shared Model instance

- **WHEN** two agents in the same Host both resolve to `"openai:gpt-4o"`
- **THEN** both agents SHALL receive the same `Model` instance from ModelCache
- **AND** only one HTTP client pool SHALL be created for that model

#### Scenario: Different models get different instances

- **WHEN** one agent resolves to `"openai:gpt-4o"` and another resolves to `"anthropic:claude-sonnet-4-0"`
- **THEN** two distinct `Model` instances SHALL be created and cached
- **AND** each SHALL have its own HTTP client pool

#### Scenario: ModelCache is per-Host scoped

- **WHEN** two different Hosts both resolve to `"openai:gpt-4o"`
- **THEN** each Host SHALL have its own ModelCache with its own `Model` instance
- **AND** the Model instances SHALL NOT be shared across Hosts

### Requirement: Model alias resolution may differ per tenant

Since provider config is part of HostConfig and Hosts are per-tenant, model aliases SHALL resolve within the Host's provider context. The same alias name MAY resolve to different concrete models for different tenants if their Hosts have different provider configs.

#### Scenario: Same alias, different tenants, different models

- **WHEN** tenant-1's Host has alias `"smart"` → `"openai:gpt-4o"` and tenant-2's Host has alias `"smart"` → `"anthropic:claude-sonnet-4-0"`
- **THEN** an agent using `"smart"` in tenant-1's Host SHALL use `openai:gpt-4o`
- **AND** the same agent config in tenant-2's Host SHALL use `anthropic:claude-sonnet-4-0`

### Requirement: Fallback model composition works with aliases

When an alias resolves to a fallback chain, the fallback model SHALL be composed using the provider configs from the Host. Each model in the fallback chain SHALL be resolved through ModelCache independently.

#### Scenario: Fallback chain with provider config

- **WHEN** alias `"resilient"` maps to `["openai:gpt-4o", "anthropic:claude-sonnet-4-0"]` and the Host has provider configs for both openai and anthropic
- **THEN** a fallback Model SHALL be created with both providers configured
- **AND** each model in the chain SHALL be cached independently in ModelCache
