## MODIFIED Requirements

### Requirement: HostContext is a frozen dataclass carrying infrastructure handles

HostContext SHALL be a `@dataclass(frozen=True)` that carries typed references to shared infrastructure objects. It SHALL be immutable — fields cannot be modified after construction. It SHALL be the sole mechanism for passing infrastructure dependencies to agents and factories. HostContext SHALL include `main_agent_name: str | None = None`. The `pool: AgentPool | None = None` field SHALL remain as a temporary escape hatch for skill-related accesses (to be removed in the skill-service-extraction change).

#### Scenario: Construct HostContext from AgentPool

- **WHEN** AgentPool starts and initializes its infrastructure (MCP servers, storage, skills, etc.)
- **THEN** AgentPool SHALL construct a HostContext dataclass containing references to all initialized infrastructure objects
- **AND** the HostContext SHALL include: mcp, storage, skills_registry, capability_cache, prompt_manager, model_registry, model_cache, config_id, tenant_id, main_agent_name, pool

#### Scenario: HostContext immutability

- **WHEN** code attempts to modify a field on a HostContext instance after construction
- **THEN** a FrozenInstanceError SHALL be raised
- **AND** the original HostContext instance remains unchanged

#### Scenario: HostContext carries main_agent_name

- **WHEN** HostContext is constructed with `main_agent_name="primary"`
- **THEN** `ctx.main_agent_name` SHALL return `"primary"`

#### Scenario: HostContext main_agent_name defaults to None

- **WHEN** HostContext is constructed without explicit `main_agent_name`
- **THEN** `ctx.main_agent_name` SHALL be `None`

## REMOVED Requirements

### Requirement: MessageNode.agent_pool returns HostContext-compatible object

**Reason**: The `agent_pool` property migration is complete. All source code now uses `host_context` directly. The property remains as a deprecated shim but no code should depend on it for HostContext access.
**Migration**: Use `node.host_context` instead of `node.agent_pool`. For internal wiring, use `node._bind_pool(pool)`.
