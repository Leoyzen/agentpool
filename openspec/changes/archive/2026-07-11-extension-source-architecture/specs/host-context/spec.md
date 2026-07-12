## ADDED Requirements

### Requirement: HostContext SHALL carry ExtensionRegistry reference for session-scoped capabilities

`HostContext` SHALL include an `extension_registry: ExtensionRegistry | None = None` field for session-scoped capability access. Pool-level capabilities SHALL be registered on `AgentPool`'s registry instance. Session/agent/turn-level capabilities SHALL be registered on the `HostContext`'s registry instance.

#### Scenario: HostContext carries session-scoped registry
- **WHEN** `HostContext` is constructed for a protocol server session
- **THEN** `ctx.extension_registry` SHALL reference the session's `ExtensionRegistry` instance
- **AND** session-scoped capabilities SHALL be queryable via `ctx.extension_registry.get_visible_capabilities(scope)`

#### Scenario: HostContext without ExtensionRegistry (standalone execution)
- **WHEN** `HostContext` is constructed for standalone execution (no protocol server)
- **THEN** `ctx.extension_registry` SHALL be `None`
- **AND** capability discovery SHALL fall back to pool-level registry
