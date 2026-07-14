## MODIFIED Requirements

### Requirement: HostContext config_id is populated from RunScope

HostContext's `config_id` field SHALL be populated from the `RunScope.config_id` value when the HostContext is created through HostRegistry. The `config_id` SHALL NOT be hardcoded to `"default"` when a RunScope is available. The default value of `"default"` SHALL only be used when no RunScope is provided (e.g., single-config backward compatibility mode).

#### Scenario: HostContext created with RunScope

- **WHEN** HostRegistry creates a Host for RunScope `(config_id="config-a", tenant_id="tenant-1")`
- **THEN** the HostContext's `config_id` SHALL be `"config-a"`
- **AND** the HostContext's `tenant_id` SHALL be `"tenant-1"`

#### Scenario: HostContext defaults in single-config mode

- **WHEN** AgentPool is constructed via `async with AgentPool("config.yml")` (no RunScope)
- **THEN** the HostContext's `config_id` SHALL be `"default"`
- **AND** the HostContext's `tenant_id` SHALL be `"default"`
- **AND** all existing behavior SHALL be preserved

### Requirement: HostContext includes ModelCache reference

HostContext SHALL include a `model_cache: ModelCache` field that provides shared pydantic-ai `Model` instances. The ModelCache SHALL be scoped to the Host that owns the HostContext. AgentFactory SHALL use this ModelCache for model instance creation during compile.

#### Scenario: ModelCache is accessible from HostContext

- **WHEN** AgentFactory accesses `host_context.model_cache` during compile
- **THEN** a `ModelCache` instance SHALL be returned
- **AND** the cache SHALL contain any previously created `Model` instances for this Host

#### Scenario: ModelCache is scoped per-Host

- **WHEN** two Hosts are created for different `(config_id, tenant_id)` pairs
- **THEN** each Host's HostContext SHALL have a distinct `ModelCache` instance
- **AND** `Model` instances SHALL NOT be shared across Hosts

### Requirement: HostContext is reconstructed on HostConfig reload

When a HostConfig change triggers `Host.reload()`, a new HostContext SHALL be constructed with updated infrastructure handles. The new HostContext SHALL replace the previous one. Agents created after the reload SHALL use the new HostContext.

#### Scenario: HostConfig reload creates new HostContext

- **WHEN** a HostConfig change triggers `host.reload()` and infrastructure is reinitialized
- **THEN** a new HostContext SHALL be constructed with the updated infrastructure handles
- **AND** the `config_id` and `tenant_id` SHALL be preserved from the previous HostContext
- **AND** agents created after reload SHALL receive the new HostContext

#### Scenario: HostConfig reload does not affect active turns

- **WHEN** a HostConfig change triggers `host.reload()` while a turn is in progress
- **THEN** the active turn SHALL continue using the HostContext it was started with (snapshot)
- **AND** the new HostContext SHALL only be used for turns started after the reload completes
