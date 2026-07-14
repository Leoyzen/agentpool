## MODIFIED Requirements

### Requirement: RunScope.tenant_id validated at every layer boundary

RunScope.tenant_id SHALL be validated at every layer boundary: ProtocolServer → HostRegistry, HostRegistry → AgentHost, AgentHost → AgentFactory, and AgentFactory → Agent. Each boundary SHALL verify that the incoming RunScope.tenant_id matches the expected tenant_id for the target context. Mismatches SHALL raise `TenantMismatchError` and reject the request.

#### Scenario: ProtocolServer validates tenant_id against auth token

- **WHEN** a protocol `initialize` request arrives with an auth token that resolves to tenant_id="tenant-1"
- **AND** the request specifies config_id="default"
- **THEN** ProtocolServer SHALL construct a RunScope with tenant_id="tenant-1"
- **AND** the RunScope SHALL be passed to HostRegistry for Host lookup

#### Scenario: HostRegistry validates RunScope.tenant_id matches Host

- **WHEN** HostRegistry receives a RunScope with tenant_id="tenant-2"
- **AND** the resolved Host has tenant_id="tenant-1"
- **THEN** HostRegistry SHALL raise `TenantMismatchError`
- **AND** the request SHALL NOT proceed to the Host

#### Scenario: AgentHost validates RunScope.tenant_id before agent execution

- **WHEN** an AgentHost receives a RunScope with tenant_id that does not match its own tenant_id
- **THEN** the AgentHost SHALL raise `TenantMismatchError`
- **AND** no agent execution SHALL occur

#### Scenario: Forged tenant_id rejected at AgentFactory boundary

- **WHEN** AgentFactory receives a RunScope with tenant_id="tenant-2" but is operating within tenant-1's Host
- **THEN** AgentFactory SHALL raise `TenantMismatchError`
- **AND** no agent compilation or execution SHALL occur

### Requirement: RunScope.tenant_id extracted from auth token at protocol boundary

ProtocolServer SHALL extract tenant_id from the authentication token during `initialize`. API keys SHALL map to tenant_id via a configurable lookup table. JWT claims SHALL include a `tenant_id` field. OAuth tokens SHALL resolve tenant_id via the provider's userinfo endpoint. When no auth token is provided, tenant_id SHALL default to `"default"`.

#### Scenario: API key maps to tenant_id

- **WHEN** a request arrives with API key `sk-tenant-1-abc123`
- **AND** the API key lookup table maps `sk-tenant-1-abc123` to tenant_id="tenant-1"
- **THEN** ProtocolServer SHALL set RunScope.tenant_id="tenant-1"

#### Scenario: JWT claim provides tenant_id

- **WHEN** a request arrives with a JWT containing `{"tenant_id": "tenant-2"}`
- **AND** the JWT signature is valid
- **THEN** ProtocolServer SHALL set RunScope.tenant_id="tenant-2"

#### Scenario: No auth token defaults to "default" tenant

- **WHEN** a request arrives with no authentication token
- **THEN** ProtocolServer SHALL set RunScope.tenant_id="default"
- **AND** the request SHALL proceed with single-tenant behavior

### Requirement: RunScope propagates tenant_id through all layers

RunScope SHALL carry tenant_id from the protocol boundary through every layer. No layer SHALL modify tenant_id after it is set at the protocol boundary. Background jobs and hooks SHALL construct RunScope with the correct tenant_id before executing.

#### Scenario: Background job constructs RunScope with tenant_id

- **WHEN** a background job is scheduled for tenant-1's session
- **THEN** the job SHALL construct a RunScope with tenant_id="tenant-1"
- **AND** the RunScope SHALL be validated at each layer boundary during execution

#### Scenario: Hook execution preserves tenant scope

- **WHEN** a pre_turn hook fires for tenant-1's agent
- **THEN** the hook SHALL execute within tenant-1's RunScope
- **AND** the hook SHALL NOT have access to any other tenant's infrastructure
