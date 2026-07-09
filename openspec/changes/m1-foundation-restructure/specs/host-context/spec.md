## ADDED Requirements

### Requirement: HostContext is a frozen dataclass carrying infrastructure handles

HostContext SHALL be a `@dataclass(frozen=True)` that carries typed references to shared infrastructure objects. It SHALL be immutable — fields cannot be modified after construction. It SHALL be the sole mechanism for passing infrastructure dependencies to agents and factories.

#### Scenario: Construct HostContext from AgentPool

- **WHEN** AgentPool starts and initializes its infrastructure (MCP servers, storage, skills, etc.)
- **THEN** AgentPool SHALL construct a HostContext dataclass containing references to all initialized infrastructure objects
- **AND** the HostContext SHALL include: mcp, storage, skills_registry, capability_cache, prompt_manager, model_registry, model_cache, config_id, tenant_id

#### Scenario: HostContext immutability

- **WHEN** code attempts to modify a field on a HostContext instance after construction
- **THEN** a FrozenInstanceError SHALL be raised
- **AND** the original HostContext instance remains unchanged

### Requirement: HostContext fields are typed

HostContext SHALL use concrete types for all fields, not `Any`. Each field SHALL correspond to an existing infrastructure class.

#### Scenario: Type checking HostContext

- **WHEN** mypy --strict is run on the HostContext definition
- **THEN** no type errors SHALL be reported for HostContext field declarations
- **AND** each field type SHALL match the actual infrastructure class (e.g., `mcp: MCPManager`, not `mcp: Any`)

### Requirement: HostContext carries config_id and tenant_id

HostContext SHALL include `config_id: str` and `tenant_id: str` fields. These default to `"default"` when not explicitly provided. They enable RunScope routing in future milestones.

#### Scenario: Default config_id and tenant_id

- **WHEN** HostContext is constructed without explicit config_id or tenant_id
- **THEN** config_id SHALL be `"default"` and tenant_id SHALL be `"default"`

### Requirement: MessageNode.agent_pool returns HostContext-compatible object

During M1, `MessageNode.agent_pool` property SHALL remain accessible but SHALL return an object that is compatible with HostContext interface. This is a compatibility shim — full call-site migration to HostContext is M1b.

#### Scenario: agent_pool property still works

- **WHEN** agent code accesses `self.agent_pool.storage`
- **THEN** the storage manager SHALL be returned, same as before M1
- **AND** no deprecation warning SHALL be emitted in M1 (warnings added in M1b)

#### Scenario: HostContext extraction from AgentPool

- **WHEN** `pool.get_context()` is called on an AgentPool instance
- **THEN** a HostContext dataclass SHALL be returned containing all infrastructure handles
- **AND** the returned HostContext SHALL be a new frozen instance, not a mutable reference to the pool
