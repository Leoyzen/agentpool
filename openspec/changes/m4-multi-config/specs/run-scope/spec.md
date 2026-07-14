## ADDED Requirements

### Requirement: RunScope is a frozen dataclass carrying routing context

RunScope SHALL be a `@dataclass(frozen=True)` containing `config_id: str`, `tenant_id: str`, `user_id: str`, and `session_id: str`. It SHALL be immutable — fields cannot be modified after construction. It SHALL be the sole mechanism for carrying routing context across layer boundaries.

#### Scenario: Construct RunScope from protocol request

- **WHEN** a protocol server receives an `initialize` request with config and tenant headers
- **THEN** a RunScope SHALL be constructed with `config_id`, `tenant_id`, `user_id`, and `session_id` extracted from the request
- **AND** the RunScope SHALL be immutable for the lifetime of the request

#### Scenario: RunScope immutability

- **WHEN** code attempts to modify a field on a RunScope instance after construction
- **THEN** a `FrozenInstanceError` SHALL be raised
- **AND** the original RunScope instance remains unchanged

### Requirement: RunScope is extracted at protocol initialize

Protocol servers SHALL extract RunScope during the `initialize` handshake (or equivalent entry point). The extracted RunScope SHALL be stored in the session context and threaded through all subsequent operations for that session.

#### Scenario: ACP server extracts RunScope from initialize

- **WHEN** an ACP `initialize` request is received with `config_id` and `tenant_id` in the request metadata
- **THEN** the ACP server SHALL construct a RunScope from the metadata
- **AND** the RunScope SHALL be stored in the session for all subsequent requests in that session

#### Scenario: RunScope defaults when not provided

- **WHEN** a protocol `initialize` request does not include `config_id` or `tenant_id`
- **THEN** `config_id` SHALL default to `"default"` and `tenant_id` SHALL default to `"default"`
- **AND** `user_id` SHALL default to `"anonymous"` and `session_id` SHALL be auto-generated

### Requirement: RunScope is validated at layer boundaries

Every layer boundary (protocol → HostRegistry, HostRegistry → AgentHost, AgentHost → AgentFactory) SHALL validate the RunScope before proceeding. Validation SHALL check that `config_id` is registered in ConfigRegistry and that `tenant_id` is non-empty.

#### Scenario: Valid RunScope passes validation

- **WHEN** a RunScope with `config_id="config-a"` (registered) and `tenant_id="tenant-1"` is validated
- **THEN** validation SHALL pass and the operation SHALL proceed

#### Scenario: Invalid config_id is rejected

- **WHEN** a RunScope with `config_id="nonexistent"` is validated at a layer boundary
- **THEN** a `ConfigNotFoundError` SHALL be raised
- **AND** the error message SHALL include the invalid `config_id`

### Requirement: RunScope routes to correct Host and Factory

RunScope SHALL be used by HostRegistry to route to the correct AgentHost. The `config_id` and `tenant_id` fields form the Host lookup key. AgentFactory SHALL use `config_id` to resolve model aliases and provider configuration.

#### Scenario: RunScope routes to correct Host

- **WHEN** a request with RunScope `(config_id="config-a", tenant_id="tenant-1")` arrives
- **THEN** HostRegistry SHALL look up or create the Host for `("config-a", "tenant-1")`
- **AND** the request SHALL be processed by that Host's AgentFactory and agents

#### Scenario: Different RunScopes route to different Hosts

- **WHEN** two concurrent requests have RunScopes `(config_id="config-a", tenant_id="tenant-1")` and `(config_id="config-b", tenant_id="tenant-2")`
- **THEN** each request SHALL be routed to its respective Host
- **AND** the requests SHALL be processed independently without cross-contamination
